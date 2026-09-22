"""Inspect and score the candidate pre-filter: ``python -m core.classify [DESCRIPTION]``.

Two modes, neither of which calls a model or needs an API key:

    python -m core.classify "captive funding vehicle of a bank"   # show both shortlists
    python -m core.classify --golden                              # recall over the golden set
    python -m core.classify "..." --estimate                      # prompt size before paying
    python -m core.classify --golden-capture                      # re-record the register answers

A golden case with an ISIN (a real issuer, roadmap E8) is scored the way the pipeline works:
its description plus the issuer's register fact sheet, replayed from the GLEIF/OpenFIGI answers
recorded in tests/golden/identity.json. ``--golden-capture`` asks the live registers (throttled,
a few minutes) and rewrites that file; it is the only mode that uses the network.

Recall is the pre-filter's grade: the share of golden cases whose correct code made it onto
the shortlist at all. A code the filter never offers is one the classifier can never return,
so this number is the ceiling on everything downstream.

Exit codes: 0 fine, 1 a golden case's correct code was missed, 2 the codebooks or the golden
file could not be loaded.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date

from config.settings import get_settings
from core.classify.candidates import (
    DEFAULT_LIMIT,
    EsaCandidateFilter,
    NaceCandidateFilter,
)
from core.classify.golden import (
    ESA,
    NACE,
    GoldenCase,
    GoldenError,
    counts,
    load_golden,
    score_recall,
)
from core.classify.golden_fixtures import capture, load_answers, replaying_identifier
from core.classify.models import CandidateSet
from core.classify.prompts import build_prompt
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.models import CodebookSet

EXIT_OK = 0
EXIT_MISSES = 1
EXIT_LOAD_FAILED = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core.classify",
        description="Show or score the NACE/ESA candidate pre-filter (no model call).",
    )
    parser.add_argument("description", nargs="*", help="activity description to shortlist for")
    parser.add_argument(
        "--golden", action="store_true", help="score recall over tests/golden/cases.json"
    )
    parser.add_argument(
        "--golden-capture",
        action="store_true",
        help="record the GLEIF/OpenFIGI answers for the golden ISINs (uses the network)",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"shortlist size (default {DEFAULT_LIMIT})"
    )
    parser.add_argument(
        "--resident",
        action="store_true",
        help="shortlist resident ESA sectors instead of the rest-of-world block",
    )
    parser.add_argument("--verbose", action="store_true", help="show scores and reasons")
    parser.add_argument(
        "--usage",
        action="store_true",
        help="report the configured spending limits and what has been spent today",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="report the prompt size the classifier would send (no model call)",
    )
    return parser


def _load(settings) -> CodebookSet:
    codebooks, _ = load_and_check(settings, strict=False)
    return codebooks


def _print_set(candidate_set: CandidateSet, *, verbose: bool) -> None:
    print(f"\n{candidate_set.describe()}")
    for index, candidate in enumerate(candidate_set.candidates, start=1):
        line = f"  {index:>2}. {candidate.code:<8} CTS {candidate.cts_id:<5} {candidate.label}"
        print(line)
        if verbose:
            print(f"      score {candidate.score:.2f}  {'; '.join(candidate.reasons) or '-'}")


def _print_usage(settings) -> None:
    """Show the limits and today's spend, so cost is answerable from the tool."""
    from core.classify.budget import build_budget, build_ledger

    budget = build_budget(settings)
    ledger = build_ledger(settings.llm_usage_path)
    print(budget.describe())
    print(f"ledger: {settings.llm_usage_path or '(disabled)'}")
    if not getattr(ledger, "can_track", False):
        print("WARNING: usage cannot be recorded, so the daily budget is NOT enforceable.")
        print("         Every model call will be refused until this is fixed, or until")
        print("         LLM_DAILY_TOKEN_BUDGET=0 says the cap is not wanted.")
        return
    today = ledger.today()
    print(f"today : {today.describe()}")
    if budget.daily_token_budget > 0:
        left = max(0, budget.daily_token_budget - today.total_tokens)
        print(f"left  : {left:,} tokens (~{left // 3400} more uncached issuers)")


