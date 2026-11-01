from pathlib import Path

from commercial_offer import storage


def test_commit_profile_idempotent(committed_profile: str):
    from sqlalchemy import select, func
    from commercial_offer.schemas import Product

    SessionLocal = storage.make_session_factory(committed_profile)
    with SessionLocal() as s:
        n1 = s.execute(select(func.count()).select_from(Product)).scalar()
    # повторный коммит того же
    res = storage.commit_profile(
        profile_name="company_a", db_url=committed_profile,
        products=[], prices=[], rules=[], cases=[],
    )
    assert res.ok
    with SessionLocal() as s:
        n2 = s.execute(select(func.count()).select_from(Product)).scalar()
    assert n1 == n2 == 2


def test_commit_profile_creates_db(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'x.sqlite3'}"
    res = storage.commit_profile(
        profile_name="p", db_url=db_url,
        products=[{"product_id": "1", "name": "A"}],
        prices=[], rules=[], cases=[],
        profile_payload={"currency": "RUB"},
    )
    assert res.ok and res.counts["products"] == 1
    assert (tmp_path / "x.sqlite3").exists()