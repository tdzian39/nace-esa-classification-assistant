"""Web evidence for foreign issuers: find out what a company actually does, and cite it.

The classifier cannot judge an issuer from its name. This module turns an ISIN or a name
into a short activity description plus the sources it came from, so the suggestion the tool
finally makes can be checked rather than trusted.

Scope, from CLAUDE.md, enforced here rather than merely documented:

* **Foreign issuers only.** A Czech subject with a RES record is answered from DWS or ARES
  and never reaches the web.
* **Never scrape ``apl.czso.cz`` or ``or.justice.cz``** - see :data:`BLOCKED_HOSTS`, which
  refuses them at fetch time whatever a search result says.
* Rows built here are stamped ``source="WEB"``.

Design notes:

* The search provider is pluggable. Which search API a bank may call is a procurement
  question, not an engineering one, so :class:`SearchProvider` is a protocol and the HTTP
  implementation is configured by URL and field paths rather than hardcoded to a vendor.
  With none configured the gatherer still works - it just relies on the description MO
  typed in, which is the common case for an issuer they already know something about.
* A model with built-in web search could implement the same protocol later. Keeping
  evidence separate from the model call is deliberate: it stays citable, cacheable and
  reviewable on its own.
* Nothing here may ever receive data from DWS. It takes a public name, an ISIN or text the
  user typed, and nothing else.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import httpx

from config.settings import Settings
from core.sources.base import Provenance, SourceResponseError, SourceUnavailableError

LOGGER = logging.getLogger(__name__)

#: Hosts this tool must never fetch. The Czech registers have a sanctioned API (ARES) and
#: CLAUDE.md forbids scraping their web front ends; the rule is enforced, not just written.
BLOCKED_HOSTS: Final[frozenset[str]] = frozenset(
    {"apl.czso.cz", "or.justice.cz", "justice.cz", "czso.cz"}
)

#: Only these schemes are ever fetched.
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: Space, tab and NBSP - deliberately not ``\s``, which would eat the newlines
#: that keep paragraphs apart. Written unraw so the NBSP stays a visible escape in the
#: source rather than an invisible character someone removes by accident.
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile("[ \\t\\u00a0]+")
_BLANKLINES_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

#: Elements whose text is never part of a description.
_SKIP_TAGS: Final[frozenset[str]] = frozenset(
    {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "form"}
)


def is_blocked(url: str) -> bool:
    """Whether ``url`` must not be fetched: wrong scheme, or a forbidden register host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return True
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return True
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result, before anything is fetched."""

    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """One piece of evidence behind a description, shown to the reviewer."""

    url: str
    title: str = ""
    snippet: str = ""
    fetched: bool = False
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssuerEvidence:
    """What the web said about an issuer.

    Attributes:
        query: What was searched for.
        issuer_name: Best available name - the one supplied, or the best search hit's title.
        description: The assembled activity description handed to the classifier.
        sources: Where it came from, in the order used.
        provenance: Always ``WEB``, with the retrieval time.
        notes: Why the result is thin, when it is.
    """

    query: str
    issuer_name: str | None = None
    description: str | None = None
    sources: tuple[EvidenceSource, ...] = ()
    provenance: Provenance | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def has_description(self) -> bool:
        """Whether there is anything worth classifying.

        An issuer with no description must lead to an abstention, not a guess from the name.
        """
        return bool(self.description and self.description.strip())


class SearchProvider(Protocol):
    """Anything that can turn a query into ranked hits."""

    name: str

    def search(self, query: str, *, limit: int) -> tuple[SearchHit, ...]:
        """Return hits for ``query``, best first. May return ``()``."""
        ...


class NullSearchProvider:
    """No search configured: the gatherer falls back to what the user typed."""

    name = "none"

    def search(self, query: str, *, limit: int) -> tuple[SearchHit, ...]:
        return ()


class StaticSearchProvider:
    """Canned hits, for tests and for replaying a captured search."""

    name = "static"

    def __init__(self, hits: Mapping[str, Sequence[SearchHit]] | Sequence[SearchHit]) -> None:
        self._hits = hits

    def search(self, query: str, *, limit: int) -> tuple[SearchHit, ...]:
        hits = self._hits.get(query, ()) if isinstance(self._hits, Mapping) else self._hits
        return tuple(hits)[:limit]


def _dig(payload: Any, path: str) -> Any:
    """Follow a dotted path into a JSON document, returning ``None`` when it does not exist."""
    current = payload
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


class HttpSearchProvider:
    """A JSON search API, described by settings rather than hardcoded to a vendor.

    ``web_search_url`` carries ``{query}``; ``web_search_results_path`` and the three field
    names say how to read the response. TODO: confirm all four against whichever provider
    the bank approves - the defaults follow Brave's shape.
    """

    name = "http"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Accept": "application/json", "User-Agent": self._settings.web_user_agent}
            if self._settings.web_search_api_key is not None:
                headers[self._settings.web_search_api_key_header] = (
                    self._settings.web_search_api_key.get_secret_value()
                )
            self._client = httpx.Client(
                timeout=self._settings.web_timeout_seconds,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def search(self, query: str, *, limit: int) -> tuple[SearchHit, ...]:
        template = self._settings.web_search_url
        if not template:
            return ()
        url = template.replace("{query}", httpx.QueryParams({"q": query})["q"])
        try:
            response = self._ensure_client().get(url)
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(f"web search timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"web search is unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise SourceUnavailableError(f"web search returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise SourceResponseError(f"web search returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceResponseError(f"web search returned a non-JSON body: {exc}") from exc

        results = _dig(payload, self._settings.web_search_results_path)
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            LOGGER.warning(
                "web search response has no list at %r; check web_search_results_path",
                self._settings.web_search_results_path,
            )
            return ()
        hits: list[SearchHit] = []
        for item in results[:limit]:
            if not isinstance(item, Mapping):
                continue
            url_value = str(item.get(self._settings.web_search_field_url) or "").strip()
            if not url_value:
                continue
            hits.append(
                SearchHit(
                    url=url_value,
                    title=str(item.get(self._settings.web_search_field_title) or "").strip(),
                    snippet=str(item.get(self._settings.web_search_field_snippet) or "").strip(),
                )
            )
        return tuple(hits)


class _TextExtractor(HTMLParser):
    """Pull readable text and the meta description out of an HTML page.

    stdlib only: a dependency-free extractor is worth more here than a perfect one, because
    the output is a few hundred words of context for a classifier, not a rendering.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta_description: str = ""
        self.title: str = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta" and not self.meta_description:
            values = {key.lower(): (value or "") for key, value in attrs}
            marker = (values.get("name") or values.get("property") or "").lower()
            if marker in {"description", "og:description"}:
                self.meta_description = values.get("content", "").strip()
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    @property
    def text(self) -> str:
        joined = "".join(self.parts)
        joined = _WHITESPACE_RE.sub(" ", joined)
        return _BLANKLINES_RE.sub("\n\n", joined).strip()


