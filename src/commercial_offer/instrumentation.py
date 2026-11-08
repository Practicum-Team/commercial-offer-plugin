"""Автоматическая инструментация CLI: каждый вызов → run_events.

Правила run_id:
  - runs-start и profiles*  → новый run_id (uuid) до логирования.
  - остальные команды       → _resolve_run_id: --run-id → env → последний
                              открытый run профиля из БД.
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from . import storage


_LAST_EMITTED: dict = {}

# Команды, которые ВСЕГДА открывают новую цепочку в логе.
NEW_RUN_COMMANDS = {
    "runs-start",
    "profiles-list", "profiles-use", "profiles-check", "profiles-load",
    "profiles-inspect", "profiles-apply-schema", "profiles-validate",
    "profiles-confirm", "profiles-commit",
}


def set_last_emitted(payload: dict) -> None:
    global _LAST_EMITTED
    _LAST_EMITTED = payload or {}


def get_last_emitted() -> dict:
    return _LAST_EMITTED


def _public_args(args: argparse.Namespace) -> dict:
    skip = {"config", "loader", "func"}
    out: dict = {}
    for k, v in vars(args).items():
        if k in skip:
            continue
        if isinstance(v, Path):
            out[k] = str(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) if isinstance(x, Path) else x for x in v]
        else:
            out[k] = v
    return out


def _profile_id(args: argparse.Namespace) -> str | None:
    return (
        getattr(args, "name", None)
        or (getattr(args.config, "active_profile", None)
            if getattr(args, "config", None) else None)
    )


def _resolve_db_url(args: argparse.Namespace, profile_id: str | None) -> str | None:
    if getattr(args, "db_url", None):
        return args.db_url
    config = getattr(args, "config", None)
    if config is None or not profile_id:
        return None
    entry = config.profiles.get(profile_id)
    if entry is None:
        return None
    return storage.default_db_url(entry.path)


def _new_run_id(args: argparse.Namespace, profile_id: str | None) -> str:
    """Новая цепочка. Для runs-start регистрируем её в таблице runs,
    для profiles* — только генерируем id (сама run-строка не нужна)."""
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    db_url = _resolve_db_url(args, profile_id)
    if db_url and getattr(args, "_command", None) == "runs-start":
        # runs-start сам создаёт строку в runs внутри обработчика;
        # здесь ничего не пишем, чтобы не дублировать.
        pass
    return run_id


def _resolve_run_id(args: argparse.Namespace, profile_id: str | None) -> str | None:
    """Существующая цепочка: --run-id → env → последний открытый run."""
    explicit = getattr(args, "run_id", None) or os.environ.get("CO_ITEM_RUN_ID")
    if explicit:
        return explicit
    db_url = _resolve_db_url(args, profile_id)
    if not db_url or not profile_id:
        return None
    try:
        return storage.latest_open_run_id(db_url, profile_id)
    except Exception:
        return None


def instrumented(cmd_name: str) -> Callable:
    def wrapper(func):
        @functools.wraps(func)
        def inner(args: argparse.Namespace) -> int:
            set_last_emitted({})
            args._command = cmd_name

            profile_id = _profile_id(args)
            db_url = _resolve_db_url(args, profile_id)

            # 1) выбор/создание run_id в зависимости от команды
            if cmd_name in NEW_RUN_COMMANDS:
                run_id = _new_run_id(args, profile_id)
                args._new_run_id = run_id
            else:
                run_id = _resolve_run_id(args, profile_id)
                args.run_id = run_id

            started = time.monotonic()
            code, error = 0, None
            try:
                code = func(args)
                return code
            except SystemExit:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                code = 1
                raise
            finally:
                duration_ms = int((time.monotonic() - started) * 1000)
                # после func: обработчик мог сам создать/закрыть run и вернуть run_id
                emitted = get_last_emitted()
                final_run_id = (
                    run_id
                    or emitted.get("run_id")
                    or getattr(args, "_new_run_id", None)
                    or ""
                )
                if db_url:
                    try:
                        storage.log_event(
                            db_url,
                            run_id=final_run_id or "",
                            profile_id=profile_id or "",
                            command=cmd_name,
                            args=_public_args(args),
                            ok=(code == 0 and error is None),
                            exit_code=code,
                            output=emitted,
                            error=error,
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        print(f"[instrumentation] log_event failed: "
                              f"{traceback.format_exc(limit=1)}",
                              file=sys.stderr)
        return inner
    return wrapper