"""The usage ledger as an Excel workbook: every model call, its tokens and what it cost.

``python -m core.classify --usage-xlsx [PATH]`` writes it. Three sheets:

* **Summary** - the period, the totals, and the same figures by user, by model, by codebook
  and by day (calls recorded before users were kept are ``unknown``'s);
* **Calls** - one row per model call, as an Excel table (filters; select a column to sum it);
* **Prices** - the prices the costs were computed with, and where they came from.

Input the provider served from its prompt cache is priced at the cached rate, from the count
it reported with the call. What it cannot show, and says so on the Summary sheet:

* **production**: on Vercel the ledger is off (only ``/tmp`` is writable), so the deployed
  site's calls are in no ledger; the provider's usage page has them;
* **calls without a cached count** - recorded before the ledger kept one, or answered by a
  provider that does not report it - are priced as all uncached, an upper bound, and counted.

Costs are computed here rather than as Excel formulas, so the file reads the same in any
viewer. A model missing from :data:`PRICES` gets empty cost cells and is named on the Summary
sheet - never a guessed price.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from core.classify.budget import UsageRecord


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens, the provider's standard tier."""

    input: float
    cached_input: float
    output: float


#: Standard-tier prices from :data:`PRICES_SOURCE` on :data:`PRICES_CHECKED`. Update them
#: when the page changes; a model missing here gets empty cost cells, never a guess.
PRICES: Final[dict[str, Price]] = {
    "gpt-5.6-luna": Price(input=0.20, cached_input=0.02, output=1.20),
    "gpt-6-luna": Price(input=0.10, cached_input=0.01, output=0.50),
}
PRICES_SOURCE: Final[str] = "https://developers.openai.com/api/docs/pricing"
PRICES_CHECKED: Final[date] = date(2026, 9, 23)

SUMMARY_SHEET: Final[str] = "Summary"
CALLS_SHEET: Final[str] = "Calls"
PRICES_SHEET: Final[str] = "Prices"

CALL_COLUMNS: Final[tuple[str, ...]] = (
    "#",
    "Time (UTC)",
    "User",
    "Model",
    "Codebook",
    "Input tokens",
    "Cached input tokens",
    "Output tokens",
    "Total tokens",
    "Input cost (USD)",
    "Output cost (USD)",
    "Cost (USD)",
)
_GROUP_COLUMNS: Final[tuple[str, ...]] = (
    "Calls",
    "Input tokens",
    "Output tokens",
    "Cost (USD)",
    "Cost per call (USD)",
)
_PRICE_COLUMNS: Final[tuple[str, ...]] = (
    "Model",
    "Input (USD per 1M tokens)",
    "Cached input (USD per 1M tokens)",
    "Output (USD per 1M tokens)",
    "Source",
    "Checked",
)

_TOKENS: Final[str] = "#,##0"
_COST_CALL: Final[str] = "0.000000"
_COST_SUM: Final[str] = "0.0000"
_PRICE: Final[str] = "0.00"
_DATE_FORMAT: Final[str] = "yyyy-mm-dd"
_DATETIME_FORMAT: Final[str] = "yyyy-mm-dd hh:mm:ss"

_HEADER_FILL: Final[PatternFill] = PatternFill("solid", fgColor="DDEBF7")
_BOLD: Final[Font] = Font(bold=True)
_TITLE: Final[Font] = Font(bold=True, size=14)
_BLOCK_TITLE: Final[Font] = Font(bold=True, size=12)
_NOTE: Final[Font] = Font(italic=True, color="595959")
_WARNING: Final[Font] = Font(bold=True, color="C00000")


def price_for(model: str) -> Price | None:
    """The price of ``model``, or of the model a dated snapshot belongs to.

    ``gpt-6-luna-2026-08-01`` is priced as ``gpt-6-luna``; anything else unknown is ``None``.
    """
    if model in PRICES:
        return PRICES[model]
    for known in sorted(PRICES, key=len, reverse=True):
        if model.startswith(f"{known}-"):
            return PRICES[known]
    return None


@dataclass(frozen=True, slots=True)
class CallCost:
    """One call and what it cost; the costs are ``None`` when the model has no known price.

    ``exact`` is False when the call has no cached count, so its input was priced as if none
    of it had been cached: an upper bound rather than the bill.
    """

    record: UsageRecord
    input_cost: float | None
    output_cost: float | None
    exact: bool = True

    @property
    def cost(self) -> float | None:
        if self.input_cost is None or self.output_cost is None:
            return None
        return self.input_cost + self.output_cost


