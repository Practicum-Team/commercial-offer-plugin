"""Проверка правил копицентра на контролируемом наборе товаров."""
from datetime import date
from decimal import Decimal

import pytest

from commercial_offer.advanced_quote import calculate_company_quote
from commercial_offer.profile_adapter import ProfileError


def _rule(number, target, kind, **conditions):
    return {"rule_id": str(number), "applies_to": target,
            "rule_type": kind, "conditions_json": conditions}


def _profile():
    rules = [
        _rule(2, "category:Печать", "discount", discount_value="0.10", min_quantity=100),
        _rule(3, "category:Копирование", "discount", discount_value="0.15", min_quantity=200),
        _rule(4, "category:Фотопечать", "discount", discount_value="0.10", min_quantity=20),
        _rule(5, "product_id:30", "discount", discount_value="0.20", min_quantity=4),
        _rule(6, "category:Полиграфия", "discount", discount_value="0.05", min_order_amount=3000),
        _rule(7, "category:Переплёт", "discount", discount_value="0.10", bundle_with="category:Печать"),
        _rule(8, "all", "discount", discount_value="0.07", loyalty_card="discount_card_7",
              stackable_with=["2", "3", "4"], max_total_discount="0.25"),
        _rule(9, "all", "limit", max_discount_total="0.25", rounding_policy="HALF_UP",
              rounding_decimal_places=2, apply_order=["volume_discount", "category_discount",
              "loyalty_discount", "cap_total_discount", "round_final"]),
    ]
    categories = {'1': 'Печать', '2': 'Копирование', '3': 'Фотопечать',
                  '30': 'Фотопечать', '5': 'Полиграфия', '6': 'Переплёт'}
    return {'profile_name': 'company_copycenter',
            'settings': {'rounding': {'policy': 'half_up', 'decimals': 2},
                         'tax': {'vat_enabled': True, 'vat_rate': 22, 'calculation_order': 'inclusive'}},
            'rules': rules,
            'catalog': {i: {'name': i, 'category': c} for i, c in categories.items()},
            'prices': {i: [{'amount': Decimal('10.00'), 'currency': 'RUB',
                            'valid_from': '2026-01-01', 'valid_until': '2026-12-31',
                            'source_row': 2}] for i in categories}}


def _calculate(items, card=False):
    return calculate_company_quote(_profile(), items, on_date=date(2026, 6, 1), discount_card=card)


def test_threshold_and_discount():
    result = _calculate([{'product_id': '1', 'quantity': 100}])
    assert result['grand_total'] == '900.00'
    assert result['items'][0]['rule_ids'] == ['2']


def test_quantity_below_threshold():
    result = _calculate([{'product_id': '1', 'quantity': 99}])
    assert result['grand_total'] == '990.00'


def test_sequential_card_discount():
    result = _calculate([{'product_id': '1', 'quantity': 100}], True)
    # После скидки 10% карта уменьшает оставшиеся 900 рублей на 7%.
    assert result['grand_total'] == '837.00'
    assert result['items'][0]['rule_ids'] == ['2', '8']


def test_photo_bundle_not_stacked_with_card():
    result = _calculate([{'product_id': '30', 'quantity': 4}], True)
    assert result['grand_total'] == '32.00'
    assert result['items'][0]['rule_ids'] == ['5']


def test_print_and_binding():
    result = _calculate([{'product_id': '1', 'quantity': 1}, {'product_id': '6', 'quantity': 1}])
    assert result['items'][1]['rule_ids'] == ['7']
    assert result['grand_total'] == '19.00'


def test_category_amount_threshold():
    result = _calculate([{'product_id': '5', 'quantity': 300}])
    assert result['grand_total'] == '2850.00'
    assert result['items'][0]['rule_ids'] == ['6']


def test_card_without_volume_discount():
    result = _calculate([{'product_id': '1', 'quantity': 1}], True)
    assert result['grand_total'] == '9.30'


def test_invalid_order_configuration():
    profile = _profile()
    profile['rules'][-1]['conditions_json']['apply_order'] = ['round_final']
    with pytest.raises(ProfileError, match='Порядок'):
        calculate_company_quote(profile, [{'product_id': '1', 'quantity': 1}], on_date=date(2026, 6, 1))
