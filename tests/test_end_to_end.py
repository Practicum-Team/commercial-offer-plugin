"""Проверка полного сценария расчёта и создания DOCX."""
from datetime import date
from pathlib import Path

import pytest
from docx import Document

from commercial_offer.document_preparation import missing_fields, prepared_content
from commercial_offer.end_to_end import prepare_company_proposal
from commercial_offer.profile_adapter import load_profile
from commercial_offer.advanced_quote import calculate_company_quote
from commercial_offer.proposal import ProposalError


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'profiles' / 'company_comp_service'
ORDER = [{'product_id': '1', 'quantity': 1}]


def test_missing_details_prevent_document_creation(tmp_path):
    """Без данных заказчика генератор не создаёт недостоверный документ."""
    target = tmp_path / 'kp.docx'
    with pytest.raises(ProposalError, match='Не заполнены поля шаблона'):
        prepare_company_proposal(
            profile_dir=PROFILE,
            items=ORDER,
            output_path=target,
            on_date=date.today(),
        )
    assert not target.exists()


def test_complete_demo_order_creates_docx(tmp_path):
    """Подтверждённые учебные значения позволяют собрать редактируемый DOCX."""
    profile = load_profile(PROFILE)
    calculated = calculate_company_quote(profile, ORDER, on_date=date.today())
    known = prepared_content(profile['settings'], calculated, on_date=date.today())
    template = PROFILE / 'template.docx'
    absent = missing_fields(template, known)

    # Имена и реквизиты здесь являются маркерами тестовых данных, а не сведениями клиента.
    confirmed = {field: f'ТЕСТОВОЕ ЗНАЧЕНИЕ: {field}' for field in absent}
    confirmed.update({
        'описание_задачи_клиента': 'Учебная задача: установка операционной системы',
        'обоснование_выбора_решения': 'Услуга присутствует в каталоге компании',
        'соответствие_требованиям': 'Установка одной операционной системы',
        'итого_работы': calculated['grand_total'],
        'итого_оборудование': '0.00',
    })
    output = tmp_path / 'kp.docx'
    result = prepare_company_proposal(
        profile_dir=PROFILE,
        items=ORDER,
        output_path=output,
        on_date=date.today(),
        confirmed=confirmed,
    )
    assert output.is_file()
    assert result['quote']['grand_total'] == calculated['grand_total']
    document = Document(output)
    content = '\n'.join(p.text for p in document.paragraphs)
    assert calculated['grand_total'] in content
    assert any('Установка операционной системы' in cell.text
               for table in document.tables for row in table.rows for cell in row.cells)
    assert '{{' not in content


def test_calculated_values_cannot_be_forged(tmp_path):
    """Проверенные суммы нельзя заменить значениями из пользовательского ввода."""
    with pytest.raises(ProposalError, match='нельзя изменить'):
        prepare_company_proposal(
            profile_dir=PROFILE, items=ORDER,
            output_path=tmp_path / 'kp.docx', on_date=date.today(),
            confirmed={'итого_к_оплате': '0.01'},
        )