def cost_of(record: UsageRecord) -> CallCost:
    """Price one call at :data:`PRICES`, its cached input at the cached rate.

    With no cached count every input token is priced at the uncached rate - the direction
    that can only overstate the bill - and the call is marked inexact.
    """
    cached = record.cached_prompt_tokens
    exact = cached is not None
    price = price_for(record.model)
    if price is None:
        return CallCost(record, None, None, exact=exact)
    cached = min(max(cached or 0, 0), record.prompt_tokens)
    uncached = record.prompt_tokens - cached
    return CallCost(
        record,
        input_cost=(uncached * price.input + cached * price.cached_input) / 1_000_000,
        output_cost=record.completion_tokens * price.output / 1_000_000,
        exact=exact,
    )


@dataclass(frozen=True, slots=True)
class UsageReport:
    """What the workbook holds, for the command's one-line answer."""

    calls: int
    #: Of the priced calls only; see ``unpriced_models`` for the rest.
    cost: float
    unpriced_models: tuple[str, ...]
    #: Calls with no cached count, priced as all uncached; 0 means the cost is exact.
    inexact_calls: int = 0


@dataclass(slots=True)
class _Group:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    unpriced: int = 0

    def add(self, item: CallCost) -> None:
        self.calls += 1
        self.prompt_tokens += item.record.prompt_tokens
        self.completion_tokens += item.record.completion_tokens
        if item.cost is None:
            self.unpriced += 1
        else:
            self.cost += item.cost


def write_usage_workbook(
    records: Iterable[UsageRecord],
    path: Path,
    *,
    source: str = "the usage ledger",
    exported_at: datetime | None = None,
) -> UsageReport:
    """Write the workbook to ``path``, creating its folder, and say what it holds."""
    items = [cost_of(record) for record in sorted(records, key=lambda record: record.at)]
    workbook = Workbook()
    summary = workbook.active
    summary.title = SUMMARY_SHEET
    report = _write_summary(
        summary, items, source=source, exported_at=exported_at or datetime.now(UTC)
    )
    _write_calls(workbook.create_sheet(CALLS_SHEET), items)
    _write_prices(workbook.create_sheet(PRICES_SHEET))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return report


def _naive_utc(moment: datetime) -> datetime:
    """Excel has no time zones: UTC written naive, as the result sheet does."""
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _write_summary(
    sheet: Worksheet, items: Sequence[CallCost], *, source: str, exported_at: datetime
) -> UsageReport:
    sheet["A1"] = "Model usage and cost"
    sheet["A1"].font = _TITLE
    notes = (
        f"Every model call recorded in {source}: the calls made from this machine (the local "
        "server, the command line, the golden run).",
        "Production (the site on Vercel) keeps no ledger without DATABASE_URL, so its calls "
        "are NOT here (the provider's usage page has them); with it, this is the shared "
        "ledger of every device and user.",
        "Costs use the provider's standard prices (sheet Prices), cached input at the cached "
        "price from the count the provider reported. A call recorded without that count is "
        "priced as all uncached - an upper bound - and counted below.",
        "User is who the call was made for: the signed-in name on the web, the OS account on "
        "the command line; calls recorded before users were kept are 'unknown'.",
        f"Exported {_naive_utc(exported_at):%Y-%m-%d %H:%M} (UTC). All times are UTC.",
    )
    for row, note in enumerate(notes, start=2):
        sheet.cell(row=row, column=1, value=note).font = _NOTE

    total_cost = sum(item.cost for item in items if item.cost is not None)
    unpriced = tuple(sorted({item.record.model for item in items if item.cost is None}))
    inexact = sum(1 for item in items if not item.exact)
    facts: list[tuple[str, object, str | None]] = [("Calls", len(items), _TOKENS)]
    if items:
        first, last = _naive_utc(items[0].record.at), _naive_utc(items[-1].record.at)
        facts = [
            ("Period (UTC)", f"{first:%Y-%m-%d %H:%M} - {last:%Y-%m-%d %H:%M}", None),
            ("Calls", len(items), _TOKENS),
            ("Input tokens", sum(item.record.prompt_tokens for item in items), _TOKENS),
            (
                "Cached input tokens",
                sum(item.record.cached_prompt_tokens or 0 for item in items),
                _TOKENS,
            ),
            ("Output tokens", sum(item.record.completion_tokens for item in items), _TOKENS),
            ("Cost (USD)", total_cost, _COST_SUM),
        ]
    row = len(notes) + 3
    for label, value, number_format in facts:
        sheet.cell(row=row, column=1, value=label).font = _BOLD
        _put(sheet, row, 2, value, number_format)
        row += 1
    if unpriced:
        warning = f"No price known for {', '.join(unpriced)}: their cost cells are empty."
        sheet.cell(row=row, column=1, value=warning).font = _WARNING
        row += 1
    if inexact:
        warning = (
            f"{inexact} call(s) have no cached-input count and are priced as all uncached, so "
            "the cost is an upper bound."
        )
        sheet.cell(row=row, column=1, value=warning).font = _WARNING
        row += 1

    blocks: tuple[tuple[str, str, Callable[[CallCost], str]], ...] = (
        ("By user", "User", lambda item: item.record.user),
        ("By model", "Model", lambda item: item.record.model),
        ("By codebook", "Codebook", lambda item: item.record.kind or "-"),
        ("By day", "Date (UTC)", lambda item: f"{_naive_utc(item.record.at):%Y-%m-%d}"),
    )
    row += 1
    for title, first_column, key in blocks:
        row = _write_group_block(sheet, row, title, first_column, _groups(items, key))
    _set_widths(sheet, (24, 26, 14, 14, 12, 18, 30))
    return UsageReport(
        calls=len(items), cost=total_cost, unpriced_models=unpriced, inexact_calls=inexact
    )


