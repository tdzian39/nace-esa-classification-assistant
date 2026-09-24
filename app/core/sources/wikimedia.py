"""Wikidata and Wikipedia: a LEI -> what the issuer does, in a sentence someone wrote down.

The classifier needs an activity description ("popis činnosti"), and until now it had one only
when MO typed it or a paid search provider was configured - which none is (roadmap Q13). For
the well-known issuers that make up much of the work, Wikipedia already has it, free:

    LEI -> Wikidata item (property P1278 = LEI)
        -> item labels, short descriptions, industries (P452), Wikipedia sitelinks
        -> Wikipedia REST summary of the article (cs first, then en)

The match is on the **identifier** first: a LEI finds exactly one item or none. Only when no
item carries the LEI (or there is no LEI) is the **official name** tried, and then strictly:
``wbsearchentities`` matches labels and aliases, and a hit counts only when the matched text
*is* the name (case, diacritics and punctuation aside), exactly one item matches, and the item
does not carry some other entity's LEI - which is what keeps "BMW Finance N.V." from being
described as BMW, and "Amundi Funds" as the asset manager. A name that matches several items
("Bundesrepublik Deutschland": Germany, West Germany, ...) is left alone. A description of the
wrong issuer is worse than none, so every name match is flagged for the reviewer.

Endpoints, verified live on 2026-09-23 with the repository's User-Agent (Wikimedia refuses one
without contact information - see ``WEB_USER_AGENT``):

* ``GET https://www.wikidata.org/w/api.php?action=query&list=search
  &srsearch=haswbstatement:P1278=<LEI>`` - the items carrying the LEI (Deutsche Bank
  ``7LTWFZYICNSX8D621K86`` -> ``Q66048``; the EIB and BMW Finance N.V. -> none).
* ``action=wbsearchentities&search=<name>&language=en`` - label and alias matches with the
  matched text (``match.text``), 3 KB for 7 hits; ``wbgetclaims&property=P1278`` on the one
  chosen item, to see whose LEI it carries.
* ``action=wbgetentities&ids=<Q>&props=labels|descriptions|sitelinks/urls`` - 0.6 KB. Asking
  for ``claims`` as well would bring the whole item, 443 KB for Deutsche Bank, which is why the
  industries come from:
* ``action=wbgetclaims&entity=<Q>&property=P452`` (4.5 KB), then ``wbgetentities`` with
  ``props=labels`` for the industry items. Their NACE Rev. 2 codes (P4496) are **not** read:
  they sit on the industry items, whose claims are another 250 KB. The labels are usually NACE
  titles anyway ("other monetary intermediation"), which the lexical filter scores.
* ``GET https://{lang}.wikipedia.org/api/rest_v1/page/summary/<title>`` - ``type``
  (``standard``/``disambiguation``), ``extract``, ``content_urls.desktop.page``,
  ``timestamp``; 404 for a missing article.

Fail-soft contract, as in :mod:`core.sources.base`: ``None`` means Wikidata has no item for the
LEI or no unambiguous one for the name (or Wikipedia no article); :class:`~core.sources.base.SourceUnavailableError` means it could
not be asked - including when the lookup deadline leaves no time for another request.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import quote

import httpx

from config.settings import Settings
from core.sources.base import (
    Provenance,
    SourceError,
    SourceResponseError,
    SourceUnavailableError,
)

LOGGER = logging.getLogger(__name__)

WIKIDATA_API: Final[str] = "https://www.wikidata.org/w/api.php"
#: The human-readable item page, the citable form of a Wikidata item.
ITEM_PAGE: Final[str] = "https://www.wikidata.org/wiki/{qid}"
SUMMARY_URL: Final[str] = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

#: Wikidata properties read: the LEI, and the industry.
P_LEI: Final[str] = "P1278"
P_INDUSTRY: Final[str] = "P452"

#: At most this many industries are named; a conglomerate lists a dozen.
MAX_INDUSTRIES: Final[int] = 5
#: Search hits read per name query; more only adds namesakes.
NAME_SEARCH_LIMIT: Final[int] = 7
#: Editions whose labels and aliases are searched, in order.
NAME_SEARCH_LANGUAGES: Final[tuple[str, ...]] = ("en", "cs")

#: Legal-form suffixes stripped for the second, looser name query ("Kommuninvest i Sverige AB"
#: -> "Kommuninvest i Sverige"). Deliberately a list of *legal forms*, not of words.
_LEGAL_FORM_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?:[\s,]+(?:AG|SE|SA|S\.A\.|SpA|S\.p\.A\.|N\.?V\.?|B\.?V\.?|AB|ASA|AS|A/S|Oyj|plc|
    Ltd\.?|Limited|LLC|Inc\.?|Corp\.?|Corporation|GmbH|KGaA|S\.à\s?r\.l\.|Sàrl|S\.A\.S\.|SAS|
    Aktiengesellschaft|Aktiebolag|Aktieselskab|Realkreditaktieselskab))+\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
#: Anything that is not a letter or digit, for name comparison.
_NON_ALNUM_RE: Final[re.Pattern[str]] = re.compile(r"[^0-9a-z]+")
#: Search hits with these words in the description are never issuers: a disambiguation page,
#: a paper, or a name as such ("Generali" is also a family name).
_NEVER_AN_ISSUER: Final[tuple[str, ...]] = (
    "disambiguation",
    "rozcestník",
    "scholarly article",
    "family name",
    "given name",
    "surname",
    "příjmení",
    "rodné jméno",
)

#: A LEI is 20 upper-case alphanumerics (ISO 17442). Anything else never reaches the search
#: syntax, where it could widen the query.
_LEI_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z0-9]{20}")
_QID_RE: Final[re.Pattern[str]] = re.compile(r"Q[1-9][0-9]*")
#: Inline editorial marks in an extract ("[kdy?]", "[citation needed]").
_MARK_RE: Final[re.Pattern[str]] = re.compile(r"\[[^\[\]\n]{1,30}\]")
#: Space, tab and NBSP, written unraw so the NBSP stays a visible escape (as in web.py).
_SPACE_RE: Final[re.Pattern[str]] = re.compile("[ \t\u00a0]+")


@dataclass(frozen=True, slots=True)
class Industry:
    """One P452 value, labelled in Czech and English where Wikidata has the label."""

    qid: str
    label_cs: str | None = None
    label_en: str | None = None

    @property
    def text(self) -> str | None:
        """``"automobilový průmysl (automotive industry)"``, or whichever label exists."""
        if self.label_cs and self.label_en and self.label_cs != self.label_en:
            return f"{self.label_cs} ({self.label_en})"
        return self.label_cs or self.label_en


@dataclass(frozen=True, slots=True)
class Sitelink:
    """A Wikipedia article of the item: ``lang`` is the edition (``cs``), ``title`` the page."""

    lang: str
    title: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class WikidataItem:
    """The Wikidata item linked to a LEI, reduced to what a description needs.

    Attributes:
        qid: The item id (``Q66048``).
        label_cs, label_en: The item's name.
        description_cs, description_en: Wikidata's one-line description
            ("German automobile manufacturer, and conglomerate").
        industries: P452 values, preferred rank first, deprecated ones dropped.
        sitelinks: Wikipedia articles in the configured languages, in their order.
        other_items: How many further items carry the same LEI (normally 0).
        provenance: ``WEB``, detail ``web:wikidata``.
        matched_by: ``"lei"`` (the identifier) or ``"name"`` (an exact label or alias match).
    """

    qid: str
    label_cs: str | None
    label_en: str | None
    description_cs: str | None
    description_en: str | None
    industries: tuple[Industry, ...]
    sitelinks: tuple[Sitelink, ...]
    provenance: Provenance
    other_items: int = 0
    matched_by: str = "lei"

    @property
    def url(self) -> str:
        return ITEM_PAGE.format(qid=self.qid)

    @property
    def label(self) -> str | None:
        return self.label_cs or self.label_en

    def facts(self) -> tuple[str, ...]:
        """Czech lines with the English words in parentheses, like the register fact sheet."""
        lines: list[str] = []
        described = [d for d in (self.description_cs, self.description_en) if d]
        if len(described) == 2 and described[0] != described[1]:
            lines.append(f"Wikidata: {described[0]} ({described[1]}).")
        elif described:
            lines.append(f"Wikidata: {described[0]}.")
        named = [text for text in (industry.text for industry in self.industries) if text]
        if named:
            lines.append("Odvětví podle Wikidat: " + "; ".join(named) + ".")
        return tuple(lines)


@dataclass(frozen=True, slots=True)
class WikipediaSummary:
    """The lead of one Wikipedia article."""

    lang: str
    title: str
    extract: str
    url: str
    provenance: Provenance
    revised_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WikiDescription:
    """What Wikimedia says about one LEI: the item, and the article summary when there is one.

    ``notes`` explain a missing summary (no article, or Wikipedia could not be asked); the item
    alone still gives a line of description and the industries.
    """

    item: WikidataItem
    summary: WikipediaSummary | None = None
    notes: tuple[str, ...] = ()

    @property
    def paragraphs(self) -> tuple[str, ...]:
        """The description to classify: the article's lead, then Wikidata's lines."""
        parts: list[str] = []
        if self.summary is not None:
            parts.append(self.summary.extract)
        facts = " ".join(self.item.facts())
        if facts:
            parts.append(facts)
        return tuple(parts)


class WikimediaSource:
    """Read-only client of the Wikidata API and the Wikipedia REST summaries.

    Args:
        settings: ``WIKIMEDIA_*``, ``WIKIPEDIA_LANGUAGES`` and ``WEB_USER_AGENT``.
        client: Inject an ``httpx.Client`` (the tests pass a ``MockTransport`` one). Requests use
            absolute URLs, because the two APIs live on different hosts.
        sleep, monotonic: Injected so throttling, backoff and the deadline are testable.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    @property
    def languages(self) -> tuple[str, ...]:
        """The Wikipedia editions to try, in order (``("cs", "en")``)."""
        return tuple(
            lang.strip().lower()
            for lang in self._settings.wikipedia_languages.split(",")
            if lang.strip()
        )

    # -- transport ---------------------------------------------------------------------

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._settings.wikimedia_timeout_seconds,
                headers={
                    "Accept": "application/json",
                    "User-Agent": self._settings.web_user_agent,
                },
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        interval = self._settings.wikimedia_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _send(
        self, url: str, params: Mapping[str, str] | None, deadline: float | None
    ) -> httpx.Response:
        """One GET, retried on timeouts, connection errors, 429 and 5xx; never past ``deadline``.

        A request that could still be running at ``deadline`` is not started: the description
        is worth less than the rest of the lookup, which must end inside Vercel's 60 s.
        """
        attempts = max(1, self._settings.wikimedia_max_attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if (
                deadline is not None
                and self._monotonic() + self._settings.wikimedia_timeout_seconds > deadline
            ):
                raise SourceUnavailableError("na dotaz do Wikimedie nezbyl čas")
            self._throttle()
            try:
                response = self._ensure_client().get(url, params=params)
            except httpx.TimeoutException as exc:
                last_error = SourceUnavailableError(f"Wikimedia timed out on {url}: {exc}")
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(f"Wikimedia is unreachable on {url}: {exc}")
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = SourceUnavailableError(
                    f"Wikimedia returned HTTP {response.status_code} for {url}"
                )
            if attempt < attempts:
                self._sleep(max(self._settings.wikimedia_min_interval_seconds, 0.5) * attempt)
        raise (
            last_error
            if last_error is not None
            else SourceUnavailableError(f"Wikimedia could not be reached on {url}")
        )

    def _json(
        self, url: str, params: Mapping[str, str] | None, deadline: float | None
    ) -> Any | None:
        """A JSON answer; ``None`` for a 404. The Action API reports errors inside a 200."""
        response = self._send(url, params, deadline)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SourceResponseError(f"Wikimedia returned HTTP {response.status_code} for {url}")
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceResponseError(f"Wikimedia returned a non-JSON body for {url}") from exc
        if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
            code = body["error"].get("code", "error")
            raise SourceResponseError(f"Wikidata refused the request: {code}")
        return body

    def _wikidata(self, params: Mapping[str, str], deadline: float | None) -> Mapping[str, Any]:
        body = self._json(WIKIDATA_API, {**params, "format": "json"}, deadline)
        if not isinstance(body, Mapping):
            raise SourceResponseError("Wikidata returned an unexpected document")
        return body

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> WikimediaSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def describe(
        self,
        lei: str | None = None,
        *,
        name: str | None = None,
        deadline: float | None = None,
    ) -> WikiDescription | None:
        """The item for ``lei``, else the one item ``name`` exactly matches, with its summary.

        ``None`` when neither finds an item. The name is tried only after the LEI missed (or
        with no LEI at all), and only when :meth:`find_by_name` accepts the match.

        Raises:
            SourceUnavailableError, SourceResponseError: Wikidata could not be asked. A
                Wikipedia failure after the item was found is a note, not an error - the item
                alone is still a description.
        """
        item = self.find_by_lei(lei, deadline=deadline) if lei else None
        if item is None and name and self._settings.wikimedia_name_match:
            item = self.find_by_name(name, lei=lei, deadline=deadline)
        if item is None:
            return None
        notes: list[str] = []
        for link in item.sitelinks:
            try:
                summary = self.summary(link.lang, link.title, deadline=deadline)
            except SourceError as exc:
                LOGGER.warning("Wikipedia (%s) summary failed for %s: %s", link.lang, item.qid, exc)
                notes.append(f"Wikipedie ({link.lang}): článek se nepodařilo načíst ({exc})")
                continue
            if summary is not None:
                return WikiDescription(item=item, summary=summary, notes=tuple(notes))
        if not item.sitelinks:
            notes.append(f"Wikidata {item.qid} nemá článek na Wikipedii v jazycích cs/en")
        return WikiDescription(item=item, notes=tuple(notes))

    def find_by_lei(self, lei: str, *, deadline: float | None = None) -> WikidataItem | None:
        """The Wikidata item whose P1278 is ``lei``, or ``None`` when no item carries it."""
        lei = lei.strip().upper()
        if not _LEI_RE.fullmatch(lei):
            return None
        found = self._wikidata(
            {
                "action": "query",
                "list": "search",
                "srsearch": f"haswbstatement:{P_LEI}={lei}",
                "srlimit": "5",
                "srprop": "",
            },
            deadline,
        )
        query = _mapping(found.get("query"))
        hits = query.get("search")
        qids = [
            str(hit.get("title"))
            for hit in (hits if isinstance(hits, list) else [])
            if isinstance(hit, Mapping) and _QID_RE.fullmatch(str(hit.get("title", "")))
        ]
        if not qids:
            return None
        return self._load_item(qids[0], deadline, other_items=len(qids) - 1)

    def find_by_name(
        self, name: str, *, lei: str | None = None, deadline: float | None = None
    ) -> WikidataItem | None:
        """The one item whose label or alias *is* ``name``; ``None`` unless the match is clean.

        The exact name is searched first; if nothing matches, the name without its legal-form
        suffix. A hit counts when the text Wikidata matched equals the query after
        :func:`_fold` (case, diacritics, punctuation). Then:

        * several matching items -> ``None`` (a namesake would be a wrong description);
        * the item carries a LEI (P1278) other than ``lei`` -> ``None`` (another legal entity:
          the group, the brand, the manager);
        * the item carries a LEI, ``lei`` is unknown and only the suffix-stripped query matched
          -> ``None`` (a brand match to *some* entity is not evidence it is this one).
        """
        query = _SPACE_RE.sub(" ", name).strip()
        if len(_fold(query)) < 3:
            return None
        stripped = _LEGAL_FORM_RE.sub("", query).strip()
        queries = [(True, query)]
        if stripped and _fold(stripped) != _fold(query):
            queries.append((False, stripped))
        for exact, text in queries:
            hits = self._search_names(text, deadline)
            if len(hits) > 1:
                LOGGER.info("Wikidata: %r matches %d items; none taken", text, len(hits))
                return None
            if not hits:
                continue
            qid = hits[0]
            carried = self._lei_of(qid, deadline)
            foreign = carried is not None and (
                (lei is not None and carried != lei.strip().upper()) or (lei is None and not exact)
            )
            if foreign:
                LOGGER.info(
                    "Wikidata: %s matches %r but carries LEI %s; not taken", qid, text, carried
                )
                return None
            return self._load_item(qid, deadline, matched_by="name")
        return None

    def _search_names(self, text: str, deadline: float | None) -> list[str]:
        """Items whose matched label or alias equals ``text``, from the first edition with any.

        The editions are asked in order and the first one with a match decides: a later
        edition may add an item whose label *there* happens to be the name (the EIB's
        building carries "European Investment Bank" as its Czech label) and turn a clean
        match into a tie.
        """
        wanted = _fold(text)
        found: dict[str, None] = {}
        for language in NAME_SEARCH_LANGUAGES:
            if found:
                break
            body = self._wikidata(
                {
                    "action": "wbsearchentities",
                    "search": text,
                    "language": language,
                    "uselang": language,
                    "type": "item",
                    "limit": str(NAME_SEARCH_LIMIT),
                },
                deadline,
            )
            hits = body.get("search")
            for hit in hits if isinstance(hits, list) else []:
                hit = _mapping(hit)
                qid = _text(hit.get("id"))
                matched = _text(_mapping(hit.get("match")).get("text")) or _text(hit.get("label"))
                about = (_text(hit.get("description")) or "").lower()
                if not qid or not _QID_RE.fullmatch(qid) or not matched:
                    continue
                if _fold(matched) != wanted or any(word in about for word in _NEVER_AN_ISSUER):
                    continue
                found[qid] = None
        return list(found)

    def _lei_of(self, qid: str, deadline: float | None) -> str | None:
        """The LEI the item states (P1278), or ``None``."""
        claims = _mapping(
            self._wikidata(
                {"action": "wbgetclaims", "entity": qid, "property": P_LEI}, deadline
            ).get("claims")
        ).get(P_LEI)
        for claim in claims if isinstance(claims, Sequence) else ():
            claim = _mapping(claim)
            if claim.get("rank") == "deprecated":
                continue
            value = _mapping(_mapping(claim.get("mainsnak")).get("datavalue")).get("value")
            if isinstance(value, str) and _LEI_RE.fullmatch(value.strip().upper()):
                return value.strip().upper()
        return None

    def _load_item(
        self,
        qid: str,
        deadline: float | None,
        *,
        other_items: int = 0,
        matched_by: str = "lei",
    ) -> WikidataItem | None:
        """Labels, descriptions, sitelinks and industries of ``qid``; ``None`` if it is missing."""
        retrieved_at = datetime.now(UTC)
        languages = self.languages
        entities = _mapping(
            self._wikidata(
                {
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "labels|descriptions|sitelinks/urls",
                    "languages": "|".join(dict.fromkeys(("cs", "en", *languages))),
                    "sitefilter": "|".join(f"{lang}wiki" for lang in languages),
                },
                deadline,
            ).get("entities")
        )
        entity = _mapping(entities.get(qid))
        if not entity or "missing" in entity:
            return None
        labels = _mapping(entity.get("labels"))
        descriptions = _mapping(entity.get("descriptions"))
        sitelinks = _mapping(entity.get("sitelinks"))
        return WikidataItem(
            qid=qid,
            label_cs=_value(labels, "cs"),
            label_en=_value(labels, "en"),
            description_cs=_value(descriptions, "cs"),
            description_en=_value(descriptions, "en"),
            industries=self._industries(qid, deadline),
            sitelinks=tuple(
                Sitelink(lang=lang, title=str(link["title"]), url=_text(link.get("url")))
                for lang in languages
                if (link := _mapping(sitelinks.get(f"{lang}wiki"))) and _text(link.get("title"))
            ),
            provenance=Provenance(source="WEB", retrieved_at=retrieved_at, detail="web:wikidata"),
            other_items=other_items,
            matched_by=matched_by,
        )

    def summary(
        self, lang: str, title: str, *, deadline: float | None = None
    ) -> WikipediaSummary | None:
        """The lead of an article; ``None`` for a missing page, a disambiguation or no text."""
        url = SUMMARY_URL.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""))
        body = self._json(url, None, deadline)
        if not isinstance(body, Mapping) or body.get("type") == "disambiguation":
            return None
        extract = _clean(_text(body.get("extract")) or "")
        if not extract:
            return None
        page = _text(_mapping(_mapping(body.get("content_urls")).get("desktop")).get("page"))
        retrieved_at = datetime.now(UTC)
        revised_at = _parse_datetime(body.get("timestamp"))
        return WikipediaSummary(
            lang=lang,
            title=_text(body.get("title")) or title,
            extract=extract,
            url=page or f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            revised_at=revised_at,
            provenance=Provenance(
                source="WEB",
                retrieved_at=retrieved_at,
                snapshot_at=revised_at,
                detail=f"web:wikipedia:{lang}",
            ),
        )

    # -- internals ---------------------------------------------------------------------

    def _industries(self, qid: str, deadline: float | None) -> tuple[Industry, ...]:
        """The item's P452 values with their labels; ``()`` when it states none."""
        claims = _mapping(
            self._wikidata(
                {"action": "wbgetclaims", "entity": qid, "property": P_INDUSTRY}, deadline
            ).get("claims")
        ).get(P_INDUSTRY)
        ranked: list[tuple[int, str]] = []
        for claim in claims if isinstance(claims, Sequence) else ():
            claim = _mapping(claim)
            rank = claim.get("rank")
            value = _mapping(
                _mapping(_mapping(claim.get("mainsnak")).get("datavalue")).get("value")
            )
            industry = _text(value.get("id"))
            if rank == "deprecated" or not industry or not _QID_RE.fullmatch(industry):
                continue
            ranked.append((0 if rank == "preferred" else 1, industry))
        ids = list(dict.fromkeys(industry for _, industry in sorted(ranked, key=lambda r: r[0])))
        ids = ids[:MAX_INDUSTRIES]
        if not ids:
            return ()
        entities = _mapping(
            self._wikidata(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(ids),
                    "props": "labels",
                    "languages": "cs|en",
                },
                deadline,
            ).get("entities")
        )
        industries: list[Industry] = []
        for industry in ids:
            labels = _mapping(_mapping(entities.get(industry)).get("labels"))
            industries.append(
                Industry(qid=industry, label_cs=_value(labels, "cs"), label_en=_value(labels, "en"))
            )
        return tuple(industries)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _value(labels: Mapping[str, Any], lang: str) -> str | None:
    """``labels[lang]["value"]``, the shape of Wikidata labels and descriptions."""
    return _text(_mapping(labels.get(lang)).get("value"))


def _fold(name: str) -> str:
    """``"Assicurazioni Generali S.p.A."`` -> ``"assicurazionigeneralispa"``: the comparison key."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub("", ascii_only.lower())


def _clean(extract: str) -> str:
    """Drop editorial marks and stray spacing, keeping paragraph breaks."""
    lines = (_SPACE_RE.sub(" ", _MARK_RE.sub("", line)).strip() for line in extract.splitlines())
    return "\n".join(line for line in lines if line)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "Industry",
    "Sitelink",
    "WikiDescription",
    "WikidataItem",
    "WikimediaSource",
    "WikipediaSummary",
]
