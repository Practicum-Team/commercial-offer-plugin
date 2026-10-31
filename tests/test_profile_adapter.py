"""Проверки загрузки профилей и скидок на учебных данных."""
from datetime import date
from pathlib import Path

import pytest
from commercial_offer.profile_adapter import ProfileError, load_profile, calculate_profile_quote


ROOT = Path(__file__).resolve().parents[1] / "profiles"


def test_comp_service_without_card():
    profile = load_profile(ROOT / "company_comp_service")
    result = calculate_profile_quote(profile, [{"product_id": "1", "quantity": 1}],
                                     on_date=date(2026, 11, 1))
    assert result["grand_total"] == "700.00"
    assert result["items"][0]["discount_percent"] == "0"


def test_comp_service_with_card():
    profile = load_profile(ROOT / "company_comp_service")
    result = calculate_profile_quote(profile, [{"product_id": "1", "quantity": 15}],
                                     on_date=date(2026, 11, 1), discount_card=True)
    assert result["grand_total"] == "9450.00"
    assert result["items"][0]["rule_ids"] == ["3"]


def test_expired_price():
    profile = load_profile(ROOT / "company_comp_service")
    with pytest.raises(ProfileError, match="актуальных цен"):
        calculate_profile_quote(profile, [{"product_id": "1", "quantity": 1}],
                                on_date=date(2027, 1, 1))


def test_copycenter_json_files_are_valid():
    # Проверяем корректность файлов профиля и правил копицентра
    import json
    from pathlib import Path

    profile_dir = (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "company_copycenter"
    )

    for filename in ("profile.json", "rules.json"):
        with (profile_dir / filename).open(encoding="utf-8") as file:
            data = json.load(file)

        assert isinstance(data, (dict, list))
