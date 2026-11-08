"""LLM-driven сопоставление файлов/полей и валидация перед загрузкой в SQLite."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import importer
from .schemas import (
    ALL_KINDS, FIELD_ALIASES, KIND_CASES, KIND_CATALOG, KIND_PRICES,
    KIND_PROFILE, KIND_RULES, KIND_TEMPLATE, REQUIRED_FIELDS,
    FileSchema, ProfileSchema
)


# ---------- 1. Сбор отпечатков и запрос LLM ----------

@dataclass
class InspectResult:
    profile_name: str
    fingerprints: list[dict]
    llm_prompt: str
    errors: list[dict] = field(default_factory=list)


def inspect(profile_name: str, paths: list[Path]) -> InspectResult:
    fps = importer.fingerprint_all(paths)
    errors = [{"file": fp.filename, "reason": fp.error}
              for fp in fps if fp.error]
    valid = [fp.to_dict() for fp in fps if not fp.error]
    prompt = _build_llm_prompt(profile_name, valid)
    return InspectResult(profile_name, valid, prompt, errors)


def _build_llm_prompt(profile_name: str, fps: list[dict]) -> str:
    return (
        "Ты сопоставляешь файлы профиля компании с назначением. "
        "Доступные назначения: " + ", ".join(sorted(ALL_KINDS)) + ".\n"
        "Для каждого файла верни JSON с полями:\n"
        "  filename, kind, field_map (логическое_поле -> столбец_в_файле), "
        "payload_columns (столбцы без явной роли).\n"
        "Логические поля: " + ", ".join(sorted(FIELD_ALIASES)) + ".\n"
        "Синонимы столбцов: " + json.dumps(FIELD_ALIASES, ensure_ascii=False) + ".\n"
        "Ответ — единый JSON: {\"profile_name\": ..., \"files\": [...]}.\n"
        f"profile_name = {profile_name!r}.\n"
        "Данные файлов:\n" + json.dumps(fps, ensure_ascii=False, indent=2)
    )


# ---------- 2. Проверка схемы, полученной от LLM, и структура файлов ----------

@dataclass
class StructureCheck:
    ok: bool
    errors: list[dict] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)


def check_schema(profile_name: str, schema: ProfileSchema,
                 files_on_disk: dict[str, Path]) -> StructureCheck:
    errors: list[dict] = []
    seen_kinds: dict[str, int] = {}

    for fs in schema.files:
        if fs.filename not in files_on_disk:
            errors.append({"file": fs.filename, "reason": "файл не найден"})
            continue
        if fs.kind not in ALL_KINDS:
            errors.append({"file": fs.filename, "reason": f"неизвестное назначение {fs.kind!r}"})
            continue
        seen_kinds[fs.kind] = seen_kinds.get(fs.kind, 0) + 1

        path = files_on_disk[fs.filename]

        # Для JSON и Markdown правила читаются из содержимого файла, а не из field_map.
        required = REQUIRED_FIELDS.get(fs.kind, [])
        if not (fs.kind == KIND_RULES and path.suffix.lower() in (".json", ".md")):
            for req in required:
                alternatives = req.split("|")
                if not any(alt in fs.field_map for alt in alternatives):
                    errors.append({
                        "file": fs.filename,
                        "reason": f"нет обязательного поля {req}",
                    })

        # карта полей ссылается на реальные столбцы файла
        if path.suffix.lower() in (".csv", ".xlsx"):
            columns, _ = importer.read_table(path)
            unknown = [col for col in fs.field_map.values() if col not in columns]
            if unknown:
                errors.append({
                    "file": fs.filename,
                    "reason": f"в field_map указаны отсутствующие столбцы: {unknown}",
                })

    for required_kind in (KIND_CATALOG, KIND_PRICES, KIND_RULES):
        if required_kind not in seen_kinds:
            errors.append({"file": None, "reason": f"нет файла назначения {required_kind!r}"})

    duplicated = [k for k, n in seen_kinds.items() if n > 1 and k != KIND_CASES]
    for k in duplicated:
        errors.append({"file": None, "reason": f"назначение {k!r} назначено более одного раза"})

    return StructureCheck(ok=not errors, errors=errors)


# ---------- 3. Содержательные проверки ----------

@dataclass
class ContentCheck:
    ok: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    prices: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)


def _iter_file_rows(profile_name: str, fs: FileSchema, path: Path) -> Iterable[dict]:
    for row_no, raw in importer.iter_rows(path):
        norm = importer.normalize_row(raw, fs.field_map)
        # Дополнительные столбцы сохраняются отдельно, чтобы не терять характеристики товара.
        norm["_payload_columns"] = list(fs.payload_columns)
        for column in fs.payload_columns:
            if column in raw:
                norm[column] = raw[column]
        norm["_source_file"] = path.name
        norm["_source_row"] = row_no
        yield norm


def check_content(profile_name: str, schema: ProfileSchema,
                  files_on_disk: dict[str, Path]) -> ContentCheck:
    errors: list[dict] = []
    warnings: list[dict] = []
    products: list[dict] = []
    prices: list[dict] = []
    rules: list[dict] = []
    cases: list[dict] = []

    product_ids: set[str] = set()

    for fs in schema.files:
        path = files_on_disk[fs.filename]
        if fs.kind in (KIND_PROFILE, KIND_TEMPLATE):
            continue

        if fs.kind in (KIND_CATALOG, KIND_PRICES) and path.suffix.lower() not in (".csv", ".xlsx"):
            errors.append({"file": path.name, "reason": f"{fs.kind} должен быть csv/xlsx"})
            continue

        if fs.kind == KIND_CASES:
            cases.extend(_parse_cases(profile_name, fs, path, errors))
            continue
        if fs.kind == KIND_RULES and path.suffix.lower() in (".json", ".md"):
            rules.extend(_parse_rules_markdown_json(profile_name, fs, path, errors))
            continue

        # табличные назначения
        for row in _iter_file_rows(profile_name, fs, path):
            if fs.kind == KIND_CATALOG:
                pid = row.get("product_id")
                if not pid:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустой product_id"})
                    continue
                if not row.get("name"):
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустое название"})
                if pid in product_ids:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": f"дубль product_id={pid}"})
                    continue
                product_ids.add(pid)
                products.append(row)

            elif fs.kind == KIND_PRICES:
                pid = row.get("product_id")
                amount = row.get("amount")
                currency = row.get("currency")
                valid_from = row.get("valid_from")
                valid_until = row.get("valid_until")
                if not pid:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустой product_id"})
                    continue
                if amount is None:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "цена не число"})
                if not currency:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустая валюта"})
                if not valid_from and not valid_until:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "нет срока действия цены"})
                for k in ("valid_from", "valid_until"):
                    if row.get(k) and not _looks_like_date(row[k]):
                        errors.append({"file": path.name, "row": row["_source_row"],
                                       "reason": f"{k} не дата: {row[k]!r}"})
                if valid_from and valid_until and valid_from > valid_until:
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "valid_from > valid_until"})
                prices.append(row)

            elif fs.kind == KIND_RULES:
                if not row.get("description"):
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустой текст правила"})
                if not row.get("applies_to"):
                    errors.append({"file": path.name, "row": row["_source_row"],
                                   "reason": "пустая область применения"})
                rules.append(row)

    # проверка ссылочной целостности цен на каталог
    for p in prices:
        if p.get("product_id") and p["product_id"] not in product_ids:
            errors.append({
                "file": p["_source_file"], "row": p["_source_row"],
                "reason": f"product_id={p['product_id']!r} отсутствует в каталоге",
            })

    return ContentCheck(ok=not errors, errors=errors, warnings=warnings,
                        products=products, prices=prices, rules=rules, cases=cases)


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    try:
        datetime.fromisoformat(str(value))
        return True
    except ValueError:
        return False


import re

_CASE_HEADER = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)
_SEPARATOR = re.compile(r"^\s*---\s*$", re.MULTILINE)


def _split_markdown_cases(text: str) -> list[tuple[str, str]]:
    """Разбивает Markdown на (title, body). Кейс = блок от '## ' до следующего '## '
    (или до конца файла). Горизонтальные '---' внутри блока сохраняются,
    но идущие сразу после заголовка/перед следующим — отрезаются."""
    matches = list(_CASE_HEADER.finditer(text))
    if not matches:
        # нет H2 — попробуем делить по '---' как fallback
        chunks = [c.strip() for c in _SEPARATOR.split(text) if c.strip()]
        return [(f"case_{i}", c) for i, c in enumerate(chunks, start=1)]

    out: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # отрезать один хвостовой/начальный '---'
        body = _SEPARATOR.sub("", body, count=1).strip()
        body = _SEPARATOR.sub("", body[::-1], count=1)[::-1].strip() if body.endswith("---") else body
        out.append((m.group("title").strip(), body))
    return out


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", title.lower(), flags=re.UNICODE).strip("_")
    return s or "case"


def _parse_cases(profile_name: str, fs: FileSchema, path: Path,
                 errors: list[dict]) -> list[dict]:
    out: list[dict] = []
    if path.suffix.lower() == ".md":
        text = importer.read_text(path)
        blocks = _split_markdown_cases(text)
        is_fictional_file = int("вымышлен" in text.lower() or "fictional" in text.lower())
        for idx, (title, body) in enumerate(blocks, start=1):
            if not body:
                continue
            # каждый кейс — отдельная запись; id формируем из заголовка
            case_id = f"{path.stem}_{idx}_{_slug(title)}"[:120]
            # признак вымышленности — по заголовку/телу кейса, иначе по файлу
            local_text = (title + "\n" + body).lower()
            is_fictional = int(
                "вымышлен" in local_text or "fictional" in local_text
                or is_fictional_file
            )
            out.append({
                "case_id": case_id,
                "text": f"## {title}\n\n{body}".strip(),
                "is_fictional": is_fictional,
                "_source_file": path.name,
            })
        if not out:
            errors.append({"file": path.name, "reason": "не удалось выделить кейсы"})
        return out

    # табличные файлы (csv/xlsx) — по строке на кейс
    for row in _iter_file_rows(profile_name, fs, path):
        case_id = row.get("case_id") or f"{path.stem}_{row['_source_row']}"
        text = row.get("description") or ""
        if not text:
            errors.append({"file": path.name, "row": row["_source_row"],
                           "reason": "пустой текст кейса"})
            continue
        out.append({
            "case_id": case_id,
            "text": text,
            "is_fictional": int(bool(row.get("is_fictional"))),
            "_source_file": path.name,
        })
    return out


def _parse_rules_markdown_json(profile_name: str, fs: FileSchema, path: Path,
                               errors: list[dict]) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "rules" in data:
            data = data["rules"]
        if not isinstance(data, list):
            errors.append({"file": path.name, "reason": "rules json должен содержать массив"})
            return []
        out = []
        for i, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                errors.append({"file": path.name, "row": i, "reason": "правило должно быть объектом"})
                continue
            desc = item.get("description") or item.get("text") or ""
            applies_to = item.get("applies_to") or item.get("scope") or ""
            if not desc:
                errors.append({"file": path.name, "row": i, "reason": "нет описания правила"})
            if not applies_to:
                errors.append({"file": path.name, "row": i, "reason": "нет области применения"})

            # В готовых профилях условия уже могут храниться в поле conditions_json.
            conditions = item.get("conditions_json", item.get("conditions", {}))
            if isinstance(conditions, str):
                try:
                    conditions = json.loads(conditions)
                except json.JSONDecodeError:
                    errors.append({"file": path.name, "row": i, "reason": "conditions_json содержит некорректный JSON"})
                    conditions = {}
            if not isinstance(conditions, dict):
                errors.append({"file": path.name, "row": i, "reason": "условия правила должны быть объектом"})
                conditions = {}

            out.append({
                "rule_id": item.get("rule_id") or f"{path.stem}_{i}",
                "applies_to": applies_to or "all",
                "rule_type": item.get("rule_type") or item.get("type") or "discount",
                "conditions_json": json.dumps(conditions, ensure_ascii=False),
                "description": desc,
                "_source_file": path.name,
            })
        return out

    # markdown: один блок = одно правило
    text = importer.read_text(path)
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    out = []
    for i, block in enumerate(blocks, start=1):
        out.append({
            "rule_id": f"{path.stem}_{i}",
            "applies_to": "all",
            "rule_type": "discount",
            "conditions_json": "{}",
            "description": block,
            "_source_file": path.name,
        })
    return out
