"""The explanations sent with the government codes, which the codebook itself does not give."""

from __future__ import annotations

import pytest

from config.settings import get_settings
from core.classify.models import ESA, NACE, Candidate, CandidateSet
from core.classify.prompts import CODE_NOTES, build_prompt
from core.codebooks.errors import CodebookError
from core.codebooks.loaders import load_and_check

#: All the real codebook says about a non-resident government code.
POINTER = "Zahrnuje nerezidentské institucionální jednotky obdobné jako v subsektoru  1312000 pro rezidenty."


def _esa(*rows: tuple[str, str]) -> CandidateSet:
    return CandidateSet(
        kind=ESA,
        candidates=tuple(
            Candidate(
                kind=ESA, code=code, cts_id=str(600 + index), label=label, definitions=(POINTER,)
            )
            for index, (code, label) in enumerate(rows)
        ),
        considered=56,
        filter_name="test",
    )


def test_each_government_code_is_explained_right_under_its_line() -> None:
    candidates = _esa(
        ("2003120", "Národní vládní instituce"),
        ("2003110", "Ústřední vládní instituce"),
        ("2003130", "Místní vládní instituce"),
        ("2002212", "Banky soukromé národní"),
    )

    lines = build_prompt(
        candidates, issuer_name="Land Berlin", description="GLEIF: [STATE_GOVERNMENT]"
    ).user.splitlines()

    for code in ("2003120", "2003110", "2003130"):
        at = next(index for index, line in enumerate(lines) if f"kód {code} " in line)
        assert lines[at + 1] == f"   • {CODE_NOTES[code]}"
        assert lines[at + 2] == f"   • {POINTER}"  # the codebook's own text still follows
    bank = next(index for index, line in enumerate(lines) if "kód 2002212 " in line)
    assert "Vysvětlivka" not in lines[bank + 1]


def test_the_state_level_note_names_what_the_model_got_wrong() -> None:
    state = CODE_NOTES["2003120"]

    assert "S.1312" in state
    assert "STATE_GOVERNMENT" in state
    assert "Berlína" in state  # a city-state is a Land, not local government
    assert "NEznamená celostátní" in state  # "národní" is not "national"
    assert "CENTRAL_GOVERNMENT" in CODE_NOTES["2003110"]
    assert "LOCAL_GOVERNMENT" in CODE_NOTES["2003130"]


def test_nace_prompts_carry_no_explanation() -> None:
    nace = CandidateSet(
        kind=NACE,
        candidates=(
            Candidate(kind=NACE, code="84", cts_id="528", label="Veřejná správa a obrana"),
        ),
        considered=87,
        filter_name="test",
    )

    assert "Vysvětlivka" not in build_prompt(nace, issuer_name=None, description="x").user


def test_every_explained_code_is_a_leaf_of_the_real_codebook() -> None:
    settings = get_settings()
    names = (
        settings.codebook_cts_ba0036_file,
        settings.codebook_ba0036_valid_file,
        settings.codebook_cts_okec_nace2_file,
        settings.codebook_nace_stat_file,
    )
    if not all(settings.codebook_path(name).is_file() for name in names):
        pytest.skip("real codebooks not available")
    try:
        codebooks, _ = load_and_check(settings, strict=False)
    except CodebookError as exc:  # pragma: no cover - the codebook check covers this
        pytest.skip(f"real codebooks could not be loaded: {exc}")

    assert set(CODE_NOTES) <= {sector.key for sector in codebooks.esa_leaves()}
