"""Расчёт заказа по правилам из профиля компании."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .profile_adapter import ProfileError, _decimal, _price


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _percent(rule: dict[str, Any]) -> Decimal:
    """Приводит долю скидки из JSON к процентам."""
    condition = rule['conditions_json']
    if 'discount_value' in condition:
        return _decimal(condition['discount_value'], 'discount_value') * 100
    return _decimal(condition['discount_percent'], 'discount_percent')


def calculate_company_quote(profile: dict[str, Any], items: list[dict[str, Any]], *,
                            on_date: date | None = None, discount_card: bool = False,
                            requested_discount_percent: str | None = None,
                            budget: str | None = None) -> dict[str, Any]:
    """Вычисляет стоимость и применяет правила, описанные в профиле.

    Особые условия, для которых недостаточно входных данных, отклоняются.
    """
    if not items:
        raise ProfileError('В заказе отсутствуют позиции')
    on_date = on_date or date.today()
    settings = profile['settings']
    tax_settings = settings.get('tax') or {}
    rounding = settings.get('rounding') or {}
    if rounding.get('policy', 'half_up').lower() != 'half_up' or int(rounding.get('decimals', 2)) != 2:
        raise ProfileError('Неподдерживаемые правила округления')
    if tax_settings.get('calculation_order') != 'inclusive' or tax_settings.get('vat_enabled') is not True:
        raise ProfileError('Профиль задаёт неподдерживаемый порядок расчёта налога')
    rate = _decimal(tax_settings.get('vat_rate'), 'vat_rate')
    if rate < 0 or rate > 100:
        raise ProfileError('Ставка налога должна находиться в диапазоне от 0 до 100%')
    rules = profile['rules']
    catalog = profile['catalog']
    order = []
    used = set()
    for row in items:
        pid = str(row.get('product_id', ''))
        if pid not in catalog:
            raise ProfileError(f'Позиция {pid} отсутствует в каталоге')
        if pid in used:
            raise ProfileError(f'Позиция {pid} повторяется в заказе')
        used.add(pid)
        qty = row.get('quantity')
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ProfileError(f'Количество для позиции {pid} должно быть положительным целым числом')
        price = _price(profile, pid, on_date)
        gross = price['amount'] * qty
        order.append(dict(product_id=pid, name=catalog[pid]['name'], category=catalog[pid]['category'],
                          quantity=qty, unit_price=price['amount'], gross=gross,
                          currency=price['currency'], discount_percent=Decimal(0), rule_ids=[],
                          source={'file': 'prices.csv', 'row': price['source_row']}))
    currencies = {x['currency'] for x in order}
    if len(currencies) != 1 or '' in currencies:
        raise ProfileError('В заказе используются разные валюты')
    currency = next(iter(currencies))
    subtotal = sum((x['gross'] for x in order), Decimal(0))
    category_totals: dict[str, Decimal] = {}
    for line in order:
        category_totals[line['category']] = category_totals.get(line['category'], Decimal(0)) + line['gross']

    if profile['profile_name'] == 'company_comp_service':
        # Особые условия требуют данных о ремонте, выезде или договоре.
        if used & {'14', '30', '51'}:
            raise ProfileError('Для выбранной услуги требуется дополнительная проверка условий')
        if any(x['category'] == 'Комплектующие' for x in order) and subtotal >= 20000:
            raise ProfileError('Не определено сочетание скидки на комплектующие с другими условиями')
        if discount_card:
            matched = []
            for rule in rules:
                if rule.get('rule_type') != 'discount' or rule.get('applies_to') != 'all':
                    continue
                cond = rule.get('conditions_json', {})
                if not cond.get('discount_card_required'):
                    continue
                minimum = _decimal(cond.get('min_purchase_amount', 0), 'min_purchase_amount')
                maximum = cond.get('max_purchase_amount')
                if subtotal < minimum or (maximum is not None and subtotal >= _decimal(maximum, 'max_purchase_amount')):
                    continue
                matched.append((minimum, _percent(rule), str(rule['rule_id'])))
            if not matched:
                raise ProfileError('В профиле отсутствует правило скидки по карте')
            _, percent, rid = max(matched)
        else:
            percent, rid = Decimal(0), ''
        for line in order:
            line['discount_percent'] = percent
            line['rule_ids'] = [rid] if rid else []
    elif profile['profile_name'] == 'company_copycenter':
        limit_rules = [r for r in rules if r.get('rule_type') == 'limit']
        if len(limit_rules) != 1:
            raise ProfileError('Не удалось однозначно определить ограничение скидок')
        limit = limit_rules[0]['conditions_json']
        expected_order = ['volume_discount', 'category_discount', 'loyalty_discount',
                          'cap_total_discount', 'round_final']
        if limit.get('apply_order') != expected_order:
            raise ProfileError('Порядок применения скидок не поддерживается')
        if str(limit.get('rounding_policy', '')).upper() != 'HALF_UP' or limit.get('rounding_decimal_places') != 2:
            raise ProfileError('Не поддерживается правило округления копицентра')
        cap = _decimal(limit['max_discount_total'], 'максимальная скидка') * 100
        if not Decimal(0) <= cap <= Decimal(100):
            raise ProfileError('Неверное ограничение суммарной скидки')
        loyalty_rules = [r for r in rules if r.get('rule_type') == 'discount'
                         and 'loyalty_card' in r.get('conditions_json', {})]
        if discount_card and len(loyalty_rules) != 1:
            raise ProfileError('Не найдено правило скидки по дисконтной карте')
        loyalty_rule = loyalty_rules[0] if discount_card else None
        card_rate = _percent(loyalty_rule) if loyalty_rule else Decimal(0)
        permitted = ({str(i) for i in loyalty_rule['conditions_json'].get('stackable_with', [])}
                     if loyalty_rule else set())
        cap_from_card = (_decimal(loyalty_rule['conditions_json'].get('max_total_discount', '0.25'),
                                  'лимит скидки по карте') * 100 if loyalty_rule else cap)
        cap = min(cap, cap_from_card)
        for line in order:
            candidates = []
            for rule in rules:
                if rule.get('rule_type') != 'discount':
                    continue
                cond = rule.get('conditions_json', {})
                if 'loyalty_card' in cond:
                    continue
                target = str(rule.get('applies_to', ''))
                if target.startswith('product_id:'):
                    if target.partition(':')[2] != line['product_id']:
                        continue
                elif target.startswith('category:'):
                    if target.partition(':')[2] != line['category']:
                        continue
                else:
                    raise ProfileError(f'Неизвестная область применения правила {rule.get("rule_id")}')
                if line['quantity'] < int(cond.get('min_quantity', 0)):
                    continue
                if category_totals[line['category']] < _decimal(cond.get('min_order_amount', 0), 'порог заказа'):
                    continue
                required = cond.get('bundle_with')
                if required:
                    if not required.startswith('category:'):
                        raise ProfileError('Неизвестный формат условия на комплект услуг')
                    if required.partition(':')[2] not in category_totals:
                        continue
                percent = _percent(rule)
                if not Decimal(0) <= percent <= Decimal(100):
                    raise ProfileError('Недопустимая процентная скидка')
                candidates.append((percent, str(rule['rule_id'])))
            # Несовмещаемые скидки на одну позицию не суммируются.
            if candidates:
                percent, rid = max(candidates, key=lambda x: (x[0], -int(x[1])))
                line['discount_percent'] = percent
                line['rule_ids'] = [rid]
            if loyalty_rule:
                current = line['discount_percent']
                ids = line['rule_ids']
                if not ids:
                    line['discount_percent'] = card_rate
                    line['rule_ids'] = [str(loyalty_rule['rule_id'])]
                elif all(rid in permitted for rid in ids):
                    # Карта применяется к оставшейся после первой скидки стоимости.
                    line['discount_percent'] = current + (Decimal(100) - current) * card_rate / 100
                    line['rule_ids'].append(str(loyalty_rule['rule_id']))
                elif card_rate > current:
                    # Для несовместимых правил выбираем только более выгодную скидку.
                    line['discount_percent'] = card_rate
                    line['rule_ids'] = [str(loyalty_rule['rule_id'])]
            line['discount_percent'] = min(line['discount_percent'], cap)
    else:
        raise ProfileError('Для профиля не настроены правила расчёта')

    total_discount = Decimal(0)
    grand_total = Decimal(0)
    output = []
    for line in order:
        discount_amount = _money(line['gross'] * line['discount_percent'] / 100)
        total = _money(line['gross']) - discount_amount
        vat = _money(total * rate / (Decimal(100) + rate))
        total_discount += discount_amount
        grand_total += total
        output.append(dict(product_id=line['product_id'], name=line['name'],
                           category=line['category'], quantity=line['quantity'],
                           unit_price=str(line['unit_price']), subtotal=str(_money(line['gross'])),
                           discount_percent=str(line['discount_percent']),
                           discount_amount=str(discount_amount), total=str(total),
                           tax_included=str(vat), rule_ids=line['rule_ids'], source=line['source']))
    if requested_discount_percent is not None:
        actual = total_discount * 100 / subtotal if subtotal else Decimal(0)
        required = _decimal(requested_discount_percent, 'requested_discount_percent')
        if abs(actual - required) > Decimal('0.001'):
            raise ProfileError('Запрошенная скидка не соответствует рассчитанным условиям')
    if budget is not None and grand_total > _decimal(budget, 'budget'):
        raise ProfileError('Стоимость заказа превышает указанный бюджет')
    return dict(profile=profile['profile_name'], as_of=on_date.isoformat(), currency=currency,
                items=output, subtotal=str(_money(subtotal)), discount_total=str(total_discount),
                grand_total=str(grand_total), tax_rate_percent=str(rate),
                prices_include_tax=True, status='рассчитано')
