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
