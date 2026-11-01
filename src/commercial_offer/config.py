"""Загрузка/сохранение настроек, выбор профиля, путей к файлам и локальному хранилищу."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = PROJECT_ROOT / "profiles"
DEFAULT_PROFILE_NAME = "company_comp_service"

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".json", ".jsonl", ".md", ".docx"}

# Путь к персистентному конфигу. Можно переопределить переменной окружения.
CONFIG_PATH = Path(
    os.environ.get("COMMERCIAL_OFFER_CONFIG", str(PROJECT_ROOT / "profiles" / "config.json"))
)


@dataclass
class ProfileEntry:
    name: str
    path: Path


@dataclass
class Config:
    profiles_dir: Path = PROFILES_DIR
    profiles: Dict[str, ProfileEntry] = field(default_factory=dict)
    active_profile: Optional[str] = None
    config_path: Path = CONFIG_PATH

    # ---------- персистентность ----------

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        path = Path(config_path) if config_path else CONFIG_PATH
        cfg = cls(config_path=path)

        # 1) восстанавливаем зарегистрированные профили из файла
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"config.json повреждён: {exc}") from exc
            for name, p in data.get("profiles", {}).items():
                cfg.profiles[name] = ProfileEntry(name, Path(p))
            active = data.get("active_profile")
            if active and active in cfg.profiles:
                cfg.active_profile = active
            cfg.profiles_dir = Path(data.get("profiles_dir", cfg.profiles_dir))

        # 2) добираем новые каталоги из profiles/, которых нет в файле
        cfg._scan_profiles(merge=True)

        # 3) активный профиль: файл -> default -> первый доступный
        if cfg.active_profile not in cfg.profiles:
            if DEFAULT_PROFILE_NAME in cfg.profiles:
                cfg.active_profile = DEFAULT_PROFILE_NAME
            elif cfg.profiles:
                cfg.active_profile = next(iter(cfg.profiles))
            else:
                cfg.active_profile = None

        # 4) фиксируем состояние (создаём config.json при первом запуске)
        cfg.save()
        return cfg

    def save(self) -> None:
        """Атомарная запись config.json с fsync."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_profile": self.active_profile,
            "profiles_dir": str(self.profiles_dir),
            "profiles": {name: str(e.path) for name, e in self.profiles.items()},
        }
        with _file_lock(self.config_path):
            fd, tmp = tempfile.mkstemp(
                prefix=self.config_path.name + ".", suffix=".tmp",
                dir=str(self.config_path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.config_path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # ---------- индексация ----------

    def _scan_profiles(self, merge: bool = False) -> None:
        if not self.profiles_dir.exists():
            return
        if not merge:
            self.profiles.clear()
        for child in sorted(self.profiles_dir.iterdir()):
            if child.is_dir() and child.name not in self.profiles:
                self.profiles[child.name] = ProfileEntry(child.name, child.resolve())

    # ---------- профили ----------

    def register_profile(self, name: str, path: Path) -> ProfileEntry:
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"Недопустимое имя профиля: {name!r}")
        entry = ProfileEntry(name=name, path=Path(path).resolve())
        self.profiles[name] = entry
        self.save()
        return entry

    def set_active_profile(self, name: str) -> ProfileEntry:
        entry = self.profiles.get(name)
        if not entry:
            raise KeyError(f"Профиль не найден: {name}. Доступно: {sorted(self.profiles)}")
        self.active_profile = name
        self.save()
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

    # ---------- пути активного профиля ----------

    @property
    def active_entry(self) -> ProfileEntry:
        if not self.active_profile or self.active_profile not in self.profiles:
            raise RuntimeError("Активный профиль не выбран")
        return self.profiles[self.active_profile]

    def profile_path(self) -> Path:
        return self.active_entry.path

    def document_path(self, filename: str) -> Path:
        return self.profile_path() / filename

    def profile_file(self, filename: str) -> Path:
        return self.document_path(filename)


# ---------- блокировка файла конфига (без внешних зависимостей) ----------

@contextmanager
def _file_lock(target: Path):
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        try:
            import fcntl  # POSIX
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except ImportError:
            try:
                import msvcrt  # Windows
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            except ImportError:
                pass  # нет блокировки — атомарный os.replace всё равно защищает от порчи
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                pass
        fh.close()