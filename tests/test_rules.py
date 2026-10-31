import pytest
from decimal import Decimal
from commercial_offer.rules import evaluate_discount, RuleError, validate_product

def test_priority_and_ceiling():
    rules = [{"id":"A", "type":"discount", "max_percent":"10", "priority":1},
             {"id":"B", "type":"discount", "max_percent":"5", "priority":1}]
    decision = evaluate_discount(product_id="P", category="C", quantity=1,
                                 unit_price=Decimal("100"), requested_percent="5", rules=rules)
    assert decision.applied_rule_ids == ("B",)
    with pytest.raises(RuleError, match="превышает допустимые"):
        evaluate_discount(product_id="P", category="C", quantity=1,
                          unit_price=Decimal("100"), requested_percent="6", rules=rules)

def test_reject_missing_product():
    with pytest.raises(RuleError, match="отсутствует в каталоге"):
        validate_product(product_id="bad", known_product_ids={"P"})
