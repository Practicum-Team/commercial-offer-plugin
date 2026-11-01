"""Работа с SQLite: схема, загрузка профиля, чтение для инструментов."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .schemas import (
    Base, Case, Price, Product, Profile, Rule, to_payload,
)


# ---------- движок ----------

def default_db_url(profile_dir: Path) -> str:
    return f"sqlite:///{Path(profile_dir) / 'profile.sqlite3'}"


def make_session_factory(db_url: str) -> sessionmaker:
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, future=True)


# ---------- загрузка ----------

@dataclass
class CommitResult:
    ok: bool
    counts: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def commit_profile(
    profile_name: str,
    db_url: str,
    *,
    products: list[dict],
    prices: list[dict],
    rules: list[dict],
    cases: list[dict],
    profile_payload: Optional[dict] = None,
) -> CommitResult:
    """Идемпотентная запись данных профиля в SQLite (merge по ключам)."""
    try:
        SessionLocal = make_session_factory(db_url)
    except Exception as exc:
        return CommitResult(ok=False, errors=[{"reason": f"БД недоступна: {exc}"}])

    try:
        with SessionLocal.begin() as session:  
            if session.get(Profile, profile_name) is None:
                session.add(Profile(
                    profile_id=profile_name,
                    name=profile_name,
                    payload=json.dumps(profile_payload or {}, ensure_ascii=False),
                ))
            elif profile_payload is not None:
                obj = session.get(Profile, profile_name)
                obj.payload = json.dumps(profile_payload, ensure_ascii=False)

            for p in products:
                session.merge(Product(
                    profile_id=profile_name,
                    product_id=p["product_id"],
                    name=p["name"],
                    category=p.get("category"),
                    description=p.get("description"),
                    payload=to_payload(p, p.get("_payload_columns", [])),
                    source_file=p.get("_source_file"),
                    source_row=p.get("_source_row"),
                ))

            for p in prices:
                session.merge(Price(
                    profile_id=profile_name,
                    price_id=p.get("price_id") or f"{p['product_id']}_{p['_source_row']}",
                    product_id=p["product_id"],
                    amount=p["amount"],
                    currency=p["currency"],
                    valid_from=_as_date(p.get("valid_from")),
                    valid_until=_as_date(p.get("valid_until")),
                    price_version=p.get("price_version"),
                    payload=to_payload(p, p.get("_payload_columns", [])),
                    source_file=p.get("_source_file"),
                    source_row=p.get("_source_row"),
                ))

            for r in rules:
                session.merge(Rule(
                    profile_id=profile_name,
                    rule_id=r["rule_id"],
                    applies_to=r["applies_to"],
                    rule_type=r["rule_type"],
                    conditions_json=r.get("conditions_json", "{}"),
                    description=r["description"],
                    payload=json.dumps(r, ensure_ascii=False, default=str),
                    source_file=r.get("_source_file"),
                ))

            for c in cases:
                session.merge(Case(
                    profile_id=profile_name,
                    case_id=c["case_id"],
                    text=c["text"],
                    is_fictional=int(c.get("is_fictional", 0)),
                    source_file=c.get("_source_file"),
                ))
    except Exception as exc:
        return CommitResult(ok=False, errors=[{"reason": str(exc)}])

    return CommitResult(ok=True, counts={
        "products": len(products),
        "prices": len(prices),
        "rules": len(rules),
        "cases": len(cases),
    })