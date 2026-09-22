"""Tool 2 in batch form: ``python -m core.batch INPUT.xlsx [-o OUTPUT.xlsx]``.

Reads a messy input sheet (see :mod:`core.batch.reader`), resolves every row through DWS
with the ARES fallback, and writes one result row per input row.

Examples::

    python -m core.batch klienti.xlsx
    python -m core.batch klienti.xlsx -o kontrola_2026_09.xlsx --sheet "Flagged"
    python -m core.batch klienti.xlsx --no-dws --no-echo-input

Exit codes: 0 every row resolved, 1 some rows need a human (not found, ambiguous, invalid
identifier), 2 the batch could not run at all (unreadable input, no source configured, or
every row failed because no source could answer).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from config.settings import Settings, get_settings
from core.audit import current_user
from core.batch.reader import BatchInputError
from core.batch.runner import run_batch
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.sources.resolver import build_default_resolver

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_FAILED = 2

LOGGER = logging.getLogger("core.batch")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core.batch",
        description="Resolve a sheet of Czech companies into RES/OR rows (Tool 2, batch form).",
    )
    parser.add_argument("input", type=Path, help="input .xlsx with IČOs and/or company names")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="output .xlsx (default: <input stem>_lookup.xlsx beside the input)",
    )
    parser.add_argument("--sheet", default=None, help="input worksheet (default: the first)")
    parser.add_argument(
        "--no-echo-input",
        action="store_true",
        help="do not copy the input columns into the result sheet",
    )
    parser.add_argument("--no-dws", action="store_true", help="skip DWS even when configured")
    parser.add_argument("--no-ares", action="store_true", help="skip the ARES fallback")
    parser.add_argument("--user", default=None, help="requesting user recorded in the audit log")
    return parser


def _configure_logging(level_name: str) -> None:
    """Root logging on stderr, so stdout carries only the summary."""
    level = logging.getLevelName(level_name.strip().upper())
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _codebook_version(settings: Settings) -> str | None:
    """The codebook version stamped onto every row, or ``None`` when the files are absent.

    Missing codebooks must not block a batch: they are needed to emit CTS IDs (step 5), not
    to read a register.
    """
    try:
        codebooks, _ = load_and_check(settings, strict=False)
    except (CodebookError, OSError) as exc:
        LOGGER.warning("codebooks unavailable, rows will carry no codebook version: %s", exc)
        return None
    return codebooks.version.id


def main(argv: Sequence[str] | None = None) -> int:
    """Run the batch CLI; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    _configure_logging(settings.log_level)
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):  # never crash on a console that cannot show Czech letters
        stdout.reconfigure(errors="backslashreplace")

    user = args.user or current_user(settings)
    resolver = build_default_resolver(
        settings,
        codebook_version=None,
        user=user,
        use_dws=not args.no_dws,
        use_ares=not args.no_ares,
    )
    if not resolver.sources:
        print(
            "error: no source enabled (configure DWS_DSN, or leave ARES enabled)",
            file=sys.stderr,
        )
        return EXIT_FAILED

    try:
        report = run_batch(
            args.input,
            args.out,
            resolver=resolver,
            user=user,
            codebook_version=_codebook_version(settings),
            sheet=args.sheet,
            echo_input=not args.no_echo_input,
        )
    except BatchInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        resolver.close()

    print(report.summary())
    for note in report.notes:
        print(f"  note: {note}")
    if report.unresolved:
        print(f"  {report.unresolved} row(s) need review; see the status column")
    if report.all_failed:
        return EXIT_FAILED
    return EXIT_OK if report.unresolved == 0 else EXIT_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
