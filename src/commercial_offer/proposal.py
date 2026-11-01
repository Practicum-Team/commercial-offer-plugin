"""Создание редактируемого DOCX на основе расчёта и согласованного текста.

Шаблон содержит плейсхолдеры вида {{key}} в абзацах и таблицах.
При замене текста внутри абзаца его смешанное форматирование может измениться.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping

from docx import Document


class ProposalError(ValueError):
    pass


REQUIRED_FIELDS = ("client_task", "solution", "justification", "known_terms")
REQUIRED_TOKENS = ("{{client_task}}", "{{solution}}", "{{items_table}}", "{{justification}}", "{{total}}", "{{known_terms}}")


def _paragraphs(doc):
    for paragraph in doc.paragraphs:
        yield paragraph
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def generate_proposal(*, template_path: str | Path, output_path: str | Path,
                      quote: Mapping[str, Any], content: Mapping[str, str]) -> Path:
    for key in REQUIRED_FIELDS:
        if key not in content or not isinstance(content[key], str):
            raise ProposalError(f"Поле content.{key} должно содержать строку")
    if not quote.get("items") or "grand_total" not in quote or "currency" not in quote:
        raise ProposalError("В расчёте должны присутствовать позиции items, итог grand_total и валюта currency")
    template = Path(template_path)
    if not template.is_file() or template.suffix.lower() != ".docx":
        raise ProposalError("Шаблон DOCX не найден")
    document = Document(template)
    paragraphs = list(_paragraphs(document))
    actual_tokens = "\n".join(p.text for p in paragraphs)
    missing = [token for token in REQUIRED_TOKENS if token not in actual_tokens]
    if missing:
        raise ProposalError(f"В шаблоне отсутствуют плейсхолдеры: {', '.join(missing)}")
    values = {"client_task": content["client_task"],
              "solution": content["solution"], "justification": content["justification"],
              "known_terms": content["known_terms"],
              "total": f"{quote['grand_total']} {quote['currency']}"}
    for paragraph in paragraphs:
        original = paragraph.text
        rewritten = original
        for key, value in values.items():
            rewritten = rewritten.replace("{{" + key + "}}", value)
        if rewritten != original:
            paragraph.text = rewritten
    # Вместо плейсхолдера items_table вставляем редактируемую таблицу Word.
    anchors = [p for p in _paragraphs(document) if "{{items_table}}" in p.text]
    if len(anchors) != 1:
        raise ProposalError("В шаблоне должен быть ровно один плейсхолдер items_table")
    anchor = anchors[0]
    anchor.text = anchor.text.replace("{{items_table}}", "")
    table = document.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    headers = ("ID", "Наименование", "Кол-во", "Цена", "Скидка", "Итого")
    for cell, label in zip(table.rows[0].cells, headers):
        cell.text = label
    for item in quote["items"]:
        cells = table.add_row().cells
        fields = (item["product_id"], item["name"], str(item["quantity"]),
                  str(item["unit_price"]), str(item["discount_percent"]) + "%",
                  str(item["total"]) + " " + str(quote["currency"]))
        for cell, value in zip(cells, fields):
            cell.text = value
    anchor._p.addnext(table._tbl)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(target)
    # Повторно открываем документ, чтобы проверить корректность сохранённого DOCX.
    Document(target)
    return target
