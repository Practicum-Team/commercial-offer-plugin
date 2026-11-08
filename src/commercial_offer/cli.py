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
from .proposal_pipeline import prepare_from_search
from .instrumentation import instrumented, set_last_emitted

CONFIG = Config.load()


def _emit(payload: dict, code: int = 0) -> int:
    set_last_emitted(payload)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
    if args.db_url:
        return args.db_url
    entry = args.config.profiles.get(args.name)
    if entry is None:
        raise ValueError(f"Профиль не зарегистрирован: {args.name}")
    return storage.default_db_url(entry.path)


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


def cmd_proposal_prepare(args: argparse.Namespace) -> int:
    try:
        search_results = json.loads(Path(args.search_results).read_text(encoding="utf-8"))
        request = json.loads(Path(args.request_schema).read_text(encoding="utf-8"))
        confirmed = (
            json.loads(Path(args.confirmed).read_text(encoding="utf-8"))
            if args.confirmed else None
        )
        result = prepare_from_search(
            config=args.config,
            search_results=search_results,
            request=request,
            output_path=Path(args.output),
            confirmed=confirmed,
        )
    except Exception as exc:
        return _emit({"ok": False, "error": str(exc)}, code=1)
    return _emit({"ok": True, **result})


def cmd_template_fields(args: argparse.Namespace) -> int:
    from .document_preparation import template_fields
    from .proposal import ProposalError
    try:
        fields = template_fields(args.config.profile_path() / "template.docx")
    except ProposalError as exc:
        return _emit({"ok": False, "error": str(exc)}, code=1)
    return _emit({"ok": True, "fields": fields})


def cmd_runs_start(args: argparse.Namespace) -> int:
    run_id = getattr(args, "_new_run_id", None)
    if not run_id:
        return _emit({"ok": False, "error": "внутренняя ошибка: run_id не создан"}, code=3)

    payload = None
    if args.input_json:
        try:
            payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        except Exception as exc:
            return _emit({"ok": False, "error": f"input-json: {exc}"}, code=2)

    db_url = _resolve_db_url(args)
    res = storage.start_run(db_url, args.name, args.input_text, payload, run_id=run_id)
    return _emit(res, code=0 if res["ok"] else 1)


def cmd_runs_finish(args: argparse.Namespace) -> int:
    payload = None
    if args.result_json:
        try:
            payload = json.loads(Path(args.result_json).read_text(encoding="utf-8"))
        except Exception as exc:
            return _emit({"ok": False, "error": f"result-json: {exc}"}, code=2)

    db_url = _resolve_db_url(args)
    res = storage.finish_run(
        db_url, args.run_id,
        result_kind=args.result_kind,
        result_json=payload,
        document_path=args.document_path,
    )
    if res.get("ok"):
        try:
            args.config.clear_active_run(args.name, args.run_id)
        except Exception as exc:
            res["warning"] = f"active_run не очищен в config.json: {exc}"
    return _emit(res, code=0 if res.get("ok") else 1)


def cmd_runs_show(args: argparse.Namespace) -> int:
    res = storage.get_run(_resolve_db_url(args), args.run_id)
    if res is None:
        return _emit({"ok": False, "error": "run_id не найден"}, code=1)
    return _emit({"ok": True, **res})


def cmd_profile_loads(args: argparse.Namespace) -> int:
    loads = storage.list_profile_loads(_resolve_db_url(args), args.name)
    return _emit({"ok": True, "profile": args.name, "loads": loads})