def _groups(items: Sequence[CallCost], key: Callable[[CallCost], str]) -> list[tuple[str, _Group]]:
    groups: dict[str, _Group] = defaultdict(_Group)
    for item in items:
        groups[key(item)].add(item)
    return sorted(groups.items())


def _write_group_block(
    sheet: Worksheet, row: int, title: str, first_column: str, groups: list[tuple[str, _Group]]
) -> int:
    sheet.cell(row=row, column=1, value=title).font = _BLOCK_TITLE
    row += 1
    _header(sheet, row, (first_column, *_GROUP_COLUMNS))
    for name, group in groups:
        row += 1
        priced = group.calls - group.unpriced
        values = (
            name,
            group.calls,
            group.prompt_tokens,
            group.completion_tokens,
            group.cost if priced else None,
            group.cost / priced if priced else None,
        )
        formats = (None, _TOKENS, _TOKENS, _TOKENS, _COST_SUM, _COST_CALL)
        for column, (value, number_format) in enumerate(zip(values, formats, strict=True), 1):
            _put(sheet, row, column, value, number_format)
        if group.unpriced:
            sheet.cell(row=row, column=7, value=f"{group.unpriced} call(s) without a known price")
    return row + 2


def _write_calls(sheet: Worksheet, items: Sequence[CallCost]) -> None:
    _header(sheet, 1, CALL_COLUMNS)
    formats = (
        None,
        _DATETIME_FORMAT,
        None,
        None,
        None,
        _TOKENS,
        _TOKENS,
        _TOKENS,
        _TOKENS,
        _COST_CALL,
        _COST_CALL,
        _COST_CALL,
    )
    for number, item in enumerate(items, start=1):
        record = item.record
        values = (
            number,
            _naive_utc(record.at),
            record.user,
            record.model,
            record.kind,
            record.prompt_tokens,
            record.cached_prompt_tokens,
            record.completion_tokens,
            record.total_tokens,
            item.input_cost,
            item.output_cost,
            item.cost,
        )
        for column, (value, number_format) in enumerate(zip(values, formats, strict=True), 1):
            _put(sheet, number + 1, column, value, number_format)
    if items:  # Excel refuses a table without a data row
        last = f"{get_column_letter(len(CALL_COLUMNS))}{len(items) + 1}"
        table = Table(displayName="UsageCalls", ref=f"A1:{last}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        sheet.add_table(table)
    sheet.freeze_panes = "A2"
    _set_widths(sheet, (6, 20, 18, 16, 11, 13, 14, 14, 13, 16, 17, 12))


def _write_prices(sheet: Worksheet) -> None:
    _header(sheet, 1, _PRICE_COLUMNS)
    formats = (None, _PRICE, _PRICE, _PRICE, None, _DATE_FORMAT)
    for row, (model, price) in enumerate(sorted(PRICES.items()), start=2):
        values = (
            model,
            price.input,
            price.cached_input,
            price.output,
            PRICES_SOURCE,
            PRICES_CHECKED,
        )
        for column, (value, number_format) in enumerate(zip(values, formats, strict=True), 1):
            _put(sheet, row, column, value, number_format)
    _set_widths(sheet, (16, 16, 18, 16, 48, 12))


def _put(sheet: Worksheet, row: int, column: int, value: object, number_format: str | None) -> None:
    cell = sheet.cell(row=row, column=column, value=value)
    if number_format:
        cell.number_format = number_format


def _header(sheet: Worksheet, row: int, labels: Sequence[str]) -> None:
    for column, label in enumerate(labels, start=1):
        cell = sheet.cell(row=row, column=column, value=label)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def _set_widths(sheet: Worksheet, widths: Sequence[int]) -> None:
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width


__all__ = [
    "CALLS_SHEET",
    "CALL_COLUMNS",
    "PRICES",
    "PRICES_CHECKED",
    "PRICES_SOURCE",
    "PRICES_SHEET",
    "SUMMARY_SHEET",
    "CallCost",
    "Price",
    "UsageReport",
    "cost_of",
    "price_for",
    "write_usage_workbook",
]
