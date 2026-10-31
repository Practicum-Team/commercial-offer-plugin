from datetime import date
import pytest
from commercial_offer.calculator import calculate_quote, CalculationError

CATALOG = {"P1": {"name": "Товар", "price": "100.00", "currency": "RUB", "category": "equipment", "valid_until": "2030-12-31", "source_file": "prices.csv", "source_row": 2}}
RULES = [{"id":"D10", "type":"discount", "category":"equipment", "min_quantity":2, "max_percent":"10"}]

def calc(items, **kwargs):
    return calculate_quote(items=items, catalog=CATALOG, rules=RULES, currency="RUB", tax_percent="20", quote_date=date(2026, 11, 1), **kwargs)

def test_correct_total_exclusive_tax():
    res = calc([{"product_id":"P1", "quantity":2, "discount_percent":"10"}])
    assert (res["subtotal"], res["discount_total"], res["net_total"], res["tax_total"], res["grand_total"]) == ("200.00", "20.00", "180.00", "36.00", "216.00")
    assert res["items"][0]["source"]["row"] == 2

def test_tax_included():
    res = calc([{"product_id":"P1", "quantity":2, "discount_percent":"10"}], prices_include_tax=True)
    assert (res["net_total"], res["tax_total"], res["grand_total"]) == ("150.00", "30.00", "180.00")

def test_disallowed_discount():
    with pytest.raises(CalculationError, match="превышает допустимые"):
        calc([{"product_id":"P1", "quantity":2, "discount_percent":"15"}])

def test_unknown_product():
    with pytest.raises(ValueError, match="отсутствует в каталоге"):
        calc([{"product_id":"MISSING", "quantity":1}])

def test_no_discount_without_matching_rule():
    with pytest.raises(CalculationError, match="Скидка запрещена"):
        calc([{"product_id":"P1", "quantity":1, "discount_percent":"5"}])

def test_budget():
    result = calc([{"product_id":"P1", "quantity":1}], budget="100")
    assert result["within_budget"] is False

def test_float_rejected():
    with pytest.raises(ValueError, match="float"):
        calc([{"product_id":"P1", "quantity":1, "discount_percent":0.5}])

def test_expired_price():
    old = {"P1": {**CATALOG["P1"], "valid_until":"2020-01-01"}}
    with pytest.raises(CalculationError, match="Срок действия цены истёк"):
        calculate_quote(items=[{"product_id":"P1", "quantity":1}], catalog=old, rules=[], currency="RUB", quote_date=date(2026,11,1))
