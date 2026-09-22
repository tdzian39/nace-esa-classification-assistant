"""Startup consistency check of a loaded :class:`~core.codebooks.models.CodebookSet`.

The check never raises by itself; it produces a :class:`ConsistencyReport` of findings with
stable codes (``E_*`` errors, ``W_*`` warnings, ``I_*`` informational) that
:func:`assert_consistent` turns into a :class:`~core.codebooks.errors.CodebookConsistencyError`.
The central guarantee it enforces: every CTS ID the two lookups can emit exists in the loaded
codebook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.codebooks.errors import CodebookConsistencyError, MalformedCodeError, UnknownCodeError
from core.codebooks.models import CodebookSet, CtsCodebook, MalformedRow
from core.codebooks.normalize import format_esa_code

Severity = Literal["error", "warning", "info"]

_SEVERITY_ORDER: dict[str, int] = {"error": 0, "warning": 1, "info": 2}
#: Cap on the number of codes quoted inside a finding message (details always hold them all).
_MESSAGE_LIST_LIMIT = 25


@dataclass(frozen=True)
class Finding:
    """One observation of the consistency check.

    Attributes:
        severity: ``error`` blocks startup in strict mode, ``warning`` and ``info`` do not.
        code: Stable machine-readable code such as ``E_DUPLICATE_CTS_ID``.
        message: Human-readable one-liner.
        details: Structured data (codebook name, offending codes, rows ...), JSON-friendly.
    """

    severity: Severity
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsistencyReport:
    """All findings of one check run."""

    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def infos(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "info")

    @property
    def ok(self) -> bool:
        """True when there is no error-level finding."""
        return not self.errors

    @property
    def counts(self) -> dict[str, int]:
        """Number of findings per severity (always all three keys)."""
        return {
            "error": len(self.errors),
            "warning": len(self.warnings),
            "info": len(self.infos),
        }

    def sorted(self) -> tuple[Finding, ...]:
        """Findings ordered errors first, then warnings, then infos (stable within a level)."""
        return tuple(sorted(self.findings, key=lambda f: _SEVERITY_ORDER[f.severity]))

    def summary(self) -> str:
        """Multi-line text: a header line followed by one line per finding, errors first."""
        counts = self.counts
        status = "OK" if self.ok else "FAILED"
        lines = [
            f"Codebook consistency {status}: {counts['error']} error(s), "
            f"{counts['warning']} warning(s), {counts['info']} info"
        ]
        lines.extend(f"[{f.severity.upper()}] {f.code}: {f.message}" for f in self.sorted())
        return "\n".join(lines)


def _quote(codes: list[str]) -> str:
    """Comma-separated codes for a message, truncated with a count when too many."""
    if len(codes) <= _MESSAGE_LIST_LIMIT:
        return ", ".join(codes)
    shown = ", ".join(codes[:_MESSAGE_LIST_LIMIT])
    return f"{shown} ... ({len(codes) - _MESSAGE_LIST_LIMIT} more)"


def _malformed_details(rows: tuple[MalformedRow, ...]) -> list[dict[str, object]]:
    return [{"row": r.row_number, "reason": r.reason, "raw": dict(r.raw)} for r in rows]


class _Checker:
    """Collects findings for one codebook set; one method per group of checks."""

    def __init__(self, codebooks: CodebookSet) -> None:
        self.cb = codebooks
        self.findings: list[Finding] = []
        self.cts_books: tuple[tuple[str, CtsCodebook], ...] = (
            ("cts_ba0036", codebooks.cts_ba0036),
            ("cts_okec_nace2", codebooks.cts_okec_nace2),
        )

    def add(self, severity: Severity, code: str, message: str, **details: object) -> None:
        self.findings.append(Finding(severity, code, message, dict(details)))

    def run(self) -> ConsistencyReport:
        self.check_empty()
        self.check_duplicates()
        self.check_malformed()
        self.check_valid_esa_have_cts_ids()
        self.check_nace_stat_have_cts_ids()
        self.check_emitted_ids()
        self.check_valid_list_duplicates()
        self.check_cts_nace_have_labels()
        self.check_descriptions()
        self.check_skipped_rows()
        self.info_parent_codes()
        return ConsistencyReport(tuple(self.findings))

    # -- errors --------------------------------------------------------------------------

    def check_empty(self) -> None:
        sizes = (
            ("cts_ba0036", len(self.cb.cts_ba0036.by_id), self.cb.cts_ba0036.file),
            ("ba0036_valid", len(self.cb.ba0036_valid.by_key), self.cb.ba0036_valid.file),
            ("cts_okec_nace2", len(self.cb.cts_okec_nace2.by_id), self.cb.cts_okec_nace2.file),
            ("nace_stat", len(self.cb.nace_stat.divisions), self.cb.nace_stat.file),
        )
        for name, size, file in sizes:
            if size == 0:
                self.add(
                    "error",
                    "E_EMPTY_CODEBOOK",
                    f"{name} has no usable entries ({file.path.name}, {file.row_count} data rows read)",
                    codebook=name,
                    file=str(file.path),
                    row_count=file.row_count,
                )

    def check_duplicates(self) -> None:
        for name, book in self.cts_books:
            if book.duplicate_ids:
                ids = list(book.duplicate_ids)
                self.add(
                    "error",
                    "E_DUPLICATE_CTS_ID",
                    f"{name}: CTS ID(s) appear more than once: {_quote(ids)}",
                    codebook=name,
                    ids=ids,
                )
            if book.duplicate_keys:
                keys = list(book.duplicate_keys)
                values = [self._display(book, key) for key in keys]
                self.add(
                    "error",
                    "E_DUPLICATE_CTS_VALUE",
                    f"{name}: code(s) mapped by more than one row (ambiguous CTS ID): {_quote(values)}",
                    codebook=name,
                    keys=keys,
                    values=values,
                )

    def check_malformed(self) -> None:
        books = (
            ("cts_ba0036", self.cb.cts_ba0036.malformed),
            ("ba0036_valid", self.cb.ba0036_valid.malformed),
            ("cts_okec_nace2", self.cb.cts_okec_nace2.malformed),
            ("nace_stat", self.cb.nace_stat.malformed),
        )
        for name, malformed in books:
            if malformed:
                rows = [str(r.row_number) for r in malformed]
                self.add(
                    "error",
                    "E_MALFORMED_ROW",
                    f"{name}: {len(malformed)} row(s) with a code that cannot be normalized "
                    f"(Excel rows {_quote(rows)})",
                    codebook=name,
                    rows=_malformed_details(malformed),
                )

    def check_valid_esa_have_cts_ids(self) -> None:
        missing = [
            sector.code
            for sector in self.cb.esa_leaves()
            if sector.key not in self.cb.cts_ba0036.by_key
        ]
        if missing:
            self.add(
                "error",
                "E_VALID_ESA_WITHOUT_CTS_ID",
                f"{len(missing)} valid ESA leaf code(s) have no CTS_BA0036 entry and could never "
                f"be emitted: {_quote(missing)}",
                codes=missing,
            )

    def check_nace_stat_have_cts_ids(self) -> None:
        missing = [
            code for code in self.cb.nace_stat.codes if code not in self.cb.cts_okec_nace2.by_key
        ]
        if missing:
            self.add(
                "error",
                "E_NACE_STAT_WITHOUT_CTS_ID",
                f"{len(missing)} NACE_STAT division(s) have no CTS_OKEC_NACE2 entry: {_quote(missing)}",
                codes=missing,
            )

    def check_emitted_ids(self) -> None:
        """The explicit spec self-check: every emittable CTS ID exists in its codebook."""
        esa_ids: dict[str, str] = {}
        for sector in self.cb.esa_leaves():
            try:
                esa_ids[self.cb.cts_id_for_esa(sector.code).cts_id] = sector.code
            except (UnknownCodeError, MalformedCodeError):
                continue  # already reported by E_VALID_ESA_WITHOUT_CTS_ID / E_MALFORMED_ROW
        nace_ids: dict[str, str] = {}
        for division in self.cb.nace_divisions():
            try:
                nace_ids[self.cb.cts_id_for_nace(division.code).cts_id] = division.code
            except (UnknownCodeError, MalformedCodeError):
                continue  # already reported by E_NACE_STAT_WITHOUT_CTS_ID
        missing_esa = sorted(i for i in esa_ids if i not in self.cb.cts_ba0036.by_id)
        missing_nace = sorted(i for i in nace_ids if i not in self.cb.cts_okec_nace2.by_id)
        if missing_esa or missing_nace:
            self.add(
                "error",
                "E_EMITTED_ID_MISSING",
                "emittable CTS ID(s) are not present in the loaded codebook: "
                f"ESA {_quote(missing_esa) or '-'}; NACE {_quote(missing_nace) or '-'}",
                esa_ids=missing_esa,
                nace_ids=missing_nace,
            )
        self.add(
            "info",
            "I_EMITTABLE_IDS",
            f"codebook version {self.cb.version.id}: {len(esa_ids)} emittable ESA CTS ID(s), "
            f"{len(nace_ids)} emittable NACE CTS ID(s)",
            version=self.cb.version.id,
            esa_count=len(esa_ids),
            nace_count=len(nace_ids),
        )

    # -- warnings ------------------------------------------------------------------------

    def check_valid_list_duplicates(self) -> None:
        duplicates = [format_esa_code(key) for key in self.cb.ba0036_valid.duplicate_keys]
        if duplicates:
            self.add(
                "warning",
                "W_DUPLICATE_VALID_ESA",
                f"ba0036_valid: duplicate Kód (first row wins): {_quote(duplicates)}",
                codes=duplicates,
            )

    def check_cts_nace_have_labels(self) -> None:
        unlabeled = [
            key for key in self.cb.cts_okec_nace2.by_key if key not in self.cb.nace_stat.divisions
        ]
        if unlabeled:
            self.add(
                "warning",
                "W_CTS_NACE_NOT_IN_STAT",
                f"cts_okec_nace2: {len(unlabeled)} division(s) without NACE_STAT labels: "
                f"{_quote(unlabeled)}",
                codes=unlabeled,
            )

    def check_descriptions(self) -> None:
        checks: tuple[tuple[str, str, list[str]], ...] = (
            (
                "cts_ba0036",
                "DESCRIPTION",
                [e.value for e in dict.fromkeys(self.cb.cts_ba0036.entries) if not e.description],
            ),
            (
                "ba0036_valid",
                "Název",
                [s.code for s in self.cb.ba0036_valid.by_key.values() if not s.name],
            ),
            (
                "cts_okec_nace2",
                "DESCRIPTION",
                [
                    e.value
                    for e in dict.fromkeys(self.cb.cts_okec_nace2.entries)
                    if not e.description
                ],
            ),
            (
                "nace_stat",
                "Zkrtext/Text",
                [d.code for d in self.cb.nace_divisions() if not d.labels],
            ),
        )
        for name, column, codes in checks:
            if codes:
                self.add(
                    "warning",
                    "W_MISSING_DESCRIPTION",
                    f"{name}: {len(codes)} entr(y/ies) with empty {column}: {_quote(codes)}",
                    codebook=name,
                    column=column,
                    codes=codes,
                )

    def check_skipped_rows(self) -> None:
        books = (
            ("cts_ba0036", self.cb.cts_ba0036.skipped_rows),
            ("ba0036_valid", self.cb.ba0036_valid.skipped_rows),
            ("cts_okec_nace2", self.cb.cts_okec_nace2.skipped_rows),
            ("nace_stat", self.cb.nace_stat.skipped_rows),
        )
        for name, skipped in books:
            if skipped:
                self.add(
                    "warning",
                    "W_SKIPPED_ROWS",
                    f"{name}: {skipped} row(s) skipped because the code or ID cell was empty",
                    codebook=name,
                    skipped_rows=skipped,
                )

    # -- info ----------------------------------------------------------------------------

    def info_parent_codes(self) -> None:
        # Distinct entries in file order (not ``by_id``): a row hidden behind a duplicate CTS ID
        # is still live in ``by_key`` and must not disappear from the report.
        parents = [
            entry
            for entry in dict.fromkeys(self.cb.cts_ba0036.entries)
            if entry.key not in self.cb.ba0036_valid.by_key
        ]
        if parents:
            codes = [format_esa_code(e.key) for e in parents]
            self.add(
                "info",
                "I_CTS_ESA_NOT_VALID_LEAF",
                f"cts_ba0036: {len(parents)} entr(y/ies) are not valid leaves and will never be "
                f"emitted (expected: parent codes): {_quote(codes)}",
                codes=codes,
                ids=[e.cts_id for e in parents],
            )

    @staticmethod
    def _display(book: CtsCodebook, key: str) -> str:
        return format_esa_code(key) if book.kind == "BA0036" else key


def check_consistency(codebooks: CodebookSet) -> ConsistencyReport:
    """Run every check and return the report (never raises for a finding)."""
    return _Checker(codebooks).run()


def assert_consistent(report: ConsistencyReport) -> None:
    """Raise :class:`CodebookConsistencyError` carrying ``report`` unless ``report.ok``."""
    if not report.ok:
        raise CodebookConsistencyError(report)
