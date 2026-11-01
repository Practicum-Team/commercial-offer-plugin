"""Подготовка проверенных данных для шаблона коммерческого предложения."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

from docx import Document

from .proposal import ProposalError

_TOKEN = re.compile(r"\{\{([^{}]+)\}\}")


def _paragraphs(document):
    """Перебирает абзацы документа, включая таблицы, колонтитулы."""
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in document.sections:
        for part in (section.header, section.footer):
            for paragraph in part.paragraphs:
                yield paragraph
            for table in part.tables:
                for row in table.rows:
                    for cell in row.cells:
                        yield from cell.paragraphs


def template_fields(template_path: str | Path) -> list[str]:
    """Возвращает названия всех полей шаблона без повторений."""
    path = Path(template_path)
    if not path.is_file():
        raise ProposalError('Шаблон коммерческого предложения не найден')
    document = Document(path)
    return sorted({field for paragraph in _paragraphs(document)
                   for field in _TOKEN.findall(paragraph.text)})


def prepared_content(profile_settings: Mapping[str, Any], quote: Mapping[str, Any],
                     *, on_date: date, confirmed: Mapping[str, str] | None = None) -> dict[str, str]:
    """Собирает только сведения из профиля, расчёта и подтверждённого ввода."""
    company = profile_settings.get('company') or {}
    tax = sum((Decimal(str(item['tax_included'])) for item in quote.get('items', [])), Decimal('0'))
    result = {
        'дата_КП': on_date.strftime('%d.%m.%Y'),
        'скидка_процент': 'по позициям',
        'скидка_сумма': str(quote['discount_total']),
        'итого_к_оплате': str(quote['grand_total']),
        'НДС': str(tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
    }
    # Контактное лицо компании и подписант — не одно и то же.
    if company.get('contact_person'):
        result['ФИО_контактного_лица'] = str(company['contact_person'])
    # Подтверждённые сведения можно добавлять, но нельзя заменять проверенные суммы.
    protected = {'дата_КП', 'скидка_процент', 'скидка_сумма', 'итого_к_оплате', 'НДС'}
    for key, value in (confirmed or {}).items():
        if key in protected and str(value) != result[key]:
            raise ProposalError(f'Поле «{key}» нельзя изменить вручную: значение получено расчётом')
        if value is not None and str(value).strip():
            result[key] = str(value)
    return result


def missing_fields(template_path: str | Path, values: Mapping[str, Any]) -> list[str]:
    """Находит незаполненные поля до создания документа."""
    return [field for field in template_fields(template_path)
            if field not in values or values[field] is None or not str(values[field]).strip()]
