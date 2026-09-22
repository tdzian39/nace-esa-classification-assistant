"""Command line check of the codebooks: ``python -m core.codebooks [--dir DIR] [--no-strict] [--json]``.

Exit codes: 0 when the consistency report has no errors, 1 when it has errors, 2 when the
files themselves cannot be loaded (missing file, unreadable workbook, wrong columns).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from config.settings import get_settings
from core.codebooks.consistency import ConsistencyReport
from core.codebooks.errors import CodebookConsistencyError, CodebookFileError, CodebookSchemaError
from core.codebooks.loaders import load_and_check
from core.codebooks.models import CodebookSet

EXIT_OK = 0
EXIT_INCONSISTENT = 1
EXIT_LOAD_FAILED = 2


def _build_parser() -> argparse.ArgumentParser:
    """Argument parser for ``--dir``, ``--no-strict`` and ``--json``."""
    parser = argparse.ArgumentParser(
        prog="python -m core.codebooks",
        description="Load the four bootstrap xlsx codebooks and run the startup consistency check.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="directory with the codebook files (default: CODEBOOK_DIR from the settings)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="return the report instead of raising on error-level findings (exit code is unchanged)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a JSON document with the version and the findings instead of text",
    )
    return parser


def _configure_logging(level_name: str) -> None:
    """Configure root logging on stderr at ``level_name``; unknown names fall back to INFO."""
    level = logging.getLevelName(level_name.strip().upper())
    known = isinstance(level, int)
    logging.basicConfig(
        level=level if known else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not known:
        logging.getLogger(__name__).warning(
            "unknown LOG_LEVEL %r, falling back to INFO", level_name
        )


def _json_document(codebooks: CodebookSet | None, report: ConsistencyReport) -> dict[str, object]:
    """JSON-serializable document with the version (when loaded), counts and sorted findings."""
    version: dict[str, object] | None = None
    if codebooks is not None:
        version = {
            "id": codebooks.version.id,
            "label": codebooks.version.label,
            "loaded_at": codebooks.version.loaded_at.isoformat(),
            "files": [
                {
                    **asdict(file),
                    "path": str(file.path),
                    "modified_at": file.modified_at.isoformat(),
                }
                for file in codebooks.version.files
            ],
        }
    return {
        "ok": report.ok,
        "version": version,
        "summary": codebooks.describe() if codebooks is not None else None,
        "counts": report.counts,
        "findings": [asdict(finding) for finding in report.sorted()],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    _configure_logging(settings.log_level)
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):  # never crash on a console that cannot show Czech letters
        stdout.reconfigure(errors="backslashreplace")

    codebooks: CodebookSet | None
    try:
        codebooks, report = load_and_check(
            settings, codebook_dir=args.dir, strict=not args.no_strict
        )
    except CodebookConsistencyError as exc:
        codebooks, report = exc.codebooks, exc.report
    except (CodebookFileError, CodebookSchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_LOAD_FAILED

    if args.json:
        print(json.dumps(_json_document(codebooks, report), indent=2, ensure_ascii=True))
    else:
        if codebooks is not None:
            print(codebooks.describe())
        print(report.summary())
    return EXIT_OK if report.ok else EXIT_INCONSISTENT


if __name__ == "__main__":
    raise SystemExit(main())
