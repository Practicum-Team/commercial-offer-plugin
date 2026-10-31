"""Загрузка настроек, выбор профиля, путей к файлам и локальному хранилищу."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# Корень проекта: .../src/commercial_offer/config.py -> .../
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = PROJECT_ROOT / "profiles"
DEFAULT_PROFILE_NAME = "company_comp_service"
ACTIVE_PROFILE_FILE = PROFILES_DIR / ".active-profile.json"

# Белый список расширений для загружаемых документов профиля
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".json", ".jsonl", ".md", ".docx"}


@dataclass
class ProfileEntry:
    name: str
    path: Path


@dataclass
class Config:
    """Конфигурация плагина: реестр профилей и активный профиль."""
    profiles_dir: Path = PROFILES_DIR
    profiles: Dict[str, ProfileEntry] = field(default_factory=dict)
    active_profile: Optional[str] = None
    rules_path: Optional[Path] = None

    # --- инициализация ---
    @classmethod
    def load(cls, config_file: Optional[Path] = None) -> "Config":
        cfg = cls()
        cfg._scan_profiles()
        # По умолчанию — тестовый профиль
        if DEFAULT_PROFILE_NAME in cfg.profiles:
            cfg.set_active_profile(DEFAULT_PROFILE_NAME)
        elif cfg.profiles:
            cfg.set_active_profile(next(iter(cfg.profiles)))
        selected_file = config_file if config_file is not None else ACTIVE_PROFILE_FILE
        if selected_file.exists():
            cfg._load_overrides(selected_file)
        return cfg

    def save_active_profile(self) -> None:
        ACTIVE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_PROFILE_FILE.write_text(
            json.dumps({"active_profile": self.active_profile}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_overrides(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if active := data.get("active_profile"):
            if active in self.profiles:
                self.set_active_profile(active)

    def _scan_profiles(self) -> None:
        self.profiles.clear()
        if not self.profiles_dir.exists():
            return
        for child in sorted(self.profiles_dir.iterdir()):
            if child.is_dir():
                self.profiles[child.name] = ProfileEntry(child.name, child)

    # --- профили ---
    def register_profile(self, name: str, path: Path) -> ProfileEntry:
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"Недопустимое имя профиля: {name!r}")
        entry = ProfileEntry(name=name, path=Path(path).resolve())
        self.profiles[name] = entry
        return entry

    def set_active_profile(self, name: str) -> ProfileEntry:
        entry = self.profiles.get(name)
        if not entry:
            raise KeyError(f"Профиль не найден: {name}. Доступно: {sorted(self.profiles)}")
        self.active_profile = name
        return entry

    def list_profiles(self) -> list[dict]:
        return [
            {
                "name": e.name,
                "path": str(e.path),
                "active": e.name == self.active_profile,
                "exists": e.path.exists(),
            }
            for e in self.profiles.values()
        ]

    # --- пути активного профиля ---
    @property
    def active_entry(self) -> ProfileEntry:
        if not self.active_profile or self.active_profile not in self.profiles:
            raise RuntimeError("Активный профиль не выбран")
        return self.profiles[self.active_profile]

    def profile_path(self) -> Path:
        return self.active_entry.path

    def document_path(self, filename: str) -> Path:
        """Путь к документу профиля. Используется импортёром и storage."""
        return self.profile_path() / filename

    def profile_file(self, filename: str) -> Path:
        return self.document_path(filename)