# ---------- парсер ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commercial-offer")
    sub = p.add_subparsers(dest="cmd", required=True)

    def reg(name: str, func, help: str | None = None):
        sp = sub.add_parser(name, help=help)
        return sp

    # --- файлы профиля ---
    sp = reg("profiles-list", cmd_profiles_list, "Список профилей")
    sp.set_defaults(func=instrumented("profiles-list")(cmd_profiles_list))

    sp = reg("profiles-use", cmd_profiles_use, "Переключить активный профиль")
    sp.add_argument("name")
    sp.set_defaults(func=instrumented("profiles-use")(cmd_profiles_use))

    sp = reg("profiles-check", cmd_profiles_check, "Проверить файлы без копирования")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=instrumented("profiles-check")(cmd_profiles_check))

    sp = reg("profiles-load", cmd_profiles_load, "Загрузить документы в профиль")
    sp.add_argument("name")
    sp.add_argument("file", nargs="+")
    sp.add_argument("--dir", help="Явный каталог назначения профиля")
    sp.add_argument("--overwrite", choices=["yes", "no"], default=None)
    sp.set_defaults(func=instrumented("profiles-load")(cmd_profiles_load))

    # --- импорт в SQLite ---
    sp = reg("profiles-inspect", cmd_profiles_inspect, "Отпечатки + промпт LLM")
    sp.add_argument("name")
    sp.add_argument("file", nargs="+")
    sp.set_defaults(func=instrumented("profiles-inspect")(cmd_profiles_inspect))

    sp = reg("profiles-apply-schema", cmd_profiles_apply_schema, "Проверить схему LLM")
    sp.add_argument("name"); sp.add_argument("schema_file"); sp.add_argument("file", nargs="+")
    sp.set_defaults(func=instrumented("profiles-apply-schema")(cmd_profiles_apply_schema))

    sp = reg("profiles-validate", cmd_profiles_validate, "Содержательные проверки")
    sp.add_argument("name"); sp.add_argument("schema_file"); sp.add_argument("file", nargs="+")
    sp.set_defaults(func=instrumented("profiles-validate")(cmd_profiles_validate))

    sp = reg("profiles-confirm", cmd_profiles_confirm, "Подтверждение схемы/полей/commit")
    sp.add_argument("name"); sp.add_argument("schema_file")
    sp.add_argument("kind", choices=["schema", "fields", "commit"])
    sp.add_argument("--yes", action="store_true"); sp.add_argument("--note", default=None)
    sp.set_defaults(func=instrumented("profiles-confirm")(cmd_profiles_confirm))

    sp = reg("profiles-commit", cmd_profiles_commit, "Загрузка в SQLite")
    sp.add_argument("name"); sp.add_argument("schema_file"); sp.add_argument("file", nargs="+")
    sp.add_argument("--db-url", default=None); sp.add_argument("--profile-json", default=None)
    sp.set_defaults(func=instrumented("profiles-commit")(cmd_profiles_commit))

    # --- поиск ---
    sp = reg("search-products", cmd_search_products, "Кандидаты products+prices")
    sp.add_argument("name"); sp.add_argument("--currency", default=None)
    sp.add_argument("--budget", default=None); sp.add_argument("--purchase-date", default=None)
    sp.add_argument("--category", default=None); sp.add_argument("--db-url", default=None)
    sp.add_argument("--run-id", default=None)
    sp.set_defaults(func=instrumented("search-products")(cmd_search_products))

    sp = reg("search-dump", cmd_search_dump, "Полный дамп products+prices")
    sp.add_argument("name"); sp.add_argument("--currency", default=None)
    sp.add_argument("--db-url", default=None); sp.add_argument("--run-id", default=None)
    sp.set_defaults(func=instrumented("search-dump")(cmd_search_dump))

    sp = reg("search-rules", cmd_search_rules, "Правила по product_id/category/all")
    sp.add_argument("name"); sp.add_argument("--product-id", action="append", default=[])
    sp.add_argument("--category", action="append", default=[])
    sp.add_argument("--check-discount", action="store_true")
    sp.add_argument("--db-url", default=None); sp.add_argument("--run-id", default=None)
    sp.set_defaults(func=instrumented("search-rules")(cmd_search_rules))

    # --- КП ---
    sp = reg("proposal-prepare", cmd_proposal_prepare, "Расчёт + DOCX")
    sp.add_argument("search_results"); sp.add_argument("request_schema")
    sp.add_argument("--output", required=True); sp.add_argument("--confirmed", default=None)
    sp.add_argument("--run-id", default=None); sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=instrumented("proposal-prepare")(cmd_proposal_prepare))

    sp = reg("template-fields", cmd_template_fields, "Плейсхолдеры template.docx")
    sp.set_defaults(func=instrumented("template-fields")(cmd_template_fields))

    # --- runs / loads ---
    sp = reg("runs-start", cmd_runs_start, "Начать run")
    sp.add_argument("name"); sp.add_argument("--input-text", required=True)
    sp.add_argument("--input-json", default=None); sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=instrumented("runs-start")(cmd_runs_start))

    sp = reg("runs-finish", cmd_runs_finish, "Закрыть run")
    sp.add_argument("name"); sp.add_argument("run_id")
    sp.add_argument("result_kind", choices=["success", "needs_clarification", "refused"])
    sp.add_argument("--result-json", default=None); sp.add_argument("--document-path", default=None)
    sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=instrumented("runs-finish")(cmd_runs_finish))

    sp = reg("runs-show", cmd_runs_show, "Трассировка run")
    sp.add_argument("name"); sp.add_argument("run_id"); sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=instrumented("runs-show")(cmd_runs_show))

    sp = reg("profile-loads", cmd_profile_loads, "История загрузок профиля")
    sp.add_argument("name"); sp.add_argument("--db-url", default=None)
    sp.set_defaults(func=instrumented("profile-loads")(cmd_profile_loads))

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
