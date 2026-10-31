"""Сквозная подготовка коммерческого предложения из данных компании."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .advanced_quote import calculate_company_quote
from .company_docx import generate_company_docx
from .document_preparation import missing_fields, prepared_content
from .profile_adapter import load_profile
from .proposal import ProposalError


def prepare_company_proposal(
    *,
    profile_dir: str | Path,
    items: list[dict[str, Any]],
    output_path: str | Path,
    on_date: date,
    confirmed: Mapping[str, str] | None = None,
    discount_card: bool = False,
    budget: str | None = None,
) -> dict[str, Any]:
    """Рассчитывает заказ и создаёт DOCX только при наличии всех обязательных сведений."""
    folder = Path(profile_dir)
    profile = load_profile(folder)
    quote = calculate_company_quote(
        profile,
        items,
        on_date=on_date,
        discount_card=discount_card,
        budget=budget,
    )
    template = folder / 'template.docx'
    if not template.is_file():
        raise ProposalError('В профиле компании отсутствует шаблон коммерческого предложения')

    values = prepared_content(profile['settings'], quote, on_date=on_date, confirmed=confirmed)
    absent = missing_fields(template, values)
    if absent:
        raise ProposalError('Не заполнены поля шаблона: ' + ', '.join(absent))

    # Генератор повторно заполняет дату самостоятельно, поэтому возвращаемая дата
    # должна совпадать с датой формирования документа. Не допускаем тихой подмены.
    if on_date != date.today():
        raise ProposalError('Для создания документа используйте текущую дату; расчёт за другую дату доступен отдельно')

    target = Path(output_path)
    created = generate_company_docx(
        template_path=template,
        output_path=target,
        quote=quote,
        content=values,
    )
    return {'quote': quote, 'document_path': str(created), 'missing_fields': []}
