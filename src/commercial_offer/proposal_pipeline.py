"""Мост: search-results + request-schema → prepare_company_proposal (вариант A)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .config import Config
from .proposal import ProposalError


# ---------- вспомогательные ----------

# Для компьютерного сервиса названия категорий описывают тип работ,
# а «Комплектующие» и «Расходные материалы» — продаваемые товары.
_GOODS_CATEGORIES = {"комплектующие", "расходные материалы"}
_WORK_WORDS = ("работ", "услуг", "сервис", "обеспечен", "печать", "копирован", "переплет")
_NUM_TOKEN = re.compile(r"^(?P<prefix>[A-Za-zА-Яа-я_/]+?)_(?P<n>\d+)$")


def _on_date(request: Mapping[str, Any]) -> date:
    raw = request.get("дата покупки")
    return datetime.fromisoformat(str(raw)).date() if raw else date.today()


def _discount_card(request: Mapping[str, Any]) -> bool:
    """Возвращает True только при явном подтверждении наличия карты."""
    for key in ("discount_card", "дисконтная_карта"):
        value = request.get(key)
        if isinstance(value, bool):
            return value

    restrictions = request.get("ограничения") or []
    if isinstance(restrictions, str):
        restrictions = [restrictions]
    if any(str(value).strip().lower() == "discount_card" for value in restrictions):
        return True

    text = str(request.get("запрос") or "").lower().replace("ё", "е")
    negative_patterns = (
        r"\bнет\b.{0,35}\bдисконтн\w*\s+карт\w*",
        r"\bбез\b.{0,25}\bдисконтн\w*\s+карт\w*",
        r"\bне\s+(?:имею|имеется|предъявляю|пользуюсь)\b.{0,30}\bкарт\w*",
        r"\bкарт\w*\b.{0,20}\bнет\b",
    )
    if any(re.search(pattern, text) for pattern in negative_patterns):
        return False

    positive_patterns = (
        r"\bесть\b.{0,30}\bдисконтн\w*\s+карт\w*",
        r"\bимею\b.{0,25}\bдисконтн\w*\s+карт\w*",
        r"\bпредъявляю\b.{0,25}\bкарт\w*",
        r"\bкарт\w*\s+есть\b",
    )
    return any(re.search(pattern, text) for pattern in positive_patterns)


def _items(search_results: Mapping[str, Any]) -> list[dict]:
    out = []
    for row in search_results.get("ожидаемые_товары", []):
        if row.get("product_id") is None:
            continue
        out.append({
            "product_id": str(row["product_id"]),
            "quantity": int(row.get("количество", 1)),
        })
    return out


def _is_work(category: str | None) -> bool:
    if not category:
        return False
    low = category.lower().strip()
    if low in _GOODS_CATEGORIES:
        return False
    return any(w in low for w in _WORK_WORDS)


def _sum_by(items: list[dict], predicate) -> str:
    return str(sum(
        (Decimal(str(it.get("total", "0"))) for it in items if predicate(it)),
        Decimal("0"),
    ))


def _max_index(template_fields: list[str], prefix: str) -> int:
    """Максимальный n среди плейсхолдеров вида <prefix><n>."""
    best = 0
    for field in template_fields:
        if not field.startswith(prefix):
            continue
        tail = field[len(prefix):]
        if tail.isdigit():
            best = max(best, int(tail))
    return best


# ---------- автозаполнение ----------

def _autofill_confirmed(
    quote: Mapping[str, Any],
    request: Mapping[str, Any],
    search_results: Mapping[str, Any],
    explicit: Mapping[str, str] | None,
    template_fields: list[str],
) -> dict[str, str]:
    items: list[dict] = list(quote.get("items", []))
    work_items = [it for it in items if _is_work(it.get("category"))]
    equip_items = [it for it in items if not _is_work(it.get("category"))]

    text = (request.get("запрос") or "").strip()
    client = (request.get("контактное лицо") or "").strip()

    values: dict[str, str] = {
        # контактные/справочные — из запроса
        "наименование/ФИО_заказчика": request.get("наименование") or client or "—",
        "ФИО_представителя_заказчика": client or "—",
        "телефон": (request.get("телефон") or "—").strip(),
        "email": (request.get("email") or "—").strip(),
        "описание_задачи_клиента": text or "—",
        "текущее_состояние": "—",
        "объект_инфраструктура": "—",
        "почему_решение": "—",
        "обоснование_выбора_решения": "—",
        "соответствие_требованиям": "—",
        "срок_действия_расчёта": "14 календарных дней",
        "предоплата": "согласно договору",
        "ограничения_по_срокам_бюджету_условиям": "—",
        "номер_КП": "—",
        "ФИО_подписанта": "—",
    }

    # -- позиции (позиция_n, ID_n, кол_n, ед_n, цена_n, сумма_n) --
    max_pos = _max_index(template_fields, "позиция_")
    for i in range(1, max_pos + 1):
        if i <= len(items):
            it = items[i - 1]
            values[f"ID_{i}"] = str(it.get("product_id", ""))
            values[f"позиция_{i}"] = str(it.get("name", ""))
            values[f"кол_{i}"] = str(it.get("quantity", 0))
            values[f"ед_{i}"] = "шт."
            values[f"цена_{i}"] = str(it.get("unit_price", ""))
            values[f"сумма_{i}"] = str(it.get("total", ""))
        else:
            values[f"ID_{i}"] = "—"
            values[f"позиция_{i}"] = "—"
            values[f"кол_{i}"] = "—"
            values[f"ед_{i}"] = "—"
            values[f"цена_{i}"] = "—"
            values[f"сумма_{i}"] = "—"

    # -- работы (услуга_n, кол_р_n) --
    max_work = _max_index(template_fields, "услуга_")
    max_work_qty = _max_index(template_fields, "кол_р_")
    max_work = max(max_work, max_work_qty)
    for i in range(1, max_work + 1):
        if i <= len(work_items):
            it = work_items[i - 1]
            values[f"услуга_{i}"] = str(it.get("name", ""))
            values[f"кол_р_{i}"] = str(it.get("quantity", 0))
        else:
            values[f"услуга_{i}"] = "—"
            values[f"кол_р_{i}"] = "—"

    # -- оборудование (оборудование_n, кол_об_n, ед_об_n) --
    max_eq = _max_index(template_fields, "оборудование_")
    max_eq = max(max_eq,
                 _max_index(template_fields, "кол_об_"),
                 _max_index(template_fields, "ед_об_"))
    for i in range(1, max_eq + 1):
        if i <= len(equip_items):
            it = equip_items[i - 1]
            values[f"оборудование_{i}"] = str(it.get("name", ""))
            values[f"кол_об_{i}"] = str(it.get("quantity", 0))
            values[f"ед_об_{i}"] = "шт."
        else:
            values[f"оборудование_{i}"] = "—"
            values[f"кол_об_{i}"] = "—"
            values[f"ед_об_{i}"] = "—"

    # -- итоги --
    values["итого_работы"] = _sum_by(items, lambda it: _is_work(it.get("category")))
    values["итого_оборудование"] = _sum_by(items, lambda it: not _is_work(it.get("category")))

    # -- пользовательские значения переопределяют (кроме protected) --
    if explicit:
        for key, val in explicit.items():
            if val is None:
                continue
            values[key] = str(val)

    return values


# ---------- основной вход ----------

def prepare_from_search(
    *, config: Config,
    search_results: Mapping[str, Any],
    request: Mapping[str, Any],
    output_path: Path,
    confirmed: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    clarifications = search_results.get("обязательные_уточнения") or []
    if clarifications:
        raise ProposalError("Есть обязательные уточнения: "
                            + json.dumps(clarifications, ensure_ascii=False))

    items = _items(search_results)
    if not items:
        raise ProposalError("search-results.json: пустой ожидаемые_товары")

    profile_id = search_results.get("profile_id") or request.get("profile_id")
    if not profile_id:
        raise ProposalError("не указан profile_id")
    if request.get("profile_id") and request["profile_id"] != profile_id:
        raise ProposalError("Профили запроса и результатов поиска не совпадают")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(profile_id)):
        raise ProposalError("Некорректное имя профиля компании")
    profile_dir = config.profiles_dir / profile_id

    # 1) расчёт
    from .profile_adapter import load_profile
    from .advanced_quote import calculate_company_quote
    profile = load_profile(profile_dir)
    quote = calculate_company_quote(
        profile, items,
        on_date=_on_date(request),
        discount_card=_discount_card(request),
        budget=request.get("бюджет"),
    )

    # 2) список плейсхолдеров шаблона — знать, какие слоты заполнять
    from .document_preparation import missing_fields, prepared_content, template_fields
    template = profile_dir / "template.docx"
    if not template.is_file():
        raise ProposalError("В профиле компании отсутствует шаблон коммерческого предложения")
    fields = template_fields(template)

    # 3) автозаполнение confirmed
    merged = _autofill_confirmed(quote, request, search_results, confirmed, fields)

    # 4) проверка и генерация
    values = prepared_content(profile["settings"], quote,
                              on_date=_on_date(request), confirmed=merged)
    absent = missing_fields(template, values)
    if absent:
        raise ProposalError("Не заполнены поля шаблона: " + ", ".join(absent))

    from .company_docx import generate_company_docx
    created = generate_company_docx(
        template_path=template, output_path=Path(output_path),
        quote=quote, content=values, document_date=_on_date(request),
    )
    return {
        "quote": quote,
        "document_path": str(created),
        "missing_fields": [],
        "confirmed_used": merged,
    }