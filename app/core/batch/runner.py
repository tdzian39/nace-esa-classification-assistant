"""Run a batch: read the input sheet, resolve every row, write the result workbook.

One row in, one row out, in input order - including rows that resolved to nothing, because a
reviewer working through a monthly list needs to see which lines came back empty, not find
them missing.

Repeated identifiers are resolved once and reused. A monthly list often names the same
client on several lines, and the public ARES API should not be asked the same question
twice.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.batch.reader import BatchInput, InputRow, read_batch_input
from core.export.columns import SUBJECT_COLUMNS, record_row
from core.export.xlsx import write_workbook
from core.identifiers.ico import try_normalize_ico
from core.sources.resolver import LookupResult, LookupStatus, SubjectResolver

LOGGER = logging.getLogger(__name__)

#: Column holding the Excel row number of the input line this result came from.
IN_ROW_COLUMN = "IN_row"
#: Column holding the text that was actually looked up.
IN_IDENTIFIER_COLUMN = "IN_identifier"
#: Column holding the reader's explanation of an unusual identifier choice.
IN_NOTE_COLUMN = "IN_note"


@dataclass(frozen=True, slots=True)
class BatchReport:
    """What one batch run did, for the console and for the Run sheet."""

    input_path: Path
    output_path: Path
    sheet: str
    row_count: int
    counts: Mapping[str, int]
    started_at: datetime
    finished_at: datetime
    user: str
    codebook_version: str | None
    sources: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def resolved(self) -> int:
        """Rows that produced a subject."""
        return self.counts.get("found", 0)

    @property
    def unresolved(self) -> int:
        """Rows that need a human: not found, ambiguous, invalid input or a source failure."""
        return self.row_count - self.resolved

    @property
    def all_failed(self) -> bool:
        """True when every row failed because no source could answer."""
        return self.row_count > 0 and self.counts.get("error", 0) == self.row_count

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def summary(self) -> str:
        """One line for the console."""
        breakdown = ", ".join(f"{status}={count}" for status, count in sorted(self.counts.items()))
        return (
            f"{self.row_count} row(s) in {self.duration_seconds:.1f}s "
            f"({breakdown}) -> {self.output_path}"
        )

    def run_metadata(self) -> dict[str, object]:
        """Key/value pairs written to the Run sheet of the workbook."""
        metadata: dict[str, object] = {
            "input file": self.input_path.name,
            "input sheet": self.sheet,
            "rows": self.row_count,
            "started at (UTC)": self.started_at.replace(tzinfo=None),
            "finished at (UTC)": self.finished_at.replace(tzinfo=None),
            "requested by": self.user,
            "sources": self.sources,
            "codebook version": self.codebook_version or "(codebooks not loaded)",
        }
        for status, count in sorted(self.counts.items()):
            metadata[f"rows {status}"] = count
        for index, note in enumerate(self.notes, start=1):
            metadata[f"note {index}"] = note
        return metadata


def build_columns(batch_input: BatchInput, *, echo_input: bool) -> tuple[str, ...]:
    """Sheet columns: the input echo first, then the subject columns.

    Echoing the original columns lets a reviewer keep working in the sheet they submitted
    instead of matching two files by IČO. Input columns are prefixed ``IN_`` so they can
    never collide with a subject column.
    """
    columns = [IN_ROW_COLUMN, IN_IDENTIFIER_COLUMN, IN_NOTE_COLUMN]
    if echo_input:
        columns.extend(f"IN_{name}" for name in batch_input.columns)
    return (*columns, *SUBJECT_COLUMNS)


def build_row(input_row: InputRow, result: LookupResult, *, echo_input: bool) -> dict[str, object]:
    """Merge one input line and its lookup result into one output row."""
    row: dict[str, object] = {
        IN_ROW_COLUMN: input_row.row_number,
        IN_IDENTIFIER_COLUMN: input_row.identifier,
        IN_NOTE_COLUMN: input_row.note,
    }
    if echo_input:
        for name, value in input_row.columns.items():
            row[f"IN_{name}"] = value
    row.update(record_row(result.record, status=result.status))

    # Explain an empty row where the record itself cannot: a miss, an ambiguous name or a
    # rejected identifier carries its reason in the result, not in a SubjectRecord.
    if result.record is None:
        notes = list(result.messages)
        if result.status == "ambiguous":
            notes.extend(
                f"candidate: {candidate.ico} {candidate.name or ''}".strip()
                for candidate in result.candidates
            )
        row["notes"] = tuple(notes)
        row["ico"] = result.ico
    return row


def cache_key(identifier: str) -> str:
    """The key two spellings of the same subject must share.

    An IČO is keyed by its normalized 8-digit form, so ``"49240901"``, ``"  49 240 901 "`` and
    the Excel-mangled ``49240901.0`` are one lookup rather than three. Anything else is keyed
    by its text with whitespace runs collapsed and case folded.
    """
    ico = try_normalize_ico(identifier)
    if ico is not None:
        return f"ico:{ico}"
    return "name:" + " ".join(identifier.split()).casefold()


def resolve_rows(
    rows: Sequence[InputRow], resolver: SubjectResolver
) -> list[tuple[InputRow, LookupResult]]:
    """Resolve every row, asking each distinct identifier only once."""
    cache: dict[str, LookupResult] = {}
    resolved: list[tuple[InputRow, LookupResult]] = []
    for input_row in rows:
        key = cache_key(input_row.identifier)
        result = cache.get(key)
        if result is None:
            result = resolver.resolve(input_row.identifier)
            cache[key] = result
        resolved.append((input_row, result))
    if len(cache) < len(rows):
        LOGGER.info("%d row(s) resolved from %d distinct identifier(s)", len(rows), len(cache))
    return resolved


def default_output_path(input_path: Path) -> Path:
    """``clients.xlsx`` -> ``clients_lookup.xlsx``, beside the input."""
    return input_path.with_name(f"{input_path.stem}_lookup.xlsx")


def run_batch(
    input_path: Path,
    output_path: Path | None,
    *,
    resolver: SubjectResolver,
    user: str,
    codebook_version: str | None = None,
    sheet: str | None = None,
    echo_input: bool = True,
) -> BatchReport:
    """Read ``input_path``, resolve every row and write the result workbook.

    Raises:
        BatchInputError: the input cannot be read (propagated from the reader).
        ValueError: the output path is the input path - a batch never overwrites its source.
    """
    started_at = datetime.now(UTC)
    batch_input = read_batch_input(input_path, sheet=sheet)
    destination = output_path or default_output_path(input_path)
    if destination.resolve() == input_path.resolve():
        raise ValueError("the output file would overwrite the input file; choose another path")

    resolved = resolve_rows(batch_input.rows, resolver)
    columns = build_columns(batch_input, echo_input=echo_input)
    rows = [build_row(item, result, echo_input=echo_input) for item, result in resolved]

    counts: dict[str, int] = {}
    for _, result in resolved:
        status: LookupStatus = result.status
        counts[status] = counts.get(status, 0) + 1

    report = BatchReport(
        input_path=input_path,
        output_path=destination,
        sheet=batch_input.sheet,
        row_count=len(rows),
        counts=counts,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        user=user,
        codebook_version=codebook_version,
        sources=tuple(source.source for source in resolver.sources),
        notes=batch_input.notes,
    )
    write_workbook(destination, columns, rows, run_metadata=report.run_metadata())
    LOGGER.info("batch finished: %s", report.summary())
    return report
