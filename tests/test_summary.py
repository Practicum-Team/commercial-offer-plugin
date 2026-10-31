from commercial_offer.summary import build_summary

def test_sources_and_questions():
    quote={"currency":"RUB","grand_total":"15","items":[{"product_id":"P","source":{"file":"prices.csv","row":4}}]}
    s=build_summary(quote, unresolved_questions=["Срок?"])
    assert s["sources"][0]["row"] == 4
    assert s["unresolved_questions"] == ["Срок?"]
    assert s["requires_human_review"] is True
