"""Golden cases of Czech (resident) issuers: RES's codes, replayed through the pipeline offline.

The foreign golden set (:mod:`core.classify.golden`) grades a shortlist. For a resident issuer
the shortlist is not the question - RES settles the codes (:mod:`core.classify.residents`) -
so these cases grade the **proposal** with the model off: the NACE division and the BA0036
code the page would show, or, where RES does not record the institution type, the tie the page
leaves to MO. Fifteen legal entities from Jakub's brief (2 Oct 2026), never natural persons;
``verified_by`` stays empty, as in the foreign set, until someone checks them.

The pipeline runs for real: GLEIF (by ISIN, or by IČO), the ESMA FIRDS fallback (the Czech
government bond), RES through ARES, and the ECB list memberships of the LEIs - all replayed
from ``tests/golden/residents_registers.json``, captured once with the live registers
(``python -m core.classify --golden-capture``) and trimmed like the foreign recording. A
request with no recorded answer is an error, never a silent miss.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx

from config.settings import APP_ROOT, Settings
from core.classify.golden import GoldenError
from core.classify.golden_fixtures import RecordingTransport, replay_transport
from core.sources.ares import AresSource
from core.sources.ecb import EcbEntry
from core.sources.gleif import GleifSource
from core.sources.identity import IssuerIdentifier

if TYPE_CHECKING:  # pragma: no cover
    from core.codebooks.models import CodebookSet
    from core.suggest import IssuerSuggestion, SuggestionRequest, SuggestionService

#: The cases, next to the foreign ones.
RESIDENTS_PATH: Final[Path] = APP_ROOT / "tests" / "golden" / "residents.json"
#: The recorded register answers and ECB memberships they replay.
REGISTERS_PATH: Final[Path] = APP_ROOT / "tests" / "golden" / "residents_registers.json"


@dataclass(frozen=True, slots=True)
class ResidentCase:
    """One Czech issuer and the codes RES gives it.

    Attributes:
        id: Stable slug, ``res-<name>``.
        subject: The name as RES records it.
        isin: An instrument of the issuer, when the case is looked up by ISIN; otherwise the
            IČO is typed into the name field, as MO would.
        ico: The issuer's IČO.
        legal_form, res_sector, res_nace: What RES said on capture (``pravniForma``,
            ``institucionalniSektor2010``, ``czNacePrevazujici``).
        expected_nace, expected_esa: The division and BA0036 code of Jakub's brief.
        tie: When the rules cannot pick (the institution type is not in any register), the
            codes the page offers MO; ``expected_esa`` must be among them.
        note: Why the case is there.
        verified_by: Who at the bank confirmed it; ``None`` = provisional.
    """

    id: str
    subject: str
    ico: str
    legal_form: str
    res_sector: str
    res_nace: str
    expected_nace: str
    expected_esa: str
    isin: str | None = None
    tie: tuple[str, ...] = ()
    note: str = ""
    verified_by: str | None = None

    def request(self) -> SuggestionRequest:
        """What MO would type: the ISIN, else the IČO in the name field."""
        from core.suggest import SuggestionRequest

        return SuggestionRequest(isin=self.isin) if self.isin else SuggestionRequest(name=self.ico)


def load_residents(path: Path | None = None) -> tuple[ResidentCase, ...]:
    """The resident cases. Raises :class:`GoldenError` for a missing or malformed file."""
    target = path or RESIDENTS_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenError(f"golden file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise GoldenError(f"{target.name} is not valid JSON: {exc}") from exc
    entries = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise GoldenError(f"{target.name} must hold a list under 'cases'")
    cases: list[ResidentCase] = []
    for entry in entries:
        try:
            cases.append(
                ResidentCase(
                    id=str(entry["id"]),
                    subject=str(entry["subject"]),
                    ico=str(entry["ico"]),
                    legal_form=str(entry["legal_form"]),
                    res_sector=str(entry["res_sector"]),
                    res_nace=str(entry["res_nace"]),
                    expected_nace=str(entry["expected_nace"]),
                    expected_esa=str(entry["expected_esa"]),
                    isin=entry.get("isin") or None,
                    tie=tuple(entry.get("tie") or ()),
                    note=str(entry.get("note") or ""),
                    verified_by=entry.get("verified_by") or None,
                )
            )
        except (KeyError, TypeError) as exc:
            raise GoldenError(f"{target.name}: a case lacks {exc}") from exc
    if len({case.id for case in cases}) != len(cases):
        raise GoldenError(f"{target.name} has a duplicate case id")
    return tuple(cases)


def replay_settings() -> Settings:
    """The registers a resident lookup asks, without throttles, the web or a model."""
    return Settings(
        _env_file=None,
        gleif_min_interval_seconds=0.0,
        gleif_fetch_parents=False,
        openfigi_enabled=False,
        firds_enabled=True,
        ares_enabled=True,
        ares_min_interval_seconds=0.0,
        web_enabled=False,
        wikimedia_enabled=False,
        llm_enabled=False,
        llm_api_key=None,
        llm_cache_path=None,
        llm_usage_path=None,
        ecb_enabled=False,
    )


class RecordedEcb:
    """The ECB list memberships captured with the cases, answered like an ``EcbRegister``."""

    def __init__(self, recorded: Mapping[str, Any]) -> None:
        self._as_of = recorded.get("as_of")
        self._entries = recorded.get("entries") or {}

    def find(self, lei: str) -> EcbEntry | None:
        if lei not in self._entries:
            raise GoldenError(f"no recorded ECB answer for LEI {lei}; re-run --golden-capture")
        entry = self._entries[lei]
        return EcbEntry(**entry) if entry else None

    def as_of(self) -> str | None:
        return self._as_of


def resident_identifier(
    settings: Settings,
    transport: httpx.BaseTransport,
    *,
    ecb: object = None,
    sleep: Callable[[float], None] | None = None,
) -> IssuerIdentifier:
    """The real GLEIF, FIRDS and ARES adapters, talking through ``transport``."""
    from core.sources.firds import firds_lei

    agent = {"User-Agent": settings.web_user_agent}
    gleif = httpx.Client(
        transport=transport,
        base_url=settings.gleif_base_url,
        timeout=settings.gleif_timeout_seconds,
        headers={"Accept": "application/vnd.api+json", **agent},
        follow_redirects=True,
    )
    ares = httpx.Client(
        transport=transport,
        base_url=settings.ares_base_url,
        timeout=settings.ares_timeout_seconds,
        headers={"Accept": "application/json", **agent},
        follow_redirects=True,
    )
    extra = {"sleep": sleep} if sleep is not None else {}
    return IssuerIdentifier(
        settings,
        gleif=GleifSource(settings, client=gleif, **extra),
        ares=AresSource(settings, client=ares, **extra),
        # firds_lei closes the client it is given, so each call gets its own.
        firds=lambda isin: firds_lei(
            isin, settings, client=httpx.Client(transport=transport, headers=agent)
        ),
        ecb=ecb,  # type: ignore[arg-type]
    )


def capture_residents(
    cases: Iterable[ResidentCase],
    settings: Settings,
    *,
    captured_on: date,
    ecb: object = None,
    path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Ask the live registers about every case and write the answers; returns their count.

    ``ecb`` is the ECB register (the central database); without one the S.122 cases that need
    the MFI list are recorded as ties.
    """
    recorder = RecordingTransport(transport)
    identifier = resident_identifier(settings, recorder, ecb=ecb)
    memberships: dict[str, dict[str, object] | None] = {}
    for case in cases:
        identity = identifier.identify(case.isin, ico=None if case.isin else case.ico)
        if identity.lei and ecb is not None:
            memberships[identity.lei] = asdict(identity.ecb_entry) if identity.ecb_entry else None
    document = {
        "_comment": [
            "GLEIF, ESMA FIRDS and RES (ARES) answers for tests/golden/residents.json, trimmed",
            "to the fields the parsers read, and the ECB list memberships of the LEIs found.",
            "Replayed by core.classify.golden_residents; re-record with: python -m core.classify",
            "--golden-capture (DATABASE_URL set, so the ECB lists are read).",
        ],
        "captured_on": captured_on.isoformat(),
        "user_agent": settings.web_user_agent,
        "answers": dict(sorted(recorder.answers.items())),
        "ecb": (
            {"as_of": ecb.as_of(), "entries": dict(sorted(memberships.items()))}  # type: ignore[attr-defined]
            if ecb is not None
            else None
        ),
    }
    (path or REGISTERS_PATH).write_text(
        json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return len(recorder.answers)


def load_registers(path: Path | None = None) -> tuple[dict[str, dict[str, object]], dict | None]:
    """The recorded answers and ECB memberships; empty when nothing has been captured."""
    target = path or REGISTERS_PATH
    if not target.is_file():
        return {}, None
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenError(f"{target.name} is not valid JSON: {exc}") from exc
    answers = document.get("answers")
    if not isinstance(answers, dict):
        raise GoldenError(f"{target.name} has no 'answers' object")
    return answers, document.get("ecb")


def replaying_service(codebooks: CodebookSet, *, path: Path | None = None) -> SuggestionService:
    """The pipeline over the recording: registers replayed, no web, the model off."""
    from core.classify.llm import LlmClassifier
    from core.classify.provider import NullLlmProvider
    from core.sources.web import WebEvidenceGatherer
    from core.suggest import SuggestionService

    answers, ecb = load_registers(path)
    if not answers:
        raise GoldenError(
            "no recorded register answers; run 'python -m core.classify --golden-capture'"
        )
    settings = replay_settings()
    identifier = resident_identifier(
        settings,
        replay_transport(answers),
        ecb=RecordedEcb(ecb) if ecb else None,
        sleep=lambda seconds: None,
    )
    return SuggestionService(
        codebooks,
        gatherer=WebEvidenceGatherer(settings),
        classifier=LlmClassifier(NullLlmProvider()),
        identifier=identifier,
    )


@dataclass(frozen=True, slots=True)
class ResidentResult:
    """One case through the pipeline: what the page would propose, against the brief."""

    case: ResidentCase
    nace: str | None
    esa: str | None
    tie: tuple[str, ...]

    @property
    def nace_ok(self) -> bool:
        return self.nace == self.case.expected_nace

    @property
    def esa_ok(self) -> bool:
        if self.case.tie:
            return self.esa is None and set(self.tie) == set(self.case.tie)
        return self.esa == self.case.expected_esa

    def describe(self) -> str:
        esa = self.esa or ("tie " + "/".join(self.tie) if self.tie else "none")
        mark = "ok  " if self.nace_ok and self.esa_ok else "MISS"
        return (
            f"{mark} {self.case.id:<28} NACE {self.nace or '-':<3} (brief {self.case.expected_nace})"
            f"  ESA {esa} (brief {self.case.expected_esa})"
        )


def result_of(case: ResidentCase, suggestion: IssuerSuggestion) -> ResidentResult:
    nace, esa = suggestion.nace_proposal, suggestion.esa_proposal
    return ResidentResult(
        case=case,
        nace=nace.code if nace else None,
        esa=esa.code if esa else None,
        tie=() if esa else suggestion.esa_candidates.codes,
    )


def score_residents(codebooks: CodebookSet) -> tuple[ResidentResult, ...]:
    """Every resident case through the replayed pipeline, model off."""
    service = replaying_service(codebooks)
    return tuple(result_of(case, service.suggest(case.request())) for case in load_residents())


__all__ = [
    "REGISTERS_PATH",
    "RESIDENTS_PATH",
    "RecordedEcb",
    "ResidentCase",
    "ResidentResult",
    "capture_residents",
    "load_registers",
    "load_residents",
    "replay_settings",
    "replaying_service",
    "resident_identifier",
    "result_of",
    "score_residents",
]
