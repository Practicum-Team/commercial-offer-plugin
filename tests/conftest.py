"""Общие фикстуры: временный каталог профиля, БД, конфиг."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from commercial_offer.config import Config


@pytest.fixture
def tmp_profiles_dir(tmp_path: Path) -> Path:
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "company_a").mkdir()
    return d


@pytest.fixture
def config(tmp_path: Path, tmp_profiles_dir: Path) -> Config:
    cfg_path = tmp_path / "config.json"
    cfg = Config.load(config_path=cfg_path)
    cfg.profiles_dir = tmp_profiles_dir
    cfg.register_profile("company_a", tmp_profiles_dir / "company_a")
    cfg.set_active_profile("company_a")
    return cfg


@pytest.fixture
def profile_dir(config: Config) -> Path:
    return config.profile_path()


@pytest.fixture
def catalog_csv(profile_dir: Path) -> Path:
    p = profile_dir / "catalog.csv"
    p.write_text(
        "product_id,name,category,description\n"
        "32,Чистка ноутбука,service,Полная чистка\n"
        "40,Замена батарейки БИОС,service,CR2032\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def prices_csv(profile_dir: Path) -> Path:
    p = profile_dir / "prices.csv"
    p.write_text(
        "price_id,product_id,amount,currency,valid_from,valid_until\n"
        "pr1,32,1500,RUB,2024-01-01,2030-12-31\n"
        "pr2,40,1000,RUB,2024-01-01,2030-12-31\n"
        "pr3,32,9999,RUB,2020-01-01,2021-12-31\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def rules_json(profile_dir: Path) -> Path:
    p = profile_dir / "rules.json"
    p.write_text(json.dumps([
        {"rule_id": "r1", "applies_to": "all", "rule_type": "discount",
         "description": "Скидка 5% по дисконтной карте",
         "conditions_json": {"discount_card": True, "percent": 5}},
        {"rule_id": "r2", "applies_to": "product_id:32", "rule_type": "limit",
         "description": "Не более 3 чисток в год"},
        {"rule_id": "r3", "applies_to": "category:service", "rule_type": "limit",
         "description": "Ограничение по категории"},
    ], ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def cases_md(profile_dir: Path) -> Path:
    p = profile_dir / "cases.md"
    p.write_text(
        "# Кейс 1\nОтрасль: услуги. Задача: чистка. Результат вымышленный.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def template_docx(profile_dir: Path) -> Path:
    from docx import Document
    p = profile_dir / "template.docx"
    doc = Document()
    doc.add_paragraph("Заказ: {{customer_name}}")
    doc.add_paragraph("Сумма: {{total}}")
    doc.save(str(p))
    return p


@pytest.fixture
def committed_profile(config, catalog_csv, prices_csv, rules_json, cases_md):
    """Импортирует фикстуры в SQLite и возвращает db_url."""
    from commercial_offer import storage, validator
    from commercial_offer.schemas import ProfileSchema

    schema = ProfileSchema.from_dict({
        "profile_name": "company_a",
        "files": [
            {"filename": "catalog.csv", "kind": "catalog",
             "field_map": {"product_id": "product_id", "name": "name",
                           "category": "category", "description": "description"}},
            {"filename": "prices.csv", "kind": "prices",
             "field_map": {"price_id": "price_id", "product_id": "product_id",
                           "amount": "amount", "currency": "currency",
                           "valid_from": "valid_from", "valid_until": "valid_until"}},
            {"filename": "rules.json", "kind": "rules"},
            {"filename": "cases.md", "kind": "cases"},
        ],
    })
    files = {p.name: p for p in (catalog_csv, prices_csv, rules_json, cases_md)}
    content = validator.check_content("company_a", schema, files)
    assert content.ok, content.errors

    db_url = storage.default_db_url(config.profile_path())
    res = storage.commit_profile(
        profile_name="company_a", db_url=db_url,
        products=content.products, prices=content.prices,
        rules=content.rules, cases=content.cases,
        profile_payload={"currency": "RUB"},
    )
    assert res.ok, res.errors
    return db_url