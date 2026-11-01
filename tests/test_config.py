from pathlib import Path

import pytest

from commercial_offer.config import Config


def test_load_creates_config_file(tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    assert not cfg_path.exists()
    Config.load(config_path=cfg_path)
    assert cfg_path.exists()


def test_set_active_profile_persists(tmp_path: Path, tmp_profiles_dir: Path):
    cfg_path = tmp_path / "config.json"
    cfg = Config.load(config_path=cfg_path)
    cfg.profiles_dir = tmp_profiles_dir
    cfg.register_profile("company_a", tmp_profiles_dir / "company_a")
    cfg.set_active_profile("company_a")

    cfg2 = Config.load(config_path=cfg_path)
    assert cfg2.active_profile == "company_a"
    assert "company_a" in cfg2.profiles


def test_register_profile_rejects_bad_name(tmp_path: Path, tmp_profiles_dir: Path):
    cfg = Config.load(config_path=tmp_path / "c.json")
    cfg.profiles_dir = tmp_profiles_dir
    with pytest.raises(ValueError):
        cfg.register_profile("../escape", tmp_profiles_dir / "x")


def test_active_profile_missing_raises(tmp_path: Path, tmp_profiles_dir: Path):
    cfg = Config.load(config_path=tmp_path / "c.json")
    cfg.profiles_dir = tmp_profiles_dir
    cfg.active_profile = None
    with pytest.raises(RuntimeError):
        _ = cfg.profile_path()