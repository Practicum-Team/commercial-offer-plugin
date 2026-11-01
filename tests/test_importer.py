from pathlib import Path

import pytest

from commercial_offer import importer
from commercial_offer.config import ALLOWED_EXTENSIONS


def test_check_file_rejects_unknown_ext(tmp_path: Path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ\x00")
    chk = importer.check_file(p)
    assert not chk.ok


def test_check_file_csv_ok(catalog_csv: Path):
    chk = importer.check_file(catalog_csv)
    assert chk.ok and chk.detected_kind == "csv"


def test_check_file_docx_bad_magic(tmp_path: Path):
    p = tmp_path / "fake.docx"
    p.write_bytes(b"not a zip")
    chk = importer.check_file(p)
    assert not chk.ok
    assert "магические" in chk.reason


def test_read_csv_rows_semicolon(tmp_path: Path):
    p = tmp_path / "x.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")
    cols, rows = importer.read_csv_rows(p)
    assert cols == ["a", "b"]
    assert rows == [{"a": "1", "b": "2"}]


def test_fingerprint_includes_columns_and_samples(catalog_csv: Path):
    fp = importer.fingerprint(catalog_csv)
    assert fp.error is None
    assert "product_id" in fp.columns
    assert len(fp.sample_rows) >= 1


def test_normalize_row_coerces(catalog_csv: Path):
    raw = {"product_id": "32", "name": "X", "amount": "1 500,50",
           "valid_from": "01.01.2024"}
    fs_map = {"product_id": "product_id", "name": "name",
              "amount": "amount", "valid_from": "valid_from"}
    out = importer.normalize_row(raw, fs_map)
    assert out["product_id"] == "32"
    assert out["amount"] == "1500.50"
    assert out["valid_from"] == "2024-01-01"


def test_profile_loader_skips_identical(config, catalog_csv):
    loader = importer.ProfileLoader(config)
    src = config.profile_path() / "catalog.csv"
    src.write_bytes(catalog_csv.read_bytes())
    res = loader.load("company_a", [src])
    assert res["status"] == "ok"
    assert res["applied"][0]["action"] == "skip"


def test_profile_loader_conflict_needs_confirmation(config, tmp_path: Path, catalog_csv):
    loader = importer.ProfileLoader(config)
    other = tmp_path / "catalog.csv"
    other.write_text("product_id,name\n99,Different\n", encoding="utf-8")
    res = loader.load("company_a", [other], overwrite=None)
    assert res["status"] == "needs_confirmation"
    assert res["conflicts"][0]["file"] == "catalog.csv"


def test_profile_loader_overwrite_yes(config, tmp_path: Path):
    loader = importer.ProfileLoader(config)
    src = tmp_path / "catalog.csv"
    src.write_text("product_id,name\n1,A\n", encoding="utf-8")
    res = loader.load("company_a", [src], overwrite=True)
    assert res["status"] == "ok"