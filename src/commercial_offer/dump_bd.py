"""Локальная отладка: полная выгрузка всех таблиц SQLite в JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from commercial_offer.config import Config  # noqa: E402


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def _resolve_db_url(profile: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    cfg = Config.load()
    name = profile or cfg.active_profile
    if not name:
        raise SystemExit("Профиль не выбран и --db не задан")
    entry = cfg.profiles.get(name)
    if entry is None:
        raise SystemExit(f"Профиль не зарегистрирован: {name}")
    return f"sqlite:///{entry.path / 'profile.sqlite3'}"


def dump_all(db_url: str) -> dict:
    engine = create_engine(db_url, future=True)
    insp = inspect(engine)
    tables: dict = {}
    with engine.connect() as conn:
        for table_name in insp.get_table_names():
            result = conn.execute(text(f'SELECT * FROM "{table_name}"'))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            tables[table_name] = {"columns": columns, "rows": rows}
    engine.dispose()
    return {"db_url": db_url, "tables": tables}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Полная выгрузка SQLite в JSON")
    ap.add_argument("profile", nargs="?", default=None,
                    help="Имя профиля (по умолчанию — активный)")
    ap.add_argument("--db", default=None, help="SQLAlchemy URL напрямую")
    ap.add_argument("--out", default=None, help="Путь к JSON-файлу")
    ap.add_argument("--table", action="append", default=[],
                    help="Только указанные таблицы (можно несколько раз)")
    args = ap.parse_args(argv)

    db_url = _resolve_db_url(args.profile, args.db)
    payload = dump_all(db_url)

    if args.table:
        payload["tables"] = {
            name: data for name, data in payload["tables"].items()
            if name in args.table
        }

    for name, data in payload["tables"].items():
        print(f"{name}: {len(data['rows'])} строк, столбцы: {', '.join(data['columns'])}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print(f"\nЗаписано: {out}")
    else:
        print("\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())