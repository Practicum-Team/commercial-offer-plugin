"""Проверки расчётов на данных учебных компаний."""
from datetime import date
from pathlib import Path

import pytest

from commercial_offer.advanced_quote import calculate_company_quote
from commercial_offer.profile_adapter import ProfileError, load_profile


ROOT = Path(__file__).resolve().parents[1]


def test_comp_service_card():
    profile = load_profile(ROOT / 'profiles/company_comp_service')
    result = calculate_company_quote(profile, [{'product_id': '1', 'quantity': 1}],
                                     on_date=date(2026, 6, 1), discount_card=True)
    assert result['grand_total']
    assert result['items'][0]['rule_ids']


def test_unknown_product():
    profile = load_profile(ROOT / 'profiles/company_comp_service')
    with pytest.raises(ProfileError, match='отсутствует'):
        calculate_company_quote(profile, [{'product_id': '-99', 'quantity': 1}], on_date=date(2026, 6, 1))


def test_expired_price():
    profile = load_profile(ROOT / 'profiles/company_comp_service')
    with pytest.raises(ProfileError, match='актуальных цен'):
        calculate_company_quote(profile, [{'product_id': '1', 'quantity': 1}], on_date=date(2027, 1, 1))


def test_copycenter_valid_json():
    # Проверяем, что профиль копицентра содержит корректный JSON
    import json
    from pathlib import Path

    profile_path = (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "company_copycenter"
        / "profile.json"
    )

    rules_path = profile_path.parent / "rules.json"

    with profile_path.open(encoding="utf-8") as file:
        profile = json.load(file)

    with rules_path.open(encoding="utf-8") as file:
        rules = json.load(file)

    assert isinstance(profile, dict)
    assert isinstance(rules, (dict, list))
