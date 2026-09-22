"""Tests of the content-derived codebook version."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.codebooks.errors import CodebookFileError
from core.codebooks.loaders import load_codebooks
from core.codebooks.models import CodebookFile
from core.codebooks.versioning import build_version, fingerprint_file

from .conftest import FILE_NAMES, HEADERS, CodebookRows, MakeCodebookDir, make_settings, write_xlsx


def _file(name: str, sha: str) -> CodebookFile:
    return CodebookFile(name, Path(name), sha, 1, datetime(2024, 1, 1, tzinfo=UTC), 1)


def test_fingerprint_file(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(os.urandom(64 * 1024 * 3 + 17))  # spans several 64 KiB chunks, non-uniform
    file = fingerprint_file(path, "cts_ba0036", row_count=42)
    assert file.name == "cts_ba0036"
    assert file.path == path
    assert file.size_bytes == 64 * 1024 * 3 + 17
    assert file.row_count == 42
    assert file.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert file.modified_at.tzinfo is not None
    assert file.modified_at.utcoffset() == timedelta(0)
    assert abs((file.modified_at - datetime.now(UTC)).total_seconds()) < 3600


def test_fingerprint_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CodebookFileError):
        fingerprint_file(tmp_path / "missing", "nace_stat", row_count=0)


def test_build_version_is_order_independent_and_labelled() -> None:
    a, b = _file("cts_ba0036", "a" * 64), _file("nace_stat", "b" * 64)
    v1 = build_version([a, b])
    v2 = build_version([b, a])
    assert v1.id == v2.id
    assert v1.id.startswith("cb-") and len(v1.id) == len("cb-") + 16
    assert v1.label is None
    assert [f.name for f in v1.files] == ["cts_ba0036", "nace_stat"]
    labelled = build_version([a, b], label="2024-valid")
    assert labelled.id == f"{v1.id}+2024-valid"
    assert labelled.label == "2024-valid"
    assert build_version([a, b], label="  ").id == v1.id
    assert build_version([a, b], label=None).label is None


def test_build_version_changes_with_content_and_name() -> None:
    base = build_version([_file("cts_ba0036", "a" * 64)])
    assert build_version([_file("cts_ba0036", "b" * 64)]).id != base.id
    assert build_version([_file("nace_stat", "a" * 64)]).id != base.id


def test_build_version_loaded_at() -> None:
    files = [_file("cts_ba0036", "a" * 64)]
    version = build_version(files)
    assert version.loaded_at.tzinfo is not None
    assert version.loaded_at.utcoffset() == timedelta(0)
    assert abs((datetime.now(UTC) - version.loaded_at).total_seconds()) < 60
    fixed = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone(timedelta(hours=2)))
    assert build_version(files, loaded_at=fixed).loaded_at == fixed
    with pytest.raises(ValueError):
        build_version(files, loaded_at=datetime(2024, 5, 6))


def test_version_id_deterministic_across_loads_and_copies(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    original = make_codebook_dir(tmp_path / "a")
    first = load_codebooks(make_settings(original))
    second = load_codebooks(make_settings(original))
    assert first.version.id == second.version.id
    assert first.version.loaded_at.tzinfo is not None

    copied = tmp_path / "b"
    shutil.copytree(original, copied)
    old = (datetime.now(UTC) - timedelta(days=400)).timestamp()
    for path in copied.iterdir():
        os.utime(path, (old, old))
    third = load_codebooks(make_settings(copied))
    assert third.version.id == first.version.id
    assert {f.modified_at for f in third.version.files} != {
        f.modified_at for f in first.version.files
    }


def test_version_id_changes_when_one_cell_changes(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir, defaults: CodebookRows
) -> None:
    original = make_codebook_dir(tmp_path / "a")
    base = load_codebooks(make_settings(original))
    # Copy the directory byte for byte (openpyxl stamps the save time into every workbook it
    # writes, so only a copy keeps the other three files identical) and rewrite one table.
    edited = tmp_path / "b"
    shutil.copytree(original, edited)
    rows = list(defaults.nace_stat)
    rows[0] = (rows[0][0], rows[0][1], rows[0][2] + " (upraveno)")
    write_xlsx(edited / FILE_NAMES["nace_stat"], HEADERS["nace_stat"], rows)
    changed = load_codebooks(make_settings(edited))
    assert changed.version.id != base.version.id
    base_sha = {f.name: f.sha256 for f in base.version.files}
    changed_sha = {f.name: f.sha256 for f in changed.version.files}
    assert changed_sha["nace_stat"] != base_sha["nace_stat"]
    for name in ("ba0036_valid", "cts_ba0036", "cts_okec_nace2"):
        assert changed_sha[name] == base_sha[name]


def test_label_from_settings_is_appended(
    tmp_path: Path, make_codebook_dir: MakeCodebookDir
) -> None:
    directory = make_codebook_dir(tmp_path / "a")
    plain = load_codebooks(make_settings(directory))
    labelled = load_codebooks(make_settings(directory, label="2024-valid"))
    assert labelled.version.id == f"{plain.version.id}+2024-valid"
    assert labelled.version.label == "2024-valid"


def test_build_version_id_matches_documented_formula() -> None:
    a, b = _file("nace_stat", "b" * 64), _file("cts_ba0036", "a" * 64)
    material = f"cts_ba0036={'a' * 64}\nnace_stat={'b' * 64}".encode()
    expected = "cb-" + hashlib.sha256(material).hexdigest()[:16]
    assert build_version([a, b]).id == expected
    assert build_version([a, b], label="v1").id == expected + "+v1"