def _print_estimate(nace: CandidateSet, esa: CandidateSet, description: str) -> None:
    """Report what one issuer would cost to classify, before any key is configured.

    Two calls per issuer, one per codebook, cached afterwards. The numbers are an estimate
    from character counts; the real figures come back in the provider's usage fields.
    """
    total = 0
    print()
    print("prompt size (no model call made):")
    for candidates in (nace, esa):
        prompt = build_prompt(candidates, issuer_name=None, description=description)
        total += prompt.estimated_tokens
        print(f"  {prompt.describe()}")
    print(f"  both codebooks: ~{total} input tokens per issuer, then cached")


def _golden_texts(cases: Sequence[GoldenCase], settings) -> dict[str, str]:
    """What each case is scored on: the description, plus the fact sheet for a real issuer."""
    answers = load_answers()
    identifier = replaying_identifier(settings, answers) if answers else None
    if identifier is None and any(case.real for case in cases):
        print(
            "WARNING: no recorded register answers (tests/golden/identity.json) - real issuers "
            "are scored on their description only; run --golden-capture"
        )
    texts: dict[str, str] = {}
    for case in cases:
        parts = [case.description]
        if identifier is not None and case.isin:
            parts.append(identifier.identify(case.isin).fact_sheet())
        texts[case.id] = "\n\n".join(part for part in parts if part)
    return texts


def _run_golden(codebooks: CodebookSet, limit: int, resident: bool, settings) -> int:
    cases = load_golden()
    stats = counts(cases)
    print(
        f"golden set: {stats['cases']} case(s), {stats['verified']} verified, "
        f"{stats['with_nace']} with NACE, {stats['with_esa']} with ESA"
    )
    if stats["verified"] == 0:
        print(
            "WARNING: no case is verified yet - these figures exercise the filter, not its quality"
        )

    texts = _golden_texts(cases, settings)
    nace_filter = NaceCandidateFilter(codebooks)
    esa_filter = EsaCandidateFilter(codebooks, resident=resident)
    misses = 0
    groups = (
        ("real issuers (provisional unless verified)", [case for case in cases if case.real]),
        ("fictional trap cases", [case for case in cases if not case.real]),
    )
    for label, group in groups:
        if not group:
            continue
        for kind, shortlist in ((NACE, nace_filter.shortlist), (ESA, esa_filter.shortlist)):
            lists = [(case, shortlist(texts[case.id], limit=limit)) for case in group]
            report = score_recall(group, lists, kind)
            print(f"\n--- {kind}, {label} (shortlist of {limit}) ---")
            print(report.summary())
            misses += len(report.misses())
    return EXIT_MISSES if misses else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s"
    )
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):  # never crash on a console that cannot show Czech letters
        stdout.reconfigure(errors="backslashreplace")

    if args.golden_capture:
        try:
            recorded = capture(load_golden(), settings, captured_on=date.today())
        except GoldenError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_LOAD_FAILED
        print(f"recorded {recorded} register answer(s) in tests/golden/identity.json")
        return EXIT_OK

    try:
        codebooks = _load(settings)
    except (CodebookError, OSError) as exc:
        print(f"error: codebooks could not be loaded: {exc}", file=sys.stderr)
        return EXIT_LOAD_FAILED

    if args.usage:
        _print_usage(settings)
        return EXIT_OK

    if args.golden:
        try:
            return _run_golden(codebooks, args.limit, args.resident, settings)
        except GoldenError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_LOAD_FAILED

    description = " ".join(args.description).strip()
    if not description:
        print("error: give a description to shortlist for, or pass --golden", file=sys.stderr)
        return EXIT_LOAD_FAILED

    print(f"codebooks {codebooks.version.id}")
    nace = NaceCandidateFilter(codebooks).shortlist(description, limit=args.limit)
    esa = EsaCandidateFilter(codebooks, resident=args.resident).shortlist(
        description, limit=args.limit
    )
    _print_set(nace, verbose=args.verbose)
    _print_set(esa, verbose=args.verbose)
    if args.estimate:
        _print_estimate(nace, esa, description)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
