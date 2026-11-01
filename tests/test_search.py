from commercial_offer import search


def test_search_products_respects_date_and_budget(committed_profile: str):
    hits = search.search_products(
        committed_profile, "company_a",
        currency="RUB", budget="1500", purchase_date="2026-01-01",
    )
    ids = {h["product_id"] for h in hits}
    assert "32" in ids
    assert "40" not in ids  # 1000 <= 1500, но проверим бюджет 800
    hits2 = search.search_products(
        committed_profile, "company_a", budget="800", purchase_date="2026-01-01",
    )
    assert hits2 == []


def test_search_products_excludes_expired(committed_profile: str):
    hits = search.search_products(
        committed_profile, "company_a", purchase_date="2026-01-01",
    )
    price_ids = {h["price_id"] for h in hits}
    assert "pr3" not in price_ids


def test_search_products_uses_profile_currency(committed_profile: str):
    hits = search.search_products(committed_profile, "company_a")
    assert all(h["currency"] == "RUB" for h in hits)


def test_dump_all_products(committed_profile: str):
    all_rows = search.dump_all_products(committed_profile, "company_a")
    assert {r["product_id"] for r in all_rows} == {"32", "40"}


def test_find_rules_by_product_and_all(committed_profile: str):
    rows = search.find_rules(
        committed_profile, "company_a",
        product_ids=["32"], categories=["service"],
    )
    ids = {r["rule_id"] for r in rows}
    assert "r1" in ids  # all
    assert "r2" in ids  # 32


def test_find_rules_check_discount_returns_all(committed_profile: str):
    rows = search.find_rules(committed_profile, "company_a", check_discount=True)
    assert {r["rule_id"] for r in rows} == {"r1", "r2"}