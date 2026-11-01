"""Загрузка учебных профилей и расчёт по поддерживаемым правилам."""
from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


class ProfileError(ValueError):
    """Ошибка исходного профиля или условий расчёта."""


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (bool, float)) or value is None:
        raise ProfileError(f"Некорректное значение поля «{field}»: требуется десятичное число")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ProfileError(f"Поле «{field}» не содержит число") from exc
    if not number.is_finite():
        raise ProfileError(f"Поле «{field}» должно содержать конечное число")
    return number


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ProfileError(f"Файл не найден: {path.name}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ProfileError(f"Отсутствуют заголовки столбцов: {path.name}")
        if None in reader.fieldnames:
            raise ProfileError(f"В файле обнаружен столбец без названия: {path.name}")
        result = []
        for number, row in enumerate(reader, start=2):
            # Дополнительные значения после последнего столбца обрабатываются отдельно.
            extra = row.pop(None, None)
            if extra and any(value.strip() for value in extra):
                raise ProfileError(f"Лишние непустые значения в {path.name}, строка {number}")
            result.append({key: (value or "").strip() for key, value in row.items()})
        return result


def load_profile(directory: str | Path) -> dict[str, Any]:
    """Проверяет файлы профиля и объединяет каталог с действующими прайсами."""
    folder = Path(directory)
    try:
        settings = json.loads(
            (folder / "profile.json").read_text(encoding="utf-8-sig"),
            parse_float=Decimal
        )
        rules = json.loads(
            (folder / "rules.json").read_text(encoding="utf-8-sig"),
            parse_float=Decimal
        )
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"Ошибка формата JSON: строка {exc.lineno}, столбец {exc.colno}"
        ) from exc
    except OSError as exc:
        raise ProfileError("Не удалось прочитать файл настроек или правил") from exc
    if not isinstance(settings, dict) or not isinstance(rules, list):
        raise ProfileError("Профиль или список правил имеет неверную структуру")
    catalog_rows = _rows(folder / "catalog.csv")
    price_rows = _rows(folder / "prices.csv")
    catalog = {}
    for i, row in enumerate(catalog_rows, 2):
        pid = row.get("product_id", "")
        if not pid or pid in catalog:
            raise ProfileError(f"Пустой или повторяющийся идентификатор товара в строке {i}")
        catalog[pid] = {
            "product_id": pid,
            "name": row.get("name") or row.get("название"),
            "category": row.get("category") or row.get("категория"),
            "source_file": "catalog.csv",
            "source_row": i,
        }
        if not catalog[pid]["name"]:
            raise ProfileError(f"У товара {pid} отсутствует название")
    prices: dict[str, list[dict[str, Any]]] = {}
    for i, row in enumerate(price_rows, 2):
        pid = row.get("product_id", "")
        if pid not in catalog:
            raise ProfileError(f"Цена ссылается на отсутствующий товар {pid}, строка {i}")
        amount = _decimal(row.get("amount") or row.get("цена"), "цена")
        if amount < 0:
            raise ProfileError(f"Отрицательная цена товара {pid}")
        start = row.get("valid_from") or row.get("дата_начала")
        end = row.get("valid_until") or row.get("дата_окончания")
        try:
            if start:
                date.fromisoformat(start)
            if end:
                date.fromisoformat(end)
        except ValueError as exc:
            raise ProfileError(f"Некорректная дата цены товара {pid}") from exc
        prices.setdefault(pid, []).append({
            "amount": amount, "currency": row.get("currency") or row.get("валюта"),
            "valid_from": start, "valid_until": end,
            "price_version": row.get("price_version", ""), "source_row": i,
        })
    return {"settings": settings, "catalog": catalog, "prices": prices,
            "rules": rules, "profile_name": folder.name}


def _price(profile: dict[str, Any], product_id: str, on_date: date) -> dict[str, Any]:
    choices = []
    for row in profile["prices"].get(product_id, []):
        if row["valid_from"] and date.fromisoformat(row["valid_from"]) > on_date:
            continue
        if row["valid_until"] and date.fromisoformat(row["valid_until"]) < on_date:
            continue
        choices.append(row)
    if len(choices) != 1:
        raise ProfileError(f"Для товара {product_id} найдено {len(choices)} актуальных цен вместо одной")
    return choices[0]


def _rounded(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_profile_quote(profile: dict[str, Any], items: list[dict[str, Any]], *,
                            on_date: date | None = None, discount_card: bool = False,
                            requested_discount_percent: str | None = None) -> dict[str, Any]:
    """Расчёт простого заказа по поддерживаемым условиям двух учебных компаний.

    Неподдерживаемые способы применения скидок отклоняются, а не угадываются.
    """
    as_of = on_date or date.today()
    if not items:
        raise ProfileError("В заказе нет товаров или услуг")
    catalog = profile["catalog"]
    lines = []
    seen = set()
    for item in items:
        pid = str(item.get("product_id", ""))
        if pid not in catalog:
            raise ProfileError(f"Товар {pid} отсутствует в каталоге")
        if pid in seen:
            raise ProfileError(f"Товар {pid} указан несколько раз; объедините позиции")
        seen.add(pid)
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ProfileError(f"Некорректное количество для товара {pid}")
        price_row = _price(profile, pid, as_of)
        gross = price_row["amount"] * quantity
        lines.append({"product_id": pid, "name": catalog[pid]["name"],
                      "category": catalog[pid]["category"], "quantity": quantity,
                      "unit_price": price_row["amount"], "gross": gross,
                      "currency": price_row["currency"],
                      "source": {"file": "prices.csv", "row": price_row["source_row"]},
                      "discount_percent": Decimal("0"), "rule_ids": []})
    currencies = {line["currency"] for line in lines}
    if len(currencies) != 1 or not next(iter(currencies)):
        raise ProfileError("В заказе обнаружены несовместимые валюты")
    name = profile["profile_name"]
    total_gross = sum((line["gross"] for line in lines), Decimal("0"))
    if name == "company_comp_service":
        # Карта подтверждается пользователем; сумма порога — до применения скидки.
        percent = Decimal("0")
        if discount_card:
            for threshold, value in ((Decimal("30000"), 20), (Decimal("15000"), 15),
                                     (Decimal("10000"), 10), (Decimal("0"), 5)):
                if total_gross >= threshold:
                    percent = Decimal(value)
                    break
        if requested_discount_percent is not None and _decimal(requested_discount_percent, "запрошенная скидка") != percent:
            raise ProfileError(f"Запрошенная скидка не соответствует условиям: допустимо {percent}%")
        # Некоторые специальные правила зависят от ремонта, предоплаты или выезда.
        # Для таких позиций нужен отдельный разбор условий, поэтому расчёт запрещён.
        special_ids = {"14", "30", "51"}
        if seen & special_ids:
            raise ProfileError("Для выбранных услуг требуется проверить дополнительные условия ремонта или выезда")
        for line in lines:
            if line["category"] == "Комплектующие" and total_gross >= Decimal("20000"):
                raise ProfileError("Для комплектующих требуется отдельная проверка скидки по категории")
            line["discount_percent"] = percent
            if percent:
                line["rule_ids"] = ["2" if percent == 5 else {10: "3", 15: "4", 20: "5"}[int(percent)]]
        rate = Decimal("22")
    elif name == "company_copycenter":
        if discount_card:
            raise ProfileError("Скидка по карте копицентра пока не поддерживается")
        # Комплексные, накопительные и совмещаемые скидки требуют отдельного алгоритма.
        special_categories = {"Полиграфия", "Переплёт"}
        if any(line["category"] in special_categories for line in lines):
            raise ProfileError("Для выбранной категории требуется проверка комплексных скидок")
        if any(line["product_id"] == "30" for line in lines):
            raise ProfileError("Для фото на документы действует специальное правило комплекта")
        rules_by_category = {"Печать": (100, Decimal("10"), "2"),
                             "Копирование": (200, Decimal("15"), "3"),
                             "Фотопечать": (20, Decimal("10"), "4")}
        for line in lines:
            rule = rules_by_category.get(line["category"])
            if rule and line["quantity"] >= rule[0]:
                line["discount_percent"] = rule[1]
                line["rule_ids"] = [rule[2]]
        if requested_discount_percent is not None:
            raise ProfileError("Общая скидка для этого профиля не задаётся: скидки рассчитываются по позициям")
        rate = Decimal("22")
    else:
        raise ProfileError("Для указанного профиля не настроен расчёт правил")
    result = []
    total_discount = Decimal("0")
    grand_total = Decimal("0")
    for line in lines:
        discount = _rounded(line["gross"] * line["discount_percent"] / Decimal("100"))
        amount = _rounded(line["gross"]) - discount
        tax = _rounded(amount * rate / (Decimal("100") + rate))
        total_discount += discount
        grand_total += amount
        result.append({"product_id": line["product_id"], "name": line["name"],
                       "quantity": line["quantity"], "unit_price": str(line["unit_price"]),
                       "subtotal": str(_rounded(line["gross"])),
                       "discount_percent": str(line["discount_percent"]),
                       "discount_amount": str(discount), "total": str(amount),
                       "tax_included": str(tax), "rule_ids": line["rule_ids"],
                       "source": line["source"]})
    return {"profile": name, "currency": next(iter(currencies)),
            "as_of": as_of.isoformat(), "items": result,
            "subtotal": str(_rounded(total_gross)),
            "discount_total": str(total_discount), "grand_total": str(grand_total),
            "prices_include_tax": True, "tax_rate_percent": str(rate),
            "status": "рассчитано по поддерживаемым правилам"}
