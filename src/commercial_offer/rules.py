"""Проверка допустимости скидок и ограничений по товарам.

Условия рассчитываются программно по переданным правилам без обращения к LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


class RuleError(ValueError):
    """Ошибка в правиле или запрошенных коммерческих условиях."""


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise RuleError(f"{field}: ожидается строка, целое число или Decimal; значения float, bool и None не допускаются")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RuleError(f"{field}: некорректное десятичное число") from exc
    if not result.is_finite():
        raise RuleError(f"{field}: число должно быть конечным")
    return result


@dataclass(frozen=True)
class DiscountDecision:
    percent: Decimal
    applied_rule_ids: tuple[str, ...]


def evaluate_discount(*, product_id: str, category: str | None, quantity: int,
                      unit_price: Decimal, requested_percent: Any,
                      rules: Sequence[Mapping[str, Any]]) -> DiscountDecision:
    """Проверяет запрошенную скидку по применимым правилам.

    Правило может ограничиваться товаром, категорией, количеством и суммой.
    Скидки не складываются. Из правил с максимальным приоритетом выбирается
    наименьший допустимый процент. Если правило не найдено, скидка запрещена.
    """
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise RuleError("Количество должно быть положительным целым числом")
    if unit_price < 0 or not unit_price.is_finite():
        raise RuleError("Цена за единицу должна быть конечным неотрицательным числом")
    requested = decimal_value(requested_percent, "requested_percent")
    if not Decimal("0") <= requested <= Decimal("100"):
        raise RuleError("Запрошенная скидка должна быть от 0 до 100%")
    eligible = []
    for rule in rules:
        if rule.get("type") != "discount" or rule.get("enabled", True) is False:
            continue
        rid = str(rule.get("id", ""))
        if not rid:
            raise RuleError("У правила скидки отсутствует идентификатор")
        if rule.get("product_id") is not None and str(rule["product_id"]) != product_id:
            continue
        if rule.get("category") is not None and rule["category"] != category:
            continue
        min_qty = rule.get("min_quantity", 1)
        if not isinstance(min_qty, int) or isinstance(min_qty, bool) or min_qty <= 0:
            raise RuleError(f"Правило {rid}: минимальное количество должно быть положительным целым числом")
        if quantity < min_qty:
            continue
        minimum = decimal_value(rule.get("min_subtotal", "0"), f"rule {rid} min_subtotal")
        if minimum < 0:
            raise RuleError(f"Правило {rid}: минимальная сумма не может быть отрицательной")
        if unit_price * quantity < minimum:
            continue
        ceiling = decimal_value(rule.get("max_percent"), f"rule {rid} max_percent")
        if not Decimal("0") <= ceiling <= Decimal("100"):
            raise RuleError(f"Правило {rid}: максимальная скидка должна быть от 0 до 100%")
        priority = rule.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise RuleError(f"Правило {rid}: приоритет должен быть целым числом")
        eligible.append((priority, ceiling, rid))
    if not eligible:
        if requested != 0:
            raise RuleError("Скидка запрещена: нет подходящего правила")
        return DiscountDecision(Decimal("0"), ())
    best_priority = max(x[0] for x in eligible)
    candidates = [x for x in eligible if x[0] == best_priority]
    ceiling = min(x[1] for x in candidates)
    rule_ids = tuple(sorted(x[2] for x in candidates if x[1] == ceiling))
    if requested > ceiling:
        raise RuleError(f"Запрошенная скидка {requested}% превышает допустимые {ceiling}%")
    return DiscountDecision(requested, rule_ids)


def validate_product(*, product_id: str, known_product_ids: set[str],
                     active: bool = True) -> None:
    if product_id not in known_product_ids:
        raise RuleError(f"Товар отсутствует в каталоге: {product_id}")
    if not active:
        raise RuleError(f"Товар неактивен: {product_id}")
