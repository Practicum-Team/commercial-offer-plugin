from pathlib import Path

from commercial_offer import confirmations


def test_hash_changes_with_schema():
    a = {"profile_name": "p", "files": [{"filename": "x", "kind": "catalog"}]}
    b = {"profile_name": "p", "files": [{"filename": "x", "kind": "prices"}]}
    assert confirmations.schema_hash(a) != confirmations.schema_hash(b)
    assert confirmations.schema_hash(a) == confirmations.schema_hash(dict(a))


def test_record_and_check(tmp_path: Path):
    h = confirmations.schema_hash({"x": 1})
    confirmations.record(tmp_path, confirmations.CONFIRM_SCHEMA, h, True)
    ok, why = confirmations.check(tmp_path, confirmations.CONFIRM_SCHEMA, h)
    assert ok and why == ""


def test_check_invalidated_by_hash_change(tmp_path: Path):
    h1 = confirmations.schema_hash({"x": 1})
    h2 = confirmations.schema_hash({"x": 2})
    confirmations.record(tmp_path, confirmations.CONFIRM_SCHEMA, h1, True)
    ok, why = confirmations.check(tmp_path, confirmations.CONFIRM_SCHEMA, h2)
    assert not ok
    assert "другой версии схемы" in why


def test_negative_confirmation(tmp_path: Path):
    h = confirmations.schema_hash({"x": 1})
    confirmations.record(tmp_path, confirmations.CONFIRM_FIELDS, h, False, note="нет")
    ok, _ = confirmations.check(tmp_path, confirmations.CONFIRM_FIELDS, h)
    assert not ok


def test_audit_appends(tmp_path: Path):
    confirmations.audit(tmp_path, {"event": "confirm", "kind": "schema"})
    confirmations.audit(tmp_path, {"event": "commit"})
    lines = (tmp_path / confirmations.AUDIT_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2