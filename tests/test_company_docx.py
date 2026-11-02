"""Проверки заполнения корпоративного шаблона."""
from pathlib import Path

import pytest

from commercial_offer.company_docx import generate_company_docx
from commercial_offer.proposal import ProposalError


def test_missing_template_fields(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'profiles/company_comp_service/template.docx'
    quote = {'items': [{'name': 'Тест', 'quantity': 1, 'unit_price': '100',
                        'discount_amount': '0', 'total': '100', 'tax_included': '18'}],
             'grand_total': '100.00', 'discount_total': '0.00'}
    content = {'описание_задачи_клиента': 'Задача', 'обоснование_выбора_решения': 'Основание',
               'соответствие_требованиям': 'Соответствует', 'итого_работы': '100',
               'итого_оборудование': '0'}
    with pytest.raises(ProposalError, match='Не заполнены поля шаблона'):
        generate_company_docx(template_path=path, output_path=tmp_path / 'kp.docx',
                              quote=quote, content=content)


def test_calculated_totals_and_document_date_cannot_be_overridden(tmp_path):
    from datetime import date
    from docx import Document

    template = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("Дата: {{дата_КП}}")
    doc.add_paragraph("Итого: {{итого_к_оплате}}")
    doc.add_paragraph("Скидка: {{скидка_сумма}}")
    doc.add_paragraph("НДС: {{НДС}}")
    doc.save(template)

    quote = {
        "items": [{"name": "Тест", "quantity": 1, "unit_price": "100.00",
                   "discount_amount": "10.00", "total": "90.00",
                   "tax_included": "16.23"}],
        "grand_total": "90.00",
        "discount_total": "10.00",
    }
    content = {
        "дата_КП": "01.01.2000",
        "итого_к_оплате": "0.01",
        "скидка_сумма": "999.00",
        "НДС": "999.00",
        "описание_задачи_клиента": "Задача",
        "обоснование_выбора_решения": "Обоснование",
        "соответствие_требованиям": "Соответствует",
        "итого_работы": "90.00",
        "итого_оборудование": "0.00",
    }
    output = tmp_path / "result.docx"
    generate_company_docx(
        template_path=template,
        output_path=output,
        quote=quote,
        content=content,
        document_date=date(2026, 11, 1),
    )
    result = Document(output)
    text = "\n".join(p.text for p in result.paragraphs)
    assert "01.11.2026" in text
    assert "90.00" in text
    assert "10.00" in text
    assert "16.23" in text
    assert "0.01" not in text
    assert "999.00" not in text
