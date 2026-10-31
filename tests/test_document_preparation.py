"""Проверки подготовки данных для шаблона КП."""
from datetime import date
from pathlib import Path

import pytest

from commercial_offer.document_preparation import (missing_fields, prepared_content,
                                                   template_fields)
from commercial_offer.proposal import ProposalError

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'profiles' / 'company_comp_service' / 'template.docx'


def test_template_fields_are_detected():
    fields = template_fields(TEMPLATE)
    assert 'ФИО_контактного_лица' in fields
    assert 'итого_к_оплате' in fields


def test_known_profile_data_are_used_without_inventing_contacts():
    settings = {'company': {'name': 'Тестовая компания', 'contact_person': 'Петров Иван Сергеевич'}}
    quote = {'discount_total': '10.00', 'grand_total': '90.00', 'items': [{'tax_included': '16.23'}]}
    values = prepared_content(settings, quote, on_date=date(2026, 11, 1))
    assert values['ФИО_контактного_лица'] == 'Петров Иван Сергеевич'
    assert values['НДС'] == '16.23'
    assert values['итого_к_оплате'] == '90.00'
    assert 'email' in missing_fields(TEMPLATE, values)
    assert 'ФИО_подписанта' in missing_fields(TEMPLATE, values)


def test_calculated_total_cannot_be_overridden():
    with pytest.raises(ProposalError, match='нельзя изменить'):
        prepared_content({'company': {}}, {'discount_total': '0.00', 'grand_total': '80.00', 'items': []},
                         on_date=date(2026, 11, 1), confirmed={'итого_к_оплате': '50.00'})
