"""Командный интерфейс. Агент вызывает только эти подкоманды (без прямых FS-операций)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .config import Config
from .importer import ProfileLoader, check_file


from . import search, storage, validator, confirmations
from .config import Config
from .schemas import ProfileSchema


CONFIG = Config.load()


def _emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def cmd_profiles_list(args: argparse.Namespace) -> int:
    return _emit({"profiles": args.config.list_profiles(),
                  "active": args.config.active_profile,
                  "config_path": str(args.config.config_path)})

def cmd_profiles_use(args: argparse.Namespace) -> int:
    try:
        return _emit(args.loader.switch_profile(args.name))
    except KeyError as exc:
        return _emit({"status": "error", "error": str(exc)}, code=2)

def cmd_profiles_load(args: argparse.Namespace) -> int:
    sources = [Path(p) for p in args.file]
    explicit_dir = Path(args.dir) if args.dir else None
    overwrite = None if args.overwrite is None else (args.overwrite == "yes")
    result = args.loader.load(args.name, sources,
                              explicit_dir=explicit_dir, overwrite=overwrite)
    # при needs_confirmation профиль не трогаем; при ok он уже активен и сохранён
    code = 0 if result["status"] == "ok" else 3
    return _emit(result, code=code)


def cmd_profiles_check(args: argparse.Namespace) -> int:
    results = []
    for raw in args.file:
        chk = check_file(Path(raw))
        results.append({
            "file": raw,
            "ok": chk.ok,
            "kind": chk.detected_kind,
            "reason": chk.reason,
        })
    return _emit({"files": results}, code=0 if all(r["ok"] for r in results) else 1)




"""CLI импорта профиля в SQLite. Агент вызывает только эти подкоманды.

Подкоманды:
  profiles-inspect       — собрать отпечатки файлов + промпт для LLM
  profiles-apply-schema  — проверить схему от LLM (назначения и field_map)
  profiles-validate      — содержательные проверки по схеме (без записи в БД)
  profiles-commit        — загрузка в SQLite после успешной валидации
