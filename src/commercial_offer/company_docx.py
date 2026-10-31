"""Заполнение русскоязычных шаблонов коммерческих предложений."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from docx import Document

from .proposal import ProposalError

_TOKEN = re.compile(r'\{\{([^{}]+)\}\}')


def _paragraphs(document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def generate_company_docx(*, template_path: str | Path, output_path: str | Path,
                          quote: Mapping[str, Any], content: Mapping[str, str]) -> Path:
    """Подставляет данные и проверяет, что в документе не осталось пустых полей."""
    template = Path(template_path)
    if not template.is_file():
        raise ProposalError('Шаблон коммерческого предложения не найден')
    if not quote.get('items') or not quote.get('grand_total'):
        raise ProposalError('Расчёт заказа отсутствует или неполный')
    required = ('описание_задачи_клиента', 'обоснование_выбора_решения',
                'соответствие_требованиям', 'итого_работы', 'итого_оборудование')
    if any(not content.get(field) for field in required):
        raise ProposalError('Не заполнены обязательные разделы коммерческого предложения')
    document = Document(template)
    items = quote['items']
    discounts = str(quote.get('discount_total', '0.00'))
    totals = {
        'дата_КП': date.today().strftime('%d.%m.%Y'),
        'скидка_процент': 'см. таблицу',
        'скидка_сумма': discounts,
        'итого_к_оплате': quote['grand_total'],
        'НДС': str(sum((Decimal(i.get('tax_included', '0')) for i in items), Decimal(0))),
    }
    values = {**totals, **content}
    # Плейсхолдеры обязательны: неизвестные значения не подменяем выдуманными.
    missing = set()
    for paragraph in _paragraphs(document):
        tokens = _TOKEN.findall(paragraph.text)
        for token in tokens:
            if token not in values or values[token] is None or str(values[token]).strip() == '':
                missing.add(token)
        if tokens and not any(token in missing for token in tokens):
            paragraph.text = _TOKEN.sub(lambda m: str(values[m.group(1)]), paragraph.text)
    if missing:
        raise ProposalError('Не заполнены поля шаблона: ' + ', '.join(sorted(missing)))
    # Таблица состава добавляется в конце документа, чтобы не менять исходный шаблон.
    document.add_heading('Состав коммерческого предложения', level=2)
    table = document.add_table(rows=1, cols=5)
    table.style = 'Table Grid'
    for cell, heading in zip(table.rows[0].cells, ('Наименование', 'Количество', 'Цена', 'Скидка', 'Сумма')):
        cell.text = heading
    for item in items:
        values_row = (item['name'], str(item['quantity']), str(item['unit_price']),
                      str(item['discount_amount']), str(item['total']))
        for cell, value in zip(table.add_row().cells, values_row):
            cell.text = value
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(target)
    Document(target)
    return target
