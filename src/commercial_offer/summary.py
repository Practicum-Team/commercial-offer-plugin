"""Формирование служебной сводки по расчёту, источникам и вопросам."""
from __future__ import annotations
from typing import Any, Mapping, Sequence


def build_summary(quote: Mapping[str, Any], *, assumptions: Sequence[str] = (),
                  unresolved_questions: Sequence[str] = (),
                  extra_sources: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    sources = []
    for item in quote.get("items", []):
        if item.get("source", {}).get("file"):
            sources.append({"product_id": item["product_id"], **item["source"]})
    sources.extend(dict(s) for s in extra_sources)
    return {"as_of": quote.get("as_of"), "currency": quote.get("currency"),
            "grand_total": quote.get("grand_total"), "within_budget": quote.get("within_budget"),
            "sources": sources, "assumptions": list(assumptions),
            "unresolved_questions": list(unresolved_questions),
            "requires_human_review": True}
