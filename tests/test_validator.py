from pathlib import Path

from commercial_offer import validator
from commercial_offer.schemas import FileSchema, ProfileSchema


def _files(*paths):
    return {p.name: p for p in paths}


def test_inspect_builds_prompt(catalog_csv: Path):
    res = validator.inspect("company_a", [catalog_csv])
    assert res.fingerprints
    assert "field_map" in res.llm_prompt
    assert not res.errors


def test_check_schema_ok(catalog_csv, prices_csv, rules_json):
    schema = ProfileSchema.from_dict({
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id", "name": "name"}},
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"product_id": "product_id", "amount": "amount",
                           "currency": "currency", "valid_from": "valid_from"}},
            {"filename": "rules.json", "kind": "rules"},
        ],
    })
    res = validator.check_schema("company_a", schema,
                                 _files(catalog_csv, prices_csv, rules_json))
    assert res.ok, res.errors


def test_check_schema_missing_required_field(catalog_csv, prices_csv, rules_json):
    schema = ProfileSchema.from_dict({
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id"}},  # нет name
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"product_id": "product_id", "amount": "amount",
                           "currency": "currency", "valid_from": "valid_from"}},
            {"filename": "rules.json", "kind": "rules"},
        ],
    })
    res = validator.check_schema("company_a", schema,
                                 _files(catalog_csv, prices_csv, rules_json))
    assert not res.ok
    assert any("name" in e["reason"] for e in res.errors)


def test_check_content_detects_duplicate_product(profile_dir: Path):
    p = profile_dir / "catalog_dup.csv"
    p.write_text("product_id,name\n1,A\n1,B\n", encoding="utf-8")
    schema = ProfileSchema.from_dict({"profile_name": "p", "files": [
        {"filename": "catalog_dup.csv", "kind": "catalog",
         "field_map": {"product_id": "product_id", "name": "name"}},
    ]})
    res = validator.check_content("p", schema, _files(p))
    assert not res.ok
    assert any("дубль" in e["reason"] for e in res.errors)


def test_check_content_detects_missing_product_in_catalog(catalog_csv, prices_csv):
    prices_csv.write_text(
        "price_id,product_id,amount,currency,valid_from,valid_until\n"
        "pr1,999,1500,RUB,2024-01-01,2030-12-31\n",
        encoding="utf-8",
    )
    schema = ProfileSchema.from_dict({"profile_name": "p", "files": [
        {"filename": "catalog.csv", "kind": "catalog",
         "field_map": {"product_id": "product_id", "name": "name"}},
        {"filename": "prices.csv", "kind": "prices",
         "field_map": {"price_id": "price_id", "product_id": "product_id",
                       "amount": "amount", "currency": "currency",
                       "valid_from": "valid_from", "valid_until": "valid_until"}},
    ]})
    res = validator.check_content("p", schema, _files(catalog_csv, prices_csv))
    assert not res.ok
    assert any("отсутствует в каталоге" in e["reason"] for e in res.errors)


def test_check_content_detects_bad_amount(catalog_csv, prices_csv):
    prices_csv.write_text(
        "price_id,product_id,amount,currency,valid_from,valid_until\n"
        "pr1,32,abc,RUB,2024-01-01,2030-12-31\n",
        encoding="utf-8",
    )
    schema = ProfileSchema.from_dict({"profile_name": "p", "files": [
        {"filename": "catalog.csv", "kind": "catalog",
         "field_map": {"product_id": "product_id", "name": "name"}},
        {"filename": "prices.csv", "kind": "prices",
         "field_map": {"price_id": "price_id", "product_id": "product_id",
                       "amount": "amount", "currency": "currency",
                       "valid_from": "valid_from", "valid_until": "valid_until"}},
    ]})
    res = validator.check_content("p", schema, _files(catalog_csv, prices_csv))
    assert not res.ok
    assert any("цена не число" in e["reason"] for e in res.errors)


def test_check_content_rules_missing_description(profile_dir: Path):
    p = profile_dir / "rules_bad.json"
    p.write_text('[{"applies_to": "all"}]', encoding="utf-8")
    schema = ProfileSchema.from_dict({"profile_name": "p", "files": [
        {"filename": "rules_bad.json", "kind": "rules"},
    ]})
    res = validator.check_content("p", schema, _files(p))
    assert not res.ok
    assert any("описания правила" in e["reason"] for e in res.errors)