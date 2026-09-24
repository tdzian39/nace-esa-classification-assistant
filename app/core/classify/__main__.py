"""Inspect and score the candidate pre-filter: ``python -m core.classify [DESCRIPTION]``.

These modes call no model and need no API key:

    python -m core.classify "captive funding vehicle of a bank"   # show both shortlists
    python -m core.classify --golden                              # recall over the golden set
    python -m core.classify "..." --estimate                      # prompt size before paying
    python -m core.classify --golden-capture                      # re-record the register answers
    python -m core.classify --usage-xlsx [PATH]                   # the usage ledger as Excel
    python -m core.classify --hash-password                       # the hash for APP_PASSWORD_HASH

``--usage-xlsx`` writes every recorded model call with its tokens and cost (Summary, Calls and
Prices sheets) to PATH, by default next to the ledger (``LLM_USAGE_PATH`` with ``.xlsx``). It
needs neither the codebooks nor a key; see core/classify/usage_report.py for what it cannot show.

``--hash-password`` asks for a password twice (it is not echoed) and prints the hash to put
in ``APP_PASSWORD_HASH``, the one password everybody signs in with; see core/auth.py.

One does, and refuses to start without a configured model (LLM_API_KEY):

    python -m core.classify --golden --model   # the model's top-1 next to the rules', tokens used

Its calls are recorded in the usage ledger against the OS account (or ``LOOKUP_USER``).

It sends every golden case to the configured endpoint - two calls per case, about 3,400
input tokens per issuer - so it costs money; answers are cached like any other lookup.

A golden case with an ISIN (a real issuer, roadmap E8) is scored the way the pipeline works:
its description plus the issuer's register fact sheet, replayed from the GLEIF/OpenFIGI answers
recorded in tests/golden/identity.json. ``--golden-capture`` asks the live registers (throttled,
a few minutes) and rewrites that file; it is the only mode that uses the network.

Recall is the pre-filter's grade: the share of golden cases whose correct code made it onto
the shortlist at all. A code the filter never offers is one the classifier can never return,
so this number is the ceiling on everything downstream.

Exit codes: 0 fine, 1 a golden case's correct code was missed, 2 the codebooks, the golden
file or the usage ledger could not be loaded (or the workbook not written), 3 ``--model``
without a model that can be called.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from config.settings import get_settings
from core.audit import current_user
from core.auth import hash_password
from core.classify.budget import spending_as
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
from core.classify.prompts import Prompt, build_prompt
from core.classify.provider import LlmProvider, LlmResponse, NullLlmProvider, build_provider
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check
from core.codebooks.models import CodebookSet

EXIT_OK = 0
EXIT_MISSES = 1
EXIT_LOAD_FAILED = 2
EXIT_NO_MODEL = 3


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
        "--hash-password",
        action="store_true",
        help="ask for the shared sign-in password and print its hash for APP_PASSWORD_HASH",
    )
    parser.add_argument(
        "--usage-xlsx",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="write every recorded model call with its tokens and cost to an Excel workbook "
        "(default: next to the usage ledger)",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="report the prompt size the classifier would send (no model call)",
    )
    parser.add_argument(
        "--model",
        action="store_true",
        help="with --golden: send every case to the configured model (costs money, needs "
        "LLM_API_KEY) and print its top-1 next to the rules' and the tokens used",
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


def _export_usage(settings, target: str) -> int:
    """Write the usage ledger as a workbook; loads no codebooks and calls no model."""
    from core.classify.budget import SqliteLedger
    from core.classify.usage_report import PRICES_CHECKED, write_usage_workbook

    ledger_path = settings.llm_usage_path
    if ledger_path is None:
        print(
            "error: usage recording is off (LLM_USAGE_PATH is empty), so there is nothing to "
            "export. On Vercel that is by design; the provider's usage page has those calls.",
            file=sys.stderr,
        )
        return EXIT_LOAD_FAILED
    if not ledger_path.exists():
        print(f"nothing recorded yet: {ledger_path} does not exist, no model call was made here")
        return EXIT_OK
    ledger = SqliteLedger(ledger_path)
    if not ledger.usable:
        print(f"error: the usage ledger {ledger_path} cannot be opened", file=sys.stderr)
        return EXIT_LOAD_FAILED
    out = Path(target) if target else ledger_path.with_suffix(".xlsx")
    try:
        report = write_usage_workbook(ledger.records(), out, source=ledger_path.name)
    except sqlite3.Error as exc:
        print(f"error: the usage ledger {ledger_path} cannot be read: {exc}", file=sys.stderr)
        return EXIT_LOAD_FAILED
    except OSError as exc:
        print(f"error: could not write {out}: {exc} (is it open in Excel?)", file=sys.stderr)
        return EXIT_LOAD_FAILED
    print(
        f"wrote {out.resolve()}: {report.calls} call(s), ${report.cost:.4f} at the prices "
        f"checked on {PRICES_CHECKED:%d %b %Y}"
    )
    if report.inexact_calls:
        print(
            f"{report.inexact_calls} call(s) were recorded without a cached-input count and are "
            "priced as all uncached, so the total is an upper bound"
        )
    if report.unpriced_models:
        print(
            f"no price for {', '.join(report.unpriced_models)}: their cost cells are empty; add "
            "the price to PRICES in core/classify/usage_report.py"
        )
    return EXIT_OK


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


class _CountingProvider:
    """Adds up what the provider reports it used, for the ``--golden --model`` summary."""

    def __init__(self, inner: LlmProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(self, prompt: Prompt) -> LlmResponse:
        response = self._inner.complete(prompt)
        self.calls += 1
        self.prompt_tokens += response.prompt_tokens or 0
        self.completion_tokens += response.completion_tokens or 0
        return response


def _model_refusal(settings) -> str | None:
    """Why ``--golden --model`` must not start, or ``None`` when a model can be called."""
    from core.classify.budget import build_ledger

    if isinstance(build_provider(settings), NullLlmProvider):
        return (
            "the golden run through the model needs a configured model: LLM_ENABLED=true, "
            "LLM_PROVIDER=openai and LLM_API_KEY (app/README.md -> 'Enabling the model'). "
            "Nothing was sent."
        )
    if settings.llm_daily_token_budget > 0 and not getattr(
        build_ledger(settings.llm_usage_path), "can_track", False
    ):
        return (
            "LLM_DAILY_TOKEN_BUDGET is set but LLM_USAGE_PATH records nothing, so every call "
            "would be refused; set LLM_DAILY_TOKEN_BUDGET=0 or a usable LLM_USAGE_PATH. "
            "Nothing was sent."
        )
    return None


def _run_golden_model(
    codebooks: CodebookSet,
    limit: int,
    resident: bool,
    settings,
    *,
    provider: LlmProvider | None = None,
) -> int:
    """Every golden case through the configured model: its top-1 next to the rules'."""
    from core.classify.llm import build_classifier

    cases = load_golden()
    texts = _golden_texts(cases, settings)
    counter = _CountingProvider(provider if provider is not None else build_provider(settings))
    classifier = build_classifier(settings, provider=counter, codebook_version=codebooks.version.id)
    filters = {
        NACE: NaceCandidateFilter(codebooks),
        ESA: EsaCandidateFilter(codebooks, resident=resident),
    }
    stats = counts(cases)
    print(
        f"golden set through the model {counter.model} ({settings.llm_base_url}): "
        f"{stats['cases']} case(s), {stats['verified']} verified"
    )
    if stats["verified"] == 0:
        print("WARNING: no case is verified yet - these figures are provisional, not accuracy")

    for label, group in (
        ("real issuers", [case for case in cases if case.real]),
        ("fictional trap cases", [case for case in cases if not case.real]),
    ):
        if not group:
            continue
        for kind, attribute in ((NACE, "expected_nace"), (ESA, "expected_esa")):
            print(f"\n--- {kind}, {label} ---")
            print(f"  {'case':<48} {'expected':<9} {'rules':<9} model")
            rules_right = model_right = abstained = 0
            for case in group:
                expected = getattr(case, attribute)
                shortlist = filters[kind].shortlist(texts[case.id], limit=limit)
                rules = shortlist.candidates[0].code if len(shortlist) else "-"
                result = classifier.classify(
                    shortlist, issuer_name=case.issuer, description=texts[case.id]
                )
                if result.top is None:
                    abstained += 1
                    model = f"abstained: {(result.abstain_reason or '')[:70]}"
                else:
                    model = f"{result.top.code} ({result.top.confidence})"
                    model_right += result.top.code == expected
                rules_right += rules == expected
                marks = ("=" if rules == expected else "x") + (
                    "=" if result.top is not None and result.top.code == expected else "x"
                )
                print(f"  {case.id:<48} {expected or '-':<9} {rules:<9} {model}  [{marks}]")
            size = len(group)
            print(
                f"  top-1: rules {rules_right}/{size} ({rules_right / size:.0%}) | model "
                f"{model_right}/{size} ({model_right / size:.0%}); the model abstained on "
                f"{abstained}"
            )

    total = counter.prompt_tokens + counter.completion_tokens
    print(
        f"\ntokens: {counter.prompt_tokens:,} in + {counter.completion_tokens:,} out = "
        f"{total:,} in {counter.calls} call(s) to {counter.model} (answers served from the "
        "cache cost nothing and are not counted)"
    )
    return EXIT_OK


