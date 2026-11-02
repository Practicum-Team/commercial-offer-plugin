"""Поиск по БД: товары+цены, правила. Без LLM: только запросы к SQLite."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .storage import make_session_factory
from .schemas import Price, Product, Rule, Profile


# ---------- контракт запроса ----------

@dataclass
class RequestSchema:
    profile_id: str
    client_type: Optional[str] = None
    customer_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    query: str = ""
    budget: Optional[str] = None          # Decimal как строка
    currency: Optional[str] = None        # если None — берём из profile.json
    purchase_date: Optional[str] = None   # ISO
    discount: Optional[str] = None        # явно упомянутая клиентом скидка
    expected_products: list[dict] = field(default_factory=list)
    acceptable_options: list[dict] = field(default_factory=list)
    required_clarifications: list[dict] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


# ---------- утилиты ----------

def _to_decimal(v: Any) -> Optional[Decimal]:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except InvalidOperation:
        return None


def _to_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)).date()
    except ValueError:
        return None


def resolve_currency(session, profile_id: str, requested: Optional[str]) -> Optional[str]:
    if requested:
        return requested
    prof = session.get(Profile, profile_id)
    if not prof:
        return None
    try:
        payload = prof.payload or "{}"
        from json import loads
        data = loads(payload)
    except Exception:
        return None
    return data.get("currency") or data.get("валюта")


# ---------- 1. Основной поиск: products + prices ----------

@dataclass
class ProductHit:
    product_id: str
    name: str
    category: Optional[str]
    description: Optional[str]
    amount: str
    currency: str
    price_id: str
    valid_from: Optional[date]
    valid_until: Optional[date]

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "amount": self.amount,
            "currency": self.currency,
            "price_id": self.price_id,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
        }


def search_products(
    db_url: str,
    profile_id: str,
    *,
    currency: Optional[str] = None,
    budget: Optional[str] = None,
    purchase_date: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Актуальные цены в бюджете/валюте. Возвращает кандидатов для анализа LLM."""
    SessionLocal = make_session_factory(db_url)
    with SessionLocal() as session:
        effective_currency = resolve_currency(session, profile_id, currency)
        on_date = _to_date(purchase_date) or date.today()
        budget_dec = _to_decimal(budget)

        stmt = (
            select(Product, Price)
            .join(Price, (Price.profile_id == Product.profile_id)
                          & (Price.product_id == Product.product_id))
            .where(Product.profile_id == profile_id)
            .where((Price.valid_from.is_(None)) | (Price.valid_from <= on_date))
            .where((Price.valid_until.is_(None)) | (Price.valid_until >= on_date))
        )
        if effective_currency:
            stmt = stmt.where(Price.currency == effective_currency)
        if category:
            stmt = stmt.where(Product.category == category)

        hits: list[ProductHit] = []
        for product, price in session.execute(stmt):
            amount_dec = _to_decimal(price.amount)
            if budget_dec is not None and amount_dec is not None and amount_dec > budget_dec:
                continue
            hits.append(ProductHit(
                product_id=product.product_id,
                name=product.name,
                category=product.category,
                description=product.description,
                amount=price.amount,
                currency=price.currency,
                price_id=price.price_id,
                valid_from=price.valid_from,
                valid_until=price.valid_until,
            ))
        return [h.to_dict() for h in hits]


# ---------- 2. Дополнительный поиск: все строки, без фильтров ----------

def dump_all_products(
    db_url: str, profile_id: str, *, currency: Optional[str] = None
) -> list[dict]:
    """Полный дамп products+prices (для fallback-анализа LLM)."""
    SessionLocal = make_session_factory(db_url)
    with SessionLocal() as session:
        effective_currency = resolve_currency(session, profile_id, currency)
        stmt = (
            select(Product, Price)
            .join(Price, (Price.profile_id == Product.profile_id)
                          & (Price.product_id == Product.product_id))
            .where(Product.profile_id == profile_id)
        )
        if effective_currency:
            stmt = stmt.where(Price.currency == effective_currency)
        out: list[dict] = []
        for product, price in session.execute(stmt):
            out.append(ProductHit(
                product_id=product.product_id,
                name=product.name,
                category=product.category,
                description=product.description,
                amount=price.amount,
                currency=price.currency,
                price_id=price.price_id,
                valid_from=price.valid_from,
                valid_until=price.valid_until,
            ).to_dict())
        return out


# ---------- 3. Правила для выбранных товаров ----------

def find_rules(
    db_url: str,
    profile_id: str,
    *,
    product_ids: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    check_discount: bool = False,
) -> list[dict]:
    """Правила для product_id / category / all. check_discount=True → вернуть всё."""
    SessionLocal = make_session_factory(db_url)
    product_ids = product_ids or []
    categories = categories or []
    with SessionLocal() as session:
        stmt = select(Rule).where(Rule.profile_id == profile_id)
        if not check_discount:
            conditions = [Rule.applies_to == "all"]
            if product_ids:
                targets = [str(value) for value in product_ids]
                targets.extend(f"product_id:{value}" for value in product_ids)
                conditions.append(Rule.applies_to.in_(targets))
            if categories:
                targets = [str(value) for value in categories]
                targets.extend(f"category:{value}" for value in categories)
                conditions.append(Rule.applies_to.in_(targets))
            if len(conditions) > 1:
                from sqlalchemy import or_
                stmt = stmt.where(or_(*conditions))
        rows = session.execute(stmt).scalars().all()
        return [
            {
                "rule_id": r.rule_id,
                "applies_to": r.applies_to,
                "rule_type": r.rule_type,
                "conditions_json": r.conditions_json,
                "description": r.description,
                "source_file": r.source_file,
            }
            for r in rows
        ]