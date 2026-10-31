import pytest
from docx import Document
from commercial_offer.proposal import generate_proposal, ProposalError

QUOTE = {"items":[{"product_id":"P1","name":"Товар","quantity":1,"unit_price":"100.00","discount_percent":"0","total":"120.00"}],"currency":"RUB","grand_total":"120.00"}
CONTENT = {"client_task":"Задача", "solution":"Решение", "justification":"Обоснование", "known_terms":"Условия"}

def _template(path):
    doc = Document()
    for token in ("client_task", "solution", "items_table", "justification", "total", "known_terms"):
        doc.add_paragraph("{{" + token + "}}")
    doc.save(path)

def test_docx_generation(tmp_path):
    template = tmp_path / "template.docx"
    _template(template)
    out = generate_proposal(template_path=template, output_path=tmp_path/"out.docx",quote=QUOTE,content=CONTENT)
    result = Document(out)
    assert "Задача" in "\n".join(p.text for p in result.paragraphs)
    assert len(result.tables) == 1
    assert result.tables[0].rows[1].cells[1].text == "Товар"

def test_missing_placeholder(tmp_path):
    template = tmp_path / "bad.docx"
    Document().save(template)
    with pytest.raises(ProposalError, match="отсутствуют плейсхолдеры"):
        generate_proposal(template_path=template, output_path=tmp_path/"out.docx",quote=QUOTE,content=CONTENT)
