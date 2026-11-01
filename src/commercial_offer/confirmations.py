"""Артефакты подтверждения пользователя. CLI не пропускает шаги без них."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CONFIRM_FILE = "confirmations.json"
AUDIT_FILE = "audit.jsonl"

# Типы подтверждений
CONFIRM_SCHEMA = "schema"          # назначения файлов подтверждены
CONFIRM_FIELDS = "fields"          # field_map подтверждён
CONFIRM_COMMIT = "commit"          # пользователь явно разрешил запись


@dataclass
class Confirmation:
    kind: str
    schema_hash: str
    confirmed: bool
    at: str            # ISO UTC
    note: Optional[str] = None


def schema_hash(schema_dict: dict) -> str:
    """Канонический хэш схемы: любое изменение назначений/полей меняет хэш."""
    canonical = json.dumps(schema_dict, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_all(profile_dir: Path) -> dict:
    p = profile_dir / CONFIRM_FILE
    if not p.exists():
        return {"items": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}


def _write_all(profile_dir: Path, data: dict) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    p = profile_dir / CONFIRM_FILE
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(profile_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(profile_dir: Path, kind: str, schema_hash_hex: str,
           confirmed: bool, note: Optional[str] = None) -> Confirmation:
    data = _read_all(profile_dir)
    item = Confirmation(
        kind=kind, schema_hash=schema_hash_hex,
        confirmed=confirmed, at=_now(), note=note,
    )
    data["items"].append(item.__dict__)
    _write_all(profile_dir, data)
    return item


def check(profile_dir: Path, kind: str, schema_hash_hex: str) -> tuple[bool, str]:
    """True, если есть подтверждение для этого kind и текущего хэша схемы."""
    data = _read_all(profile_dir)
    for item in reversed(data.get("items", [])):
        if item.get("kind") != kind:
            continue
        if item.get("schema_hash") != schema_hash_hex:
            return False, (
                f"подтверждение {kind!r} относится к другой версии схемы "
                f"(схема изменилась). Требуется повторное подтверждение."
            )
        return bool(item.get("confirmed")), item.get("note") or ""
    return False, f"нет подтверждения {kind!r} для текущей схемы"


def audit(profile_dir: Path, event: dict) -> None:
    """Не падаем, если аудит не пишется — это диагностика, не логика."""
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        event = {"at": _now(), **event}
        with (profile_dir / AUDIT_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass