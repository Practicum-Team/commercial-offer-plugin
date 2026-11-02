"""Чтение каталога, прайса, правил и кейсов; проверка типов; копирование в профиль."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any
from xml.etree import ElementTree

from .config import ALLOWED_EXTENSIONS, Config
from .schemas import FileFingerprint

SAMPLE_ROWS = 3
SAMPLE_LINES = 5

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
    # Совпадение размера не доказывает совпадение содержимого — требуется хеш.
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

    def load(self, profile_name: str, sources: list[Path],
         explicit_dir: Optional[Path] = None,
         overwrite: Optional[bool] = None) -> dict:
        profile_dir = self._resolve_profile_dir(profile_name, explicit_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        plan, errors, conflicts = [], [], []
        for src in map(Path, sources):
            chk = check_file(src)
            if not chk.ok:
                errors.append({"file": src.name, "reason": chk.reason})
                continue
            dst = profile_dir / src.name
            if dst.exists() and dst.resolve() == src.resolve():
                plan.append({"src": src, "dst": dst, "action": "skip", "kind": chk.detected_kind})
                continue
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

            # персистентно регистрируем профиль и делаем активным
            self.config.register_profile(profile_name, profile_dir)
            self.config.set_active_profile(profile_name)

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


# ---------- чтение табличных файлов ----------

def _sniff_delimiter(sample: str) -> str:
    for d in (",", ";", "\t"):
        if d in sample:
            return d
    return ","


def read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    text = read_text(path)
    if not text.strip():
        return [], []
    delimiter = _sniff_delimiter(text.splitlines()[0])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = [dict(r) for r in reader]
    columns = list(reader.fieldnames or [])
    return columns, rows


def read_xlsx_rows(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    from openpyxl import load_workbook  # локальный импорт: openpyxl нужен только для xlsx

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    iterator = ws.iter_rows(values_only=True)
    try:
        header = next(iterator)
    except StopIteration:
        return [], []
    columns = [str(c).strip() if c is not None else "" for c in header]
    rows: list[dict[str, Any]] = []
    for raw in iterator:
        if raw is None or all(v is None for v in raw):
            continue
        rows.append({columns[i]: raw[i] for i in range(len(columns))})
    return columns, rows


def read_table(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    ext = path.suffix.lower()
    if ext == ".csv":
        return read_csv_rows(path)
    if ext == ".xlsx":
        return read_xlsx_rows(path)
    raise ValueError(f"Не табличный файл: {path.name}")


# ---------- отпечатки ----------

def fingerprint(path: Path) -> FileFingerprint:
    kind, err = _detect_kind(path)
    if err:
        return FileFingerprint(filename=path.name, ext=path.suffix.lower(), error=err)

    fp = FileFingerprint(filename=path.name, ext=path.suffix.lower())
    try:
        if kind in ("csv", "xlsx"):
            columns, rows = read_table(path)
            fp.columns = columns
            fp.sample_rows = [
                [row.get(c) for c in columns] for row in rows[:SAMPLE_ROWS]
            ]
        elif kind in ("md", "json", "jsonl"):
            fp.first_lines = read_text(path).splitlines()[:SAMPLE_LINES]
        elif kind == "docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = [
                "".join(node.text or "" for node in p.findall(".//w:t", ns))
                for p in root.findall(".//w:p", ns)
            ]
            fp.first_lines = [text for text in paragraphs if text.strip()][:SAMPLE_LINES]
    except Exception as exc:
        fp.error = f"ошибка чтения: {exc}"
    return fp


def fingerprint_all(paths: list[Path]) -> list[FileFingerprint]:
    return [fingerprint(p) for p in paths]


# ---------- разбор строк файла по field_map ----------

def _coerce_date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    from datetime import date, datetime
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s  # вернуть как есть — валидатор отклонит


def _coerce_decimal(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    from decimal import Decimal, InvalidOperation
    s = str(value).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return str(Decimal(s))
    except InvalidOperation:
        return None


def normalize_row(row: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for logical, column in field_map.items():
        value = row.get(column)
        if logical in ("valid_from", "valid_until"):
            out[logical] = _coerce_date(value)
        elif logical == "amount":
            out[logical] = _coerce_decimal(value)
        elif value is None:
            out[logical] = None
        else:
            out[logical] = str(value).strip()
    return out


def iter_rows(path: Path):
    """Строки файла с реальным номером (заголовок = строка 1)."""
    columns, rows = read_table(path)
    for idx, row in enumerate(rows, start=2):
        yield idx, row
