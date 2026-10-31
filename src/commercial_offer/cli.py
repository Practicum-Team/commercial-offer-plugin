"""Командный интерфейс. Агент вызывает только эти подкоманды (без прямых FS-операций)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .importer import ProfileLoader, check_file

CONFIG = Config.load()


def _emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def cmd_profiles_list(_: argparse.Namespace) -> int:
    return _emit({"profiles": CONFIG.list_profiles(), "active": CONFIG.active_profile})


def cmd_profiles_use(args: argparse.Namespace) -> int:
    loader = ProfileLoader(CONFIG)
    try:
        result = loader.switch_profile(args.name)
        CONFIG.save_active_profile()
        return _emit(result)
    except KeyError as exc:
        return _emit({"status": "error", "error": str(exc)}, code=2)


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


def cmd_profiles_load(args: argparse.Namespace) -> int:
    loader = ProfileLoader(CONFIG)
    sources = [Path(p) for p in args.file]
    explicit_dir = Path(args.dir) if args.dir else None
    overwrite = None
    if args.overwrite is not None:
        overwrite = args.overwrite == "yes"
    result = loader.load(args.name, sources, explicit_dir=explicit_dir, overwrite=overwrite)
    # После успешной загрузки профиль становится активным
    if result["status"] == "ok":
        loader.switch_profile(args.name)
        CONFIG.save_active_profile()
    code = 0 if result["status"] == "ok" else 3
    return _emit(result, code=code)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="commercial-offer")
    sub = p.add_subparsers(dest="cmd", required=True)

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