def _hash_password(ask=getpass.getpass) -> int:
    """Ask twice, print the hash. The password itself is never printed or stored."""
    try:
        password = ask("password: ")
        again = ask("again: ")
    except (EOFError, KeyboardInterrupt):
        print("error: no password given", file=sys.stderr)
        return EXIT_LOAD_FAILED
    if not password:
        print("error: an empty password cannot be used", file=sys.stderr)
        return EXIT_LOAD_FAILED
    if password != again:
        print("error: the two passwords differ", file=sys.stderr)
        return EXIT_LOAD_FAILED
    print(hash_password(password))
    print("put it in APP_PASSWORD_HASH (in .env, in single quotes)", file=sys.stderr)
    return EXIT_OK


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

    if args.hash_password:
        return _hash_password()

    if args.golden_capture:
        try:
            recorded = capture(load_golden(), settings, captured_on=date.today())
        except GoldenError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_LOAD_FAILED
        print(f"recorded {recorded} register answer(s) in tests/golden/identity.json")
        return EXIT_OK

    if args.usage_xlsx is not None:
        return _export_usage(settings, args.usage_xlsx)

    if args.model:
        # Refuse before loading anything: a run that cannot call the model must not look
        # like one whose model abstained 92 times.
        if not args.golden:
            print("error: --model goes with --golden", file=sys.stderr)
            return EXIT_NO_MODEL
        refusal = _model_refusal(settings)
        if refusal is not None:
            print(f"error: {refusal}", file=sys.stderr)
            return EXIT_NO_MODEL

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
            if args.model:
                with spending_as(current_user(settings)):
                    return _run_golden_model(codebooks, args.limit, args.resident, settings)
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
