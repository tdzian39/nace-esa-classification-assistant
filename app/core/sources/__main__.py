"""Tool 2 from the command line: ``python -m core.sources ICO_OR_NAME [...]``.

Looks each identifier up through DWS, falling back to ARES, and prints the RES half and the
OR half side by side - the same content step 3 will write into an xlsx.

Examples::

    python -m core.sources 49240901
    python -m core.sources --file icos.txt --json
    python -m core.sources 49240901 --no-dws          # force the public fallback

Exit codes: 0 every identifier resolved, 1 at least one was not found / ambiguous / invalid,
2 nothing could be asked at all (no source configured, or every source failed).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from config.settings import Settings, get_settings
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.export.columns import json_row, record_row
from core.sources.base import SubjectRecord
from core.sources.resolver import LookupResult, build_default_resolver

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_NO_SOURCE = 2

LOGGER = logging.getLogger("core.sources")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core.sources",
        description="Look Czech subjects up in DWS with an ARES fallback (Tool 2, CLI form).",
    )
    parser.add_argument("identifiers", nargs="*", help="IČO or company name; repeatable")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="read identifiers from a text file, one per line (# starts a comment)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--no-dws", action="store_true", help="skip DWS even when configured")
    parser.add_argument("--no-ares", action="store_true", help="skip the ARES fallback")
    parser.add_argument("--user", default=None, help="requesting user recorded in the audit log")
    return parser


def _configure_logging(level_name: str) -> None:
    """Root logging on stderr, so stdout carries only the result."""
    level = logging.getLevelName(level_name.strip().upper())
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _read_identifiers(args: argparse.Namespace) -> list[str]:
    """Positional identifiers followed by the ones in ``--file``."""
    identifiers = [item.strip() for item in args.identifiers if item.strip()]
    if args.file is not None:
        for line in args.file.read_text(encoding="utf-8-sig").splitlines():
            text = line.split("#", 1)[0].strip()
            if text:
                identifiers.append(text)
    return identifiers


def _codebook_version(settings: Settings) -> str | None:
    """The codebook version stamped onto every row, or ``None`` when the files are absent.

    A missing codebook must not block a lookup: the codebooks are needed to emit CTS IDs
    (build step 5), not to read a register.
    """
    try:
        codebooks, _ = load_and_check(settings, strict=False)
    except (CodebookError, OSError) as exc:
        LOGGER.warning("codebooks unavailable, rows will carry no codebook version: %s", exc)
        return None
    return codebooks.version.id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _record_as_dict(record: SubjectRecord) -> dict[str, object]:
    """One output row, in the shape defined once in :mod:`core.export.columns`.

    The batch sheet and this CLI must never drift apart, so neither builds its own row.
    """
    return json_row(record_row(record))


def _result_as_dict(result: LookupResult) -> dict[str, object]:
    return {
        "query": result.query,
        "status": result.status,
        "ico": result.ico,
        "resolved_at": _iso(result.resolved_at),
        "messages": list(result.messages),
        "sources_tried": list(result.sources_tried),
        "candidates": [
            {"ico": item.ico, "name": item.name, "source": item.source}
            for item in result.candidates
        ],
        "record": _record_as_dict(result.record) if result.record else None,
    }


def _print_text(result: LookupResult, stream: object = None) -> None:
    """Human-readable block for one lookup."""
    out = stream or sys.stdout
    print(f"=== {result.query} -> {result.status}", file=out)
    if result.record is not None:
        row = _record_as_dict(result.record)
        width = max(len(key) for key in row)
        for key, value in row.items():
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value) or "-"
            print(f"  {key:<{width}}  {value if value is not None else '-'}", file=out)
    for candidate in result.candidates:
        print(f"  candidate: {candidate.ico}  {candidate.name or '-'}", file=out)
    for message in result.messages:
        print(f"  note: {message}", file=out)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    _configure_logging(settings.log_level)
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):  # never crash on a console that cannot show Czech letters
        stdout.reconfigure(errors="backslashreplace")

    identifiers = _read_identifiers(args)
    if not identifiers:
        print("error: no identifiers given (pass them as arguments or via --file)", file=sys.stderr)
        return EXIT_INCOMPLETE

    resolver = build_default_resolver(
        settings,
        codebook_version=_codebook_version(settings),
        user=args.user,
        use_dws=not args.no_dws,
        use_ares=not args.no_ares,
    )
    if not resolver.sources:
        print(
            "error: no source enabled (configure DWS_DSN, or leave ARES enabled)",
            file=sys.stderr,
        )
        return EXIT_NO_SOURCE

    try:
        results = resolver.resolve_many(identifiers)
    finally:
        resolver.close()

    if args.json:
        # ensure_ascii so a cp1250 Windows console cannot mangle Czech text (same as
        # ``python -m core.codebooks``); the escapes are lossless for any JSON reader.
        print(
            json.dumps([_result_as_dict(result) for result in results], indent=2, ensure_ascii=True)
        )
    else:
        for result in results:
            _print_text(result)

    if all(result.status == "error" for result in results):
        return EXIT_NO_SOURCE
    return EXIT_OK if all(result.found for result in results) else EXIT_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
