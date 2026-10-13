"""Распаковка zip — граница доверия: только *.pdf, лимит числа файлов и реально прочитанных байт, ссылки пропускаем."""

import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILES = 1000
MAX_TOTAL = 2 * 1024**3  # после распаковки; считаем прочитанное, а не file_size из заголовка
CHUNK = 1024 * 1024


class ArchiveError(Exception):
    pass


def display_name(raw: str) -> str:
    """Имя для показа: только последняя часть пути, без управляющих символов. В ключи MinIO не попадает."""
    name = PurePosixPath(raw.replace("\\", "/")).name
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:255] or "file.pdf"


def extract_pdfs(archive: Path, target: Path) -> list[tuple[str, Path]]:
    """PDF из архива во временную папку: [(имя для показа, путь)]. Синхронно — вызывать через to_thread."""
    out: list[tuple[str, Path]] = []
    total = 0
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise ArchiveError("Архив повреждён или это не zip") from e
    with zf:
        for info in zf.infolist():
            name = display_name(info.filename)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or not name.lower().endswith(".pdf"):
                continue
            if "__MACOSX" in info.filename or name.startswith("."):
                continue
            if len(out) >= MAX_FILES:
                raise ArchiveError(f"В архиве больше {MAX_FILES} файлов")
            path = target / f"{len(out) + 1}.pdf"
            with zf.open(info) as src, path.open("wb") as dst:
                while chunk := src.read(CHUNK):
                    total += len(chunk)
                    if total > MAX_TOTAL:
                        raise ArchiveError("Архив после распаковки больше 2 ГБ")
                    dst.write(chunk)
            out.append((name, path))
    if not out:
        raise ArchiveError("В архиве нет PDF")
    return out
