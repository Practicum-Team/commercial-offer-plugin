"""Интеграционная проверка: выбранная позиция -> расчёт -> документ."""
from datetime import date
from pathlib import Path

import pytest
from docx import Document

from commercial_offer.config import Config
from commercial_offer.document_preparation import template_fields
from commercial_offer.proposal_pipeline import prepare_from_search, _is_work, _discount_card
from commercial_offer.company_docx import _validate_quote_totals
from commercial_offer.proposal import ProposalError

ROOT = Path(__file__).resolve().parents[1]


def test_categories_and_negative_card():
    assert _is_work('Программное обеспечение')
    assert _is_work('Аппаратное обеспечение (ноутбук)')
    assert not _is_work('Комплектующие')
    assert not _is_work('Расходные материалы')
    assert not _discount_card({'запрос': 'Скидочная карта отсутствует'})


def test_rejects_wrong_quote_amount():
    quote = {'subtotal': '100.00', 'discount_total': '10.00', 'grand_total': '1.00'}
    rows = [{'subtotal': '100.00', 'discount_amount': '10.00', 'total': '90.00'}]
    with pytest.raises(ProposalError, match='не совпадают'):
        _validate_quote_totals(quote, rows)


def test_real_profile_proposal_from_selected_products(tmp_path):
    cfg = Config(profiles_dir=ROOT / 'profiles', config_path=tmp_path / 'config.json')
    profile_id = 'company_comp_service'
    selected = {'profile_id': profile_id, 'ожидаемые_товары': [
        {'product_id': '32', 'количество': 1},
    ]}
    request = {'profile_id': profile_id, 'дата покупки': '2026-11-02',
               'запрос': 'Нужна полная чистка ноутбука. Дисконтной карты нет.',
               'наименование': 'ТЕСТОВЫЙ ЗАКАЗЧИК'}
    template = ROOT / 'profiles' / profile_id / 'template.docx'
    confirmed = {key: f'ТЕСТОВЫЕ ДАННЫЕ: {key}' for key in template_fields(template)
                 if key not in {'дата_КП', 'скидка_процент', 'скидка_сумма', 'итого_к_оплате', 'НДС'}}
    result = prepare_from_search(config=cfg, search_results=selected,
                                 request=request, confirmed=confirmed,
                                 output_path=tmp_path / 'kp.docx')
    assert result['quote']['grand_total'] == '1400.00'
    assert result['quote']['discount_total'] == '0.00'
    doc = Document(result['document_path'])
    full_text = '\n'.join(p.text for p in doc.paragraphs)
    assert '1400.00' in full_text
    assert '02.11.2026' in full_text
    assert any('Полная чистка ноутбука' in c.text for t in doc.tables for r in t.rows for c in r.cells)
