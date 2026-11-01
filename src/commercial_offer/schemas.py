"""ORM-модели и dataclasses для промежуточных структур импорта."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sqlalchemy import (
    Date, ForeignKey, Integer, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"
    profile_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="{}")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    source_file: Mapped[Optional[str]] = mapped_column(String)
    source_row: Mapped[Optional[int]] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("profile_id", "product_id", name="uq_product"),)


class Price(Base):
    __tablename__ = "prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[str] = mapped_column(String, nullable=False)  # Decimal как строка
    currency: Mapped[str] = mapped_column(String, nullable=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_until: Mapped[Optional[date]] = mapped_column(Date)
    price_version: Mapped[Optional[str]] = mapped_column(String)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    source_file: Mapped[Optional[str]] = mapped_column(String)
    source_row: Mapped[Optional[int]] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("profile_id", "price_id", name="uq_price"),
        Index("ix_price_lookup", "profile_id", "product_id"),
    )


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    applies_to: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    source_file: Mapped[Optional[str]] = mapped_column(String)
    __table_args__ = (UniqueConstraint("profile_id", "rule_id", name="uq_rule"),)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.profile_id"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_fictional: Mapped[int] = mapped_column(Integer, default=0)
    source_file: Mapped[Optional[str]] = mapped_column(String)
    __table_args__ = (UniqueConstraint("profile_id", "case_id", name="uq_case"),)


# ---------- dataclasses для пайплайна импорта ----------

KIND_CATALOG = "catalog"
KIND_PRICES = "prices"
KIND_RULES = "rules"
KIND_CASES = "cases"
KIND_PROFILE = "profile"
KIND_TEMPLATE = "template"
ALL_KINDS = {KIND_CATALOG, KIND_PRICES, KIND_RULES, KIND_CASES, KIND_PROFILE, KIND_TEMPLATE}

REQUIRED_FIELDS = {
    KIND_CATALOG: ["product_id", "name"],
    KIND_PRICES: ["product_id", "amount", "currency", "valid_from|valid_until"],
    KIND_RULES: ["description", "applies_to"],
}

# Ожидаемые логические поля -> допустимые синонимы столбцов в файле
FIELD_ALIASES: dict[str, list[str]] = {
    "product_id": ["product_id", "id", "sku", "код", "артикул", "id товара"],
    "name": ["name", "название", "наименование", "product", "товар"],
    "category": ["category", "категория", "группа"],
    "description": ["description", "описание", "текст", "правило"],
    "amount": ["amount", "price", "цена", "стоимость", "сумма"],
    "currency": ["currency", "валюта"],
    "valid_from": ["valid_from", "date_from", "с", "начало"],
    "valid_until": ["valid_until", "date_to", "по", "окончание"],
    "applies_to": ["applies_to", "scope", "область", "применение"],
    "rule_type": ["rule_type", "type", "тип"],
    "case_id": ["case_id", "id_кейса"],
    "price_id": ["price_id", "id_цены"],
    "rule_id": ["rule_id", "id_правила"],
}


@dataclass
class FileFingerprint:
    """Отпечаток файла, который передаётся LLM."""
    filename: str
    ext: str
    columns: list[str] = field(default_factory=list)
    sample_rows: list[list[Any]] = field(default_factory=list)
    first_lines: list[str] = field(default_factory=list)  # для md/json
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "ext": self.ext,
            "columns": self.columns,
            "sample_rows": self.sample_rows,
            "first_lines": self.first_lines,
            **({"error": self.error} if self.error else {}),
        }


@dataclass
class FileSchema:
    """Схема одного файла после сопоставления LLM."""
    filename: str
    kind: str                 # catalog|prices|rules|cases|profile|template
    field_map: dict[str, str] = field(default_factory=dict)  # логическое_поле -> столбец
    payload_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "kind": self.kind,
            "field_map": self.field_map,
            "payload_columns": self.payload_columns,
        }


@dataclass
class ProfileSchema:
    """Схема профиля целиком: результат сопоставления всех файлов."""
    profile_name: str
    files: list[FileSchema] = field(default_factory=list)
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "version": self.version,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProfileSchema":
        files = [
            FileSchema(
                filename=f["filename"],
                kind=f["kind"],
                field_map=f.get("field_map", {}),
                payload_columns=f.get("payload_columns", []),
            )
            for f in data.get("files", [])
        ]
        return cls(
            profile_name=data["profile_name"],
            files=files,
            version=data.get("version", 1),
        )


def to_payload(row: dict, columns: list[str]) -> str:
    sub = {c: row.get(c) for c in columns if c in row}
    return json.dumps(sub, ensure_ascii=False, default=str)