def extract_text(html: str) -> tuple[str, str, str]:
    """``(meta_description, title, body_text)`` from an HTML document."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is forgiving, but never crash on input
        LOGGER.debug("HTML could not be fully parsed; using what was extracted", exc_info=True)
    return parser.meta_description.strip(), parser.title.strip(), parser.text


class WebEvidenceGatherer:
    """Turn a name or an ISIN into a description with citable sources.

    Args:
        settings: Limits, throttling and the search configuration.
        provider: Search provider; defaults to HTTP when a URL is configured, else none.
        client: Injected for tests, used for page fetches.
        sleep, monotonic: Injected so throttling can be asserted without spending time.
    """

    source = "WEB"

    def __init__(
        self,
        settings: Settings,
        *,
        provider: SearchProvider | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._provider = provider or (
            HttpSearchProvider(settings) if settings.web_search_url else NullSearchProvider()
        )
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    # -- transport ---------------------------------------------------------------------

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._settings.web_timeout_seconds,
                headers={"User-Agent": self._settings.web_user_agent},
                follow_redirects=True,
            )
        return self._client

    def _throttle(self) -> None:
        """Pace outbound requests; a batch of issuers must not hammer anybody's site."""
        interval = self._settings.web_min_interval_seconds
        if interval <= 0:
            return
        if self._last_request_at is not None:
            wait = interval - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def close(self) -> None:
        """Close anything this object opened."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> WebEvidenceGatherer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API --------------------------------------------------------------------

    def gather(
        self,
        *,
        name: str | None = None,
        isin: str | None = None,
        description: str | None = None,
    ) -> IssuerEvidence:
        """Assemble evidence about one foreign issuer.

        A description the user typed is authoritative and is used as-is; the web is only
        consulted to fill a gap. Nothing here raises on a thin result - an issuer the web
        cannot describe must reach the classifier as "no evidence", which makes it abstain,
        rather than as an exception that loses the row.
        """
        retrieved_at = datetime.now(UTC)
        provenance = Provenance(source="WEB", retrieved_at=retrieved_at, detail="web:search")
        query = (name or isin or "").strip()
        supplied = (description or "").strip()

        if supplied:
            return IssuerEvidence(
                query=query or supplied[:60],
                issuer_name=(name or "").strip() or None,
                description=supplied,
                provenance=Provenance(
                    source="WEB", retrieved_at=retrieved_at, detail="user-supplied"
                ),
                notes=("description supplied by the user; the web was not consulted",),
            )

        if not query:
            return IssuerEvidence(query="", provenance=provenance, notes=("nothing to search for",))
        if not self._settings.web_enabled:
            return IssuerEvidence(
                query=query, provenance=provenance, notes=("web lookups are disabled",)
            )

        notes: list[str] = []
        try:
            hits = self._provider.search(query, limit=self._settings.web_max_results)
        except (SourceUnavailableError, SourceResponseError) as exc:
            LOGGER.warning("web search failed for %r: %s", query, exc)
            return IssuerEvidence(
                query=query, provenance=provenance, notes=(f"search failed: {exc}",)
            )

        usable = [hit for hit in hits if not is_blocked(hit.url)]
        if len(usable) < len(hits):
            notes.append("some results were skipped: the Czech registers must not be scraped")
        if not usable:
            if self._provider.name == "none":
                notes.append("no search provider configured; supply a description instead")
            else:
                notes.append("no usable search result")
            return IssuerEvidence(query=query, provenance=provenance, notes=tuple(notes))

        sources: list[EvidenceSource] = []
        chunks: list[str] = []
        for hit in usable:
            fetched = False
            page_text = ""
            if len(chunks) < self._settings.web_max_pages:
                page_text, error = self._read(hit.url)
                fetched = bool(page_text)
                if error:
                    notes.append(error)
            sources.append(
                EvidenceSource(
                    url=hit.url,
                    title=hit.title,
                    snippet=hit.snippet,
                    fetched=fetched,
                    retrieved_at=retrieved_at if fetched else None,
                )
            )
            text = page_text or hit.snippet
            if text:
                chunks.append(text)

        description_text = self._assemble(chunks)
        if not description_text:
            notes.append("search returned results but no readable description")
        return IssuerEvidence(
            query=query,
            issuer_name=(name or "").strip() or (usable[0].title or None),
            description=description_text or None,
            sources=tuple(sources),
            provenance=provenance,
            notes=tuple(notes),
        )

    # -- internals ---------------------------------------------------------------------

    def _read(self, url: str) -> tuple[str, str | None]:
        """Fetch one page and extract its text. Returns ``(text, error note)``."""
        if is_blocked(url):  # defence in depth; callers filter too
            return "", f"refused to fetch a blocked host: {url}"
        self._throttle()
        try:
            response = self._ensure_client().get(url)
        except httpx.HTTPError as exc:
            LOGGER.debug("could not fetch %s: %s", url, exc)
            return "", f"could not read {url}"
        if response.status_code >= 400:
            return "", f"{url} returned HTTP {response.status_code}"
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return "", f"{url} is not a readable document ({content_type or 'unknown type'})"

        meta, _title, body = extract_text(response.text)
        # The meta description is a human-written summary of the whole page; prefer it and
        # top up from the body, rather than dumping navigation text into the prompt.
        parts = [part for part in (meta, body) if part]
        return "\n\n".join(parts), None

    def _assemble(self, chunks: Sequence[str]) -> str:
        """Join the evidence into one capped description, without repeating itself.

        Two pages about the same issuer overlap heavily - the same "about us" paragraph, the
        same boilerplate - and plain concatenation would spend the character budget, and
        later the prompt budget, saying one thing twice.
        """
        unique: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            for paragraph in chunk.split(chr(10) + chr(10)):
                block = paragraph.strip()
                if not block:
                    continue
                key = " ".join(block.split()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(block)
        text = (chr(10) + chr(10)).join(unique)
        limit = self._settings.web_max_description_chars
        if len(text) <= limit:
            return text
        clipped = text[:limit]
        # Cut at a sentence end when there is one nearby, so the classifier is not handed a
        # description that stops mid-word.
        for marker in (". ", ".\n", "! ", "? "):
            cut = clipped.rfind(marker)
            if cut > limit // 2:
                return clipped[: cut + 1].strip()
        return clipped.rstrip() + "…"
