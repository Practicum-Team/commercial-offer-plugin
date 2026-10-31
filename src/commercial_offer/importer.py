"""Чтение каталога, прайса, правил и кейсов; проверка типов; копирование в профиль."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ALLOWED_EXTENSIONS, Config

# --- проверка «магических байтов» и первой строки ---

_MAGIC = {
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
}

_CSV_SNIFF = (b",", b";", b"\t")


@dataclass
class FileCheck:
    path: Path
    ok: bool
    detected_kind: Optional[str] = None  # csv|xlsx|json|jsonl|md|docx
    reason: str = ""


def _read_head(path: Path, n: int = 4096) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _detect_kind(path: Path) -> tuple[Optional[str], str]:
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, f"расширение {ext!r} не в белом списке"
    head = _read_head(path)
    if ext in _MAGIC:
        magic = _MAGIC[ext]
        if not any(head.startswith(m) for m in magic):
            return None, f"магические байты не соответствуют {ext}"
        return ext.lstrip("."), ""
    if ext == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return "json", ""
        except Exception as exc:
            return None, f"json не парсится: {exc}"
    if ext == ".jsonl":
        try:
            with path.open(encoding="utf-8") as fh:
                first = fh.readline()
            json.loads(first)
            return "jsonl", ""
        except Exception as exc:
            return None, f"jsonl: первая строка не JSON: {exc}"
    if ext == ".md":
        if head.strip():
            return "md", ""
        return None, "md пустой"
    if ext == ".csv":
        try:
            text = head.decode("utf-8", errors="replace")
        except Exception:
            return None, "csv: ошибка декодирования"
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return None, "csv пустой"
        delimiter = next((d for d in _CSV_SNIFF if d.decode() in lines[0]), None)
        if delimiter is None:
            return None, "csv: не найден разделитель"
        try:
            list(csv.reader(io.StringIO(lines[0]), delimiter=delimiter.decode()))
        except Exception as exc:
            return None, f"csv: заголовок не парсится: {exc}"
        return "csv", ""
    return None, "неизвестный тип"


def check_file(path: Path) -> FileCheck:
    if not path.is_file():
        return FileCheck(path, False, reason="файл не существует")
    kind, reason = _detect_kind(path)
    return FileCheck(path, kind is not None, kind, reason)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file_fast(a: Path, b: Path) -> bool | None:
    """Быстрая проверка: True — одинаковы (size+mtime), False — точно разные,
    None — нужно сравнивать содержимое (хеш/побайтово)."""
    sa, sb = a.stat(), b.stat()
    if sa.st_size != sb.st_size:
        return False
    # mtime с точностью до наносекунд; допускаем небольшой разброс ФС (например, FAT — 2 с)
    if abs(sa.st_mtime_ns - sb.st_mtime_ns) <= 2_000_000_000:
        return True
    return None


# --- загрузка профиля ---

class ProfileLoader:
    """Копирует документы в каталог профиля с проверкой типов и коллизий имён."""

    def __init__(self, config: Config):
        self.config = config

    def _resolve_profile_dir(self, profile_name: str, explicit_dir: Optional[Path]) -> Path:
        if explicit_dir is not None:
            return Path(explicit_dir).resolve()
        return (self.config.profiles_dir / profile_name).resolve()

    def load(
        self,
        profile_name: str,
        sources: list[Path],
        explicit_dir: Optional[Path] = None,
        overwrite: Optional[bool] = None,
    ) -> dict:
        """
        sources — файлы, переданные пользователем (загруженные в чат или указанные путём).
        overwrite=None -> при коллизии вернуть статус needs_confirmation (агент спрашивает пользователя).
        overwrite=True/False -> перезаписать/оставить соответственно.
        """
        profile_dir = self._resolve_profile_dir(profile_name, explicit_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        plan, errors, conflicts = [], [], []
        for src in map(Path, sources):
            chk = check_file(src)
            if not chk.ok:
                errors.append({"file": src.name, "reason": chk.reason})
                continue
            dst = profile_dir / src.name
            if dst.exists() and dst.resolve() != src.resolve():
                fast = _same_file_fast(dst, src)
                if fast is True:
                    same = True
                elif fast is False:
                    same = False
                else:
                    same = _sha256(dst) == _sha256(src)
                if same:
                    plan.append({"src": src, "dst": dst, "action": "skip", "kind": chk.detected_kind})
                    continue
                if overwrite is None:
                    conflicts.append({"file": src.name, "dst": str(dst), "kind": chk.detected_kind})
                    continue
                if not overwrite:
                    plan.append({"src": src, "dst": dst, "action": "keep", "kind": chk.detected_kind})
                    continue
            plan.append({"src": src, "dst": dst, "action": "copy", "kind": chk.detected_kind})

        if conflicts:
            return {
                "status": "needs_confirmation",
                "profile": profile_name,
                "profile_dir": str(profile_dir),
                "conflicts": conflicts,
                "errors": errors,
            }

        for item in plan:
            if item["action"] == "copy":
                shutil.copy2(item["src"], item["dst"])

        self.config.register_profile(profile_name, profile_dir)
        return {
            "status": "ok",
            "profile": profile_name,
            "profile_dir": str(profile_dir),
            "applied": [
                {"file": it["dst"].name, "action": it["action"], "kind": it["kind"]}
                for it in plan
            ],
            "errors": errors,
        }

    def switch_profile(self, profile_name: str) -> dict:
        entry = self.config.set_active_profile(profile_name)
        return {
            "status": "ok",
            "active_profile": profile_name,
            "path": str(entry.path),
            "exists": entry.path.exists(),
        }