"""


# ---------- утилиты ----------


def _load_schema(path: Path) -> ProfileSchema:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProfileSchema.from_dict(data)


def _files_map(paths: list[str]) -> dict[str, Path]:
    return {Path(p).name: Path(p) for p in paths}


# ---------- обработчики ----------

def cmd_profiles_inspect(args: argparse.Namespace) -> int:
    """Собрать отпечатки файлов и промпт для LLM."""
    paths = [Path(p) for p in args.file]
    res = validator.inspect(args.name, paths)
    return _emit({
        "profile": res.profile_name,
        "fingerprints": res.fingerprints,
        "errors": res.errors,
        "llm_prompt": res.llm_prompt,
    }, code=0 if not res.errors else 1)


def cmd_profiles_apply_schema(args: argparse.Namespace) -> int:
    """Проверить схему от LLM: назначения файлов и field_map."""
    try:
        schema = _load_schema(Path(args.schema_file))
    except Exception as exc:
        return _emit({"ok": False, "errors": [{"reason": f"schema не читается: {exc}"}]}, code=2)

    if schema.profile_name != args.name:
        return _emit({
            "ok": False,
            "errors": [{
                "reason": f"profile_name в схеме ({schema.profile_name!r}) "
                          f"не совпадает с --name ({args.name!r})",
            }],
        }, code=2)

    files_on_disk = _files_map(args.file)
    res = validator.check_schema(args.name, schema, files_on_disk)
    
    return _emit({
        "ok": res.ok,
        "errors": res.errors,
        "missing_kinds": res.missing_kinds,
        "schema": schema.to_dict(),
    }, code=0 if res.ok else 1)


def cmd_profiles_validate(args: argparse.Namespace) -> int:
    """Содержательные проверки по схеме: типы, дубли, ссылки. Без записи в БД."""
    try:
        schema = _load_schema(Path(args.schema_file))
    except Exception as exc:
        return _emit({"ok": False, "errors": [{"reason": f"schema не читается: {exc}"}]}, code=2)

    files_on_disk = _files_map(args.file)

    struct = validator.check_schema(args.name, schema, files_on_disk)
    if not struct.ok:
        return _emit({
            "ok": False,
            "stage": "schema",
            "errors": struct.errors,
        }, code=1)

    content = validator.check_content(args.name, schema, files_on_disk)
    return _emit({
        "ok": content.ok,
        "stage": "content",
        "errors": content.errors,
        "warnings": content.warnings,
        "counts": {
            "products": len(content.products),
            "prices": len(content.prices),
            "rules": len(content.rules),
            "cases": len(content.cases),
        },
    }, code=0 if content.ok else 1)


# --- обработчик ---

def cmd_profiles_confirm(args: argparse.Namespace) -> int:
    schema_data = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
    h = confirmations.schema_hash(schema_data)
    profile_dir = args.config.profile_path()
    item = confirmations.record(profile_dir, args.kind, h, args.yes, args.note)
    confirmations.audit(profile_dir, {
        "event": "confirm", "kind": args.kind,
        "confirmed": args.yes, "schema_hash": h,
    })
    return _emit({
        "ok": True,
        "kind": item.kind,
        "confirmed": item.confirmed,
        "schema_hash": h,
        "at": item.at,
    })

# --- cmd_profiles_commit: гейт перед записью в БД ---

def cmd_profiles_commit(args: argparse.Namespace) -> int:
    try:
        schema_data = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
    except Exception as exc:
        return _emit({"ok": False, "errors": [{"reason": f"schema не читается: {exc}"}]}, code=2)

    from .schemas import ProfileSchema
    schema = ProfileSchema.from_dict(schema_data)
    h = confirmations.schema_hash(schema_data)
    profile_dir = args.config.profile_path()

    # 1) Жёсткие гейты подтверждения
    ok_schema, why_schema = confirmations.check(profile_dir, confirmations.CONFIRM_SCHEMA, h)
    ok_fields, why_fields = confirmations.check(profile_dir, confirmations.CONFIRM_FIELDS, h)
    ok_commit, why_commit = confirmations.check(profile_dir, confirmations.CONFIRM_COMMIT, h)
    if not (ok_schema and ok_fields and ok_commit):
        confirmations.audit(profile_dir, {
            "event": "commit_blocked",
            "schema_hash": h,
            "reasons": [why_schema, why_fields, why_commit],
        })
        return _emit({
            "ok": False,
            "stage": "confirmation",
            "schema_hash": h,
            "errors": [
                {"kind": "schema", "reason": why_schema, "confirmed": ok_schema},
                {"kind": "fields", "reason": why_fields, "confirmed": ok_fields},
                {"kind": "commit", "reason": why_commit, "confirmed": ok_commit},
            ],
            "next": "Показать пользователю назначения файлов и field_map, "
                    "получить явное «да» и вызвать profiles-confirm "
                    "с --yes для kind=schema, затем kind=fields, затем kind=commit.",
        }, code=4)

    # 2) Валидация как раньше
    files_on_disk = _files_map(args.file)
    struct = validator.check_schema(args.name, schema, files_on_disk)
    if not struct.ok:
        return _emit({"ok": False, "stage": "schema", "errors": struct.errors}, code=1)

    content = validator.check_content(args.name, schema, files_on_disk)
    if not content.ok:
        return _emit({"ok": False, "stage": "content",
                      "errors": content.errors, "warnings": content.warnings}, code=1)

    profile_payload = None
    if args.profile_json:
        try:
            profile_payload = json.loads(Path(args.profile_json).read_text(encoding="utf-8"))
        except Exception as exc:
            return _emit({"ok": False, "errors": [{"reason": f"profile.json: {exc}"}]}, code=2)

    db_url = args.db_url or storage.default_db_url(args.config.profile_path())
    res = storage.commit_profile(
        profile_name=args.name, db_url=db_url,
        products=content.products, prices=content.prices,
        rules=content.rules, cases=content.cases,
        profile_payload=profile_payload,
    )
    confirmations.audit(profile_dir, {
        "event": "commit",
        "schema_hash": h, "db_url": db_url,
        "ok": res.ok, "counts": res.counts,
    })
    if not res.ok:
        return _emit({"ok": False, "errors": res.errors}, code=3)

    return _emit({
        "ok": True, "profile": args.name, "db_url": db_url,
        "counts": res.counts, "warnings": content.warnings,
        "schema_hash": h,
    })


# --- обработчики ---

def _resolve_db_url(args: argparse.Namespace) -> str:
    return args.db_url or storage.default_db_url(args.config.profile_path())


def cmd_search_products(args: argparse.Namespace) -> int:
    hits = search.search_products(
        _resolve_db_url(args), args.name,
        currency=args.currency, budget=args.budget,
        purchase_date=args.purchase_date, category=args.category,
    )
    return _emit({"ok": True, "count": len(hits), "products": hits})


def cmd_search_dump(args: argparse.Namespace) -> int:
    hits = search.dump_all_products(
        _resolve_db_url(args), args.name, currency=args.currency,
    )
    return _emit({"ok": True, "count": len(hits), "products": hits})


def cmd_search_rules(args: argparse.Namespace) -> int:
    rows = search.find_rules(
        _resolve_db_url(args), args.name,
        product_ids=args.product_id, categories=args.category,
        check_discount=args.check_discount,
    )
    return _emit({"ok": True, "count": len(rows), "rules": rows})


# ---------- парсер ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commercial-offer")
    sub = p.add_subparsers(dest="cmd", required=True)

    # --- жизненный цикл профиля (файлы) ---
    sp = sub.add_parser("profiles-list", help="Список профилей")
    sp.set_defaults(func=cmd_profiles_list)

    sp = sub.add_parser("profiles-use", help="Переключить активный профиль")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_profiles_use)

    sp = sub.add_parser("profiles-check", help="Проверить файлы без копирования")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=cmd_profiles_check)

    sp = sub.add_parser("profiles-load", help="Загрузить документы в профиль")
    sp.add_argument("name")
    sp.add_argument("file", nargs="+")
    sp.add_argument("--dir", help="Явный каталог назначения профиля")
    sp.add_argument("--overwrite", choices=["yes", "no"], default=None,
                    help="yes/no при коллизии имён; без флага — подтверждение у пользователя")
    sp.set_defaults(func=cmd_profiles_load)

    # --- импорт в SQLite (LLM-схема → валидация → commit) ---
    sp = sub.add_parser("profiles-inspect",
                        help="Отпечатки файлов + промпт для LLM")
    sp.add_argument("name")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=cmd_profiles_inspect)

    sp = sub.add_parser("profiles-apply-schema",
                        help="Проверить схему от LLM (назначения + field_map)")
    sp.add_argument("name")
    sp.add_argument("schema_file")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=cmd_profiles_apply_schema)

    sp = sub.add_parser("profiles-validate",
                        help="Содержательные проверки (типы, дубли, ссылки)")
    sp.add_argument("name")
    sp.add_argument("schema_file")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=cmd_profiles_validate)

    sp = sub.add_parser("profiles-commit",
                        help="Загрузка в SQLite (требует успешной валидации)")
    sp.add_argument("name")
    sp.add_argument("schema_file")
    sp.add_argument("file", nargs="+")
    sp.add_argument("--db-url",
                    help="Переопределить SQLite URL (по умолчанию profiles/<name>/profile.sqlite3)")
    sp.add_argument("--profile-json",
                    help="Путь к profile.json профиля (payload для таблицы profiles)")
    sp.set_defaults(func=cmd_profiles_commit)

    # --- добавления в build_parser() ---

    # --- новые подкоманды в build_parser() ---

    sp = sub.add_parser("profiles-confirm",
                        help="Зафиксировать подтверждение пользователя по схеме")
    sp.add_argument("name")
    sp.add_argument("schema_file")
    sp.add_argument("kind", choices=["schema", "fields", "commit"])
    sp.add_argument("--yes", action="store_true",
                    help="Пользователь подтвердил. Без этого флага записывается отказ.")
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_profiles_confirm)

    # --- поиск по БД ---
    sp = sub.add_parser("search-products", help="Поиск кандидатов products+prices")
    sp.add_argument("name")
    sp.add_argument("--currency", default=None)
    sp.add_argument("--budget", default=None)
    sp.add_argument("--purchase-date", default=None)
    sp.add_argument("--category", default=None)
    sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=cmd_search_products)

    sp = sub.add_parser("search-dump", help="Полный дамп products+prices (fallback)")
    sp.add_argument("name")
    sp.add_argument("--currency", default=None)
    sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=cmd_search_dump)

    sp = sub.add_parser("search-rules", help="Правила по product_id / category / all")
    sp.add_argument("name")
    sp.add_argument("--product-id", action="append", default=[])
    sp.add_argument("--category", action="append", default=[])
    sp.add_argument("--check-discount", action="store_true",
                    help="Вернуть все правила профиля (для проверки явной скидки)")
    sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=cmd_search_rules)

    return p

# ---------- точка входа ----------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Свежий конфиг на каждый запуск процесса: читает config.json и создаёт его при первом вызове
    config = Config.load()
    loader = ProfileLoader(config)
    args.config = config
    args.loader = loader
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
