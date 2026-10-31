"""Расчёт стоимости КП с точной десятичной арифметикой."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from .rules import RuleError, decimal_value, evaluate_discount, validate_product


class CalculationError(ValueError):
    pass


def _money(value: Decimal, minor_units: int) -> Decimal:
    if not isinstance(minor_units, int) or not 0 <= minor_units <= 4:
        raise CalculationError("Число знаков после запятой должно быть целым числом от 0 до 4")
    quantum = Decimal(1).scaleb(-minor_units)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def calculate_quote(*, items: Sequence[Mapping[str, Any]],
                    catalog: Mapping[str, Mapping[str, Any]],
                    rules: Sequence[Mapping[str, Any]],
                    currency: str, tax_percent: str = "0", prices_include_tax: bool = False,
                    minor_units: int = 2, budget: str | None = None,
                    quote_date: date | None = None) -> dict[str, Any]:
    """Рассчитывает КП по ценам из проверенного каталога.

    Для каждого товара используются идентификатор, цена, валюта, при необходимости
    категория, срок действия цены и сведения об источнике.
    Скидка рассчитывается по каждой позиции до налога.
    Суммы по строкам округляются до minor_units знаков после запятой.
    Если налог входит в цену, его сумма выделяется из стоимости после скидки.
    """
    if not currency or not isinstance(currency, str):
        raise CalculationError("Не указана валюта расчёта")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
        raise CalculationError("Для расчёта нужна хотя бы одна позиция")
    tax = decimal_value(tax_percent, "tax_percent")
    if not Decimal("0") <= tax <= Decimal("100"):
        raise CalculationError("Налоговая ставка должна быть от 0 до 100%")
    as_of = quote_date or date.today()
    output = []
    gross_subtotal = Decimal("0")
    all_discounts = Decimal("0")
    net_total = Decimal("0")
    tax_total = Decimal("0")
    grand_total = Decimal("0")
    seen: set[str] = set()
    for item in items:
        product_id = str(item.get("product_id", ""))
        if product_id in seen:
            raise CalculationError(f"Товар повторяется в предложении: {product_id}")
        seen.add(product_id)
        validate_product(product_id=product_id, known_product_ids=set(catalog),
                         active=catalog.get(product_id, {}).get("active", True))
        row = catalog[product_id]
        if row.get("currency") != currency:
            raise CalculationError(f"Валюта товара не совпадает с валютой расчёта: {product_id}")
        valid_until = row.get("valid_until")
        if valid_until:
            try:
                expiration = date.fromisoformat(str(valid_until))
            except ValueError as exc:
                raise CalculationError(f"Некорректная дата окончания действия цены для товара: {product_id}") from exc
            if expiration < as_of:
                raise CalculationError(f"Срок действия цены истёк для товара: {product_id}")
        qty = item.get("quantity")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise CalculationError(f"Некорректное количество товара: {product_id}")
        price = decimal_value(row.get("price"), f"price {product_id}")
        if price < 0:
            raise CalculationError("Цена не может быть отрицательной")
        try:
            decision = evaluate_discount(product_id=product_id, category=row.get("category"),
                                         quantity=qty, unit_price=price,
                                         requested_percent=item.get("discount_percent", "0"),
                                         rules=rules)
        except RuleError as exc:
            raise CalculationError(str(exc)) from exc
        base = _money(price * qty, minor_units)
        discount = _money(base * decision.percent / Decimal("100"), minor_units)
        discounted = base - discount
        if prices_include_tax:
            total = discounted
            net = _money(total / (Decimal("1") + tax / Decimal("100")), minor_units)
            tax_amount = total - net
        else:
            net = discounted
            tax_amount = _money(net * tax / Decimal("100"), minor_units)
            total = net + tax_amount
        gross_subtotal += base
        all_discounts += discount
        net_total += net
        tax_total += tax_amount
        grand_total += total
        output.append({"product_id": product_id, "name": str(row.get("name", product_id)),
                       "quantity": qty, "unit_price": str(_money(price, minor_units)),
                       "currency": currency, "discount_percent": str(decision.percent),
                       "discount_rule_ids": list(decision.applied_rule_ids),
                       "subtotal": str(base), "discount_amount": str(discount),
                       "net_amount": str(net), "tax_amount": str(tax_amount),
                       "total": str(total),
                       "source": {"file": row.get("source_file"), "row": row.get("source_row")}})
    max_budget = decimal_value(budget, "budget") if budget is not None else None
    if max_budget is not None and max_budget < 0:
        raise CalculationError("Бюджет не может быть отрицательным")
    return {"currency": currency, "as_of": as_of.isoformat(), "tax_percent": str(tax),
            "prices_include_tax": prices_include_tax, "items": output,
            "subtotal": str(gross_subtotal), "discount_total": str(all_discounts),
            "net_total": str(net_total), "tax_total": str(tax_total),
            "grand_total": str(grand_total), "budget": str(max_budget) if max_budget is not None else None,
            "within_budget": grand_total <= max_budget if max_budget is not None else None,
            "rounding": f"Округление до ближайшего значения (половина вверх); {minor_units} знаков после запятой; отдельно по каждой позиции"}
