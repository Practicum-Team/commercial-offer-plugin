"""Работа с SQLite: схема, загрузка профиля, чтение для инструментов."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .schemas import (
    Base, Case, Price, Product, Profile, Rule, to_payload,
)


# ---------- движок ----------

def default_db_url(profile_dir: Path) -> str:
    return f"sqlite:///{Path(profile_dir) / 'profile.sqlite3'}"


def make_session_factory(db_url: str) -> sessionmaker:
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, future=True)


# ---------- загрузка ----------

@dataclass
class CommitResult:
    ok: bool
    counts: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def commit_profile(
    profile_name: str,
    db_url: str,
    *,
    products: list[dict],
    prices: list[dict],
    rules: list[dict],
    cases: list[dict],
    profile_payload: Optional[dict] = None,
) -> CommitResult:
    """Идемпотентная запись данных профиля в SQLite (merge по ключам)."""
    try:
        SessionLocal = make_session_factory(db_url)
    except Exception as exc:
        return CommitResult(ok=False, errors=[{"reason": f"БД недоступна: {exc}"}])

    try:
        with SessionLocal.begin() as session:  
            if session.get(Profile, profile_name) is None:
                session.add(Profile(
                    profile_id=profile_name,
                    name=profile_name,
                    payload=json.dumps(profile_payload or {}, ensure_ascii=False),
                ))
            elif profile_payload is not None:
                obj = session.get(Profile, profile_name)
                obj.payload = json.dumps(profile_payload, ensure_ascii=False)

            for p in products:
                session.merge(Product(
                    profile_id=profile_name,
                    product_id=p["product_id"],
                    name=p["name"],
                    category=p.get("category"),
                    description=p.get("description"),
                    payload=to_payload(p, p.get("_payload_columns", [])),
                    source_file=p.get("_source_file"),
                    source_row=p.get("_source_row"),
                ))

            for p in prices:
                session.merge(Price(
                    profile_id=profile_name,
                    price_id=p.get("price_id") or f"{p['product_id']}_{p['_source_row']}",
                    product_id=p["product_id"],
                    amount=p["amount"],
                    currency=p["currency"],
                    valid_from=_as_date(p.get("valid_from")),
                    valid_until=_as_date(p.get("valid_until")),
                    price_version=p.get("price_version"),
                    payload=to_payload(p, p.get("_payload_columns", [])),
                    source_file=p.get("_source_file"),
                    source_row=p.get("_source_row"),
                ))

            for r in rules:
                session.merge(Rule(
                    profile_id=profile_name,
                    rule_id=r["rule_id"],
                    applies_to=r["applies_to"],
                    rule_type=r["rule_type"],
                    conditions_json=r.get("conditions_json", "{}"),
                    description=r["description"],
                    payload=json.dumps(r, ensure_ascii=False, default=str),
                    source_file=r.get("_source_file"),
                ))

            for c in cases:
                session.merge(Case(
                    profile_id=profile_name,
                    case_id=c["case_id"],
                    text=c["text"],
                    is_fictional=int(c.get("is_fictional", 0)),
                    source_file=c.get("_source_file"),
                ))
    except Exception as exc:
        return CommitResult(ok=False, errors=[{"reason": str(exc)}])

    return CommitResult(ok=True, counts={
        "products": len(products),
        "prices": len(prices),
        "rules": len(rules),
        "cases": len(cases),
    })


import uuid
from datetime import datetime, timezone

from .schemas import Run, RunEvent, ProfileLoad, ProfileLoadFile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- runs ----------

def start_run(db_url: str, profile_id: str, input_text: str,
              input_json: dict | None = None, run_id: str | None = None) -> dict:
    SessionLocal = make_session_factory(db_url)
    run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
    ts = _now()
    with SessionLocal.begin() as session:
        session.add(Run(
            run_id=run_id, profile_id=profile_id,
            input_text=input_text,
            input_json=json.dumps(input_json or {}, ensure_ascii=False),
            stage="received", created_at=ts, updated_at=ts,
        ))
    return {"ok": True, "run_id": run_id, "profile_id": profile_id}


def update_run_stage(db_url: str, run_id: str, stage: str) -> dict:
    SessionLocal = make_session_factory(db_url)
    with SessionLocal.begin() as session:
        obj = session.get(Run, run_id)
        if not obj:
            return {"ok": False, "error": f"run_id не найден: {run_id}"}
        obj.stage = stage
        obj.updated_at = _now()
    return {"ok": True, "run_id": run_id, "stage": stage}


def finish_run(db_url: str, run_id: str, *,
               result_kind: str,
               result_json: dict | None = None,
               document_path: str | None = None,
               stage: str | None = None) -> dict:
    """result_kind: success | needs_clarification | refused."""
    if result_kind not in ("success", "needs_clarification", "refused"):
        return {"ok": False, "error": f"неизвестный result_kind: {result_kind!r}"}
    SessionLocal = make_session_factory(db_url)
    with SessionLocal.begin() as session:
        obj = session.get(Run, run_id)
        if not obj:
            return {"ok": False, "error": f"run_id не найден: {run_id}"}
        obj.result_kind = result_kind
        obj.result_json = json.dumps(result_json or {}, ensure_ascii=False, default=str)
        obj.document_path = document_path
        obj.stage = stage or {"success": "docx_created",
                              "needs_clarification": "clarification",
                              "refused": "refused"}[result_kind]
        obj.updated_at = _now()
    return {"ok": True, "run_id": run_id, "result_kind": result_kind}


def get_run(db_url: str, run_id: str) -> dict | None:
    SessionLocal = make_session_factory(db_url)
    with SessionLocal() as session:
        obj = session.get(Run, run_id)
        if not obj:
            return None
        events = session.query(RunEvent).filter(RunEvent.run_id == run_id).order_by(RunEvent.id).all()
        return {
            "run_id": obj.run_id,
            "profile_id": obj.profile_id,
            "input_text": obj.input_text,
            "input_json": json.loads(obj.input_json or "{}"),
            "stage": obj.stage,
            "result_kind": obj.result_kind,
            "result_json": json.loads(obj.result_json or "{}"),
            "document_path": obj.document_path,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "events": [
                {"id": e.id, "command": e.command, "ok": bool(e.ok),
                 "exit_code": e.exit_code, "duration_ms": e.duration_ms,
                 "created_at": e.created_at, "error": e.error}
                for e in events
            ],
        }


# ---------- run_events ----------

def log_event(db_url: str, *, run_id: str | None, profile_id: str | None,
              command: str, args: dict | None, ok: bool,
              exit_code: int, output: dict | None, error: str | None,
              duration_ms: int) -> None:
    """Не падаем, если логирование сломалось — это диагностика."""
    try:
        SessionLocal = make_session_factory(db_url)
        with SessionLocal.begin() as session:
            session.add(RunEvent(
                run_id=run_id or "", profile_id=profile_id or "",
                command=command,
                args_json=json.dumps(args or {}, ensure_ascii=False, default=str),
                ok=1 if ok else 0, exit_code=exit_code,
                output_json=json.dumps(output or {}, ensure_ascii=False, default=str),
                error=error, duration_ms=duration_ms,
                created_at=_now(),
            ))
    except Exception:
        pass


# ---------- profile_loads ----------

def record_profile_load(db_url: str, *, profile_id: str,
                        schema_hash: str | None,
                        files: list[dict], ok: bool = True,
                        note: str | None = None) -> dict:
    """files: [{filename, kind, action, size_bytes, sha256}]."""
    load_id = f"load_{uuid.uuid4().hex[:12]}"
    ts = _now()
    SessionLocal = make_session_factory(db_url)
    with SessionLocal.begin() as session:
        session.add(ProfileLoad(
            load_id=load_id, profile_id=profile_id,
            loaded_at=ts, schema_hash=schema_hash,
            ok=1 if ok else 0, note=note,
        ))
        for f in files:
            session.add(ProfileLoadFile(
                load_id=load_id,
                filename=f["filename"], kind=f.get("kind"),
                action=f.get("action"), size_bytes=f.get("size_bytes"),
                sha256=f.get("sha256"),
            ))
    return {"ok": True, "load_id": load_id, "profile_id": profile_id, "loaded_at": ts}


def list_profile_loads(db_url: str, profile_id: str) -> list[dict]:
    SessionLocal = make_session_factory(db_url)
    with SessionLocal() as session:
        loads = (session.query(ProfileLoad)
                 .filter(ProfileLoad.profile_id == profile_id)
                 .order_by(ProfileLoad.loaded_at.desc()).all())
        out = []
        for ld in loads:
            files = session.query(ProfileLoadFile).filter(ProfileLoadFile.load_id == ld.load_id).all()
            out.append({
                "load_id": ld.load_id, "loaded_at": ld.loaded_at,
                "schema_hash": ld.schema_hash, "ok": bool(ld.ok), "note": ld.note,
                "files": [{"filename": f.filename, "kind": f.kind,
                           "action": f.action, "size_bytes": f.size_bytes,
                           "sha256": f.sha256} for f in files],
            })
        return out


def latest_open_run_id(db_url: str, profile_id: str) -> str | None:
    """Последний незавершённый run профиля (result_kind IS NULL)."""
    from sqlalchemy import select
    SessionLocal = make_session_factory(db_url)
    with SessionLocal() as session:
        stmt = (
            select(Run.run_id)
            .where(Run.profile_id == profile_id)
            .where(Run.result_kind.is_(None))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        row = session.execute(stmt).first()
        return row[0] if row else None