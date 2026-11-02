"""Интеграционные тесты CLI через subprocess, чтобы гарантировать персистентность."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(cfg_path: Path, *args: str) -> tuple[int, dict]:
    env = {**os.environ, "COMMERCIAL_OFFER_CONFIG": str(cfg_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "commercial_offer.cli", *args],
        capture_output=True, text=True, env=env,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": proc.stdout, "stderr": proc.stderr}
    return proc.returncode, payload


def test_profiles_list_creates_config(tmp_path: Path):
    cfg = tmp_path / "config.json"
    code, out = _run(cfg, "profiles-list")
    assert code == 0
    assert cfg.exists()
    assert "profiles" in out


def test_commit_blocked_without_confirmations(
    tmp_path: Path, catalog_csv, prices_csv, rules_json
):
    cfg = tmp_path / "config.json"
    # регистрируем профиль
    code, _ = _run(cfg, "profiles-load", "company_a",
                   str(catalog_csv), str(prices_csv), str(rules_json))
    # schema
    schema_path = tmp_path / "data-schema.json"
    schema_path.write_text(json.dumps({
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id", "name": "name"}},
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"product_id": "product_id", "amount": "amount",
                           "currency": "currency", "valid_from": "valid_from"}},
            {"filename": "rules.json", "kind": "rules"},
        ],
    }), encoding="utf-8")
    code, out = _run(
        cfg, "profiles-commit", "company_a", str(schema_path),
        str(catalog_csv), str(prices_csv), str(rules_json),
    )
    assert code == 4
    assert out["stage"] == "confirmation"


def test_full_pipeline_with_confirmations(
    tmp_path: Path, catalog_csv, prices_csv, rules_json
):
    cfg = tmp_path / "config.json"
    _run(cfg, "profiles-load", "company_a",
         str(catalog_csv), str(prices_csv), str(rules_json))

    schema_path = tmp_path / "data-schema.json"
    schema_path.write_text(json.dumps({
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id", "name": "name"}},
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"price_id": "price_id", "product_id": "product_id",
                           "amount": "amount", "currency": "currency",
                           "valid_from": "valid_from", "valid_until": "valid_until"}},
            {"filename": "rules.json", "kind": "rules"},
        ],
    }), encoding="utf-8")

    for kind in ("schema", "fields", "commit"):
        code, _ = _run(cfg, "profiles-confirm", "company_a",
                       str(schema_path), kind, "--yes")
        assert code == 0

    code, out = _run(
        cfg, "profiles-commit", "company_a", str(schema_path),
        str(catalog_csv), str(prices_csv), str(rules_json),
    )
    assert code == 0, out
    assert out["counts"]["products"] == 2


def test_schema_change_invalidates_confirmations(
    tmp_path: Path, catalog_csv, prices_csv, rules_json
):
    cfg = tmp_path / "config.json"
    _run(cfg, "profiles-load", "company_a",
         str(catalog_csv), str(prices_csv), str(rules_json))

    schema_path = tmp_path / "data-schema.json"
    base = {
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id", "name": "name"}},
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"price_id": "price_id", "product_id": "product_id",
                           "amount": "amount", "currency": "currency",
                           "valid_from": "valid_from", "valid_until": "valid_until"}},
            {"filename": "rules.json", "kind": "rules"},
        ],
    }
    schema_path.write_text(json.dumps(base), encoding="utf-8")
    for kind in ("schema", "fields", "commit"):
        _run(cfg, "profiles-confirm", "company_a", str(schema_path), kind, "--yes")

    # меняем схему
    base["files"][0]["field_map"]["name"] = "description"
    schema_path.write_text(json.dumps(base), encoding="utf-8")

    code, out = _run(
        cfg, "profiles-commit", "company_a", str(schema_path),
        str(catalog_csv), str(prices_csv), str(rules_json),
    )
    assert code == 4
    assert out["stage"] == "confirmation"

def test_search_uses_database_for_requested_profile_not_active_profile(tmp_path: Path):
    from argparse import Namespace
    from commercial_offer.cli import _resolve_db_url
    from commercial_offer.config import Config

    profiles_dir = tmp_path / "profiles"
    first = profiles_dir / "company_a"
    second = profiles_dir / "company_b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    cfg = Config.load(config_path=tmp_path / "config.json")
    cfg.profiles_dir = profiles_dir
    cfg.register_profile("company_a", first)
    cfg.register_profile("company_b", second)
    cfg.set_active_profile("company_a")

    args = Namespace(db_url=None, config=cfg, name="company_b")
    assert _resolve_db_url(args) == f"sqlite:///{second / 'profile.sqlite3'}"
