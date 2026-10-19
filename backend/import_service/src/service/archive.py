"""Распаковка zip — граница доверия: только *.pdf, лимит числа файлов и реально прочитанных байт, ссылки пропускаем."""

import re
import stat
import unicodedata
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import IO

MAX_FILES = 1000  # на всю загрузку, а не на один zip
MAX_TOTAL = 2 * 1024**3  # после распаковки всех zip загрузки; считаем прочитанное, а не file_size из заголовка
CHUNK = 1024 * 1024
UTF8_FLAG = 0x800
# пароль, Deflate64 от Проводника, битый CRC, обрезанный файл
UNREADABLE = (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError)


class ArchiveError(Exception):
    pass


def display_name(raw: str) -> str:
    """Имя для показа: только последняя часть пути, без управляющих символов, в NFC. В ключи MinIO не попадает.

    NFC: Mac пишет «й» двумя символами, и без нормализации имя из zip не совпало бы с именем в таблице сведений.
    """
    name = unicodedata.normalize("NFC", PurePosixPath(raw.replace("\\", "/")).name)
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:255] or "file.pdf"


def entry_name(info: zipfile.ZipInfo) -> str:
    """Без флага UTF-8 имя записано кодировкой архиватора: Mac пишет UTF-8, 7-Zip и Проводник русской Windows — cp866.
    zipfile в этом случае читает cp437, отсюда «êóá¡«ó» вместо «Иванов»."""
    if info.flag_bits & UTF8_FLAG:
        return info.filename
    raw = info.filename.encode("cp437")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp866")


def extract_pdfs(
    archive: Path | IO[bytes], target: Path, files: int = 0, used: int = 0
) -> tuple[list[tuple[str, Path]], int]:
    """PDF из архива во временную папку → ([(имя для показа, путь)], байт распаковано всего).

    files и used — сколько файлов и байт уже дали прошлые zip этой загрузки: лимиты общие. Синхронно — через to_thread.
    """
    out: list[tuple[str, Path]] = []
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise ArchiveError("Архив повреждён или это не zip") from e
    with zf:
        for info in zf.infolist():
            raw = entry_name(info)
            name = display_name(raw)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or not name.lower().endswith(".pdf"):
                continue
            if "__MACOSX" in raw or name.startswith("."):
                continue
            if files + len(out) >= MAX_FILES:
                raise ArchiveError(f"За один раз — не больше {MAX_FILES} работ")
            path = target / f"{len(out) + 1}.pdf"
            try:
                with zf.open(info) as src, path.open("wb") as dst:
                    while chunk := src.read(CHUNK):
                        used += len(chunk)
                        if used > MAX_TOTAL:
                            raise ArchiveError("Архивы после распаковки больше 2 ГБ")
                        dst.write(chunk)
            except UNREADABLE as e:
                raise ArchiveError(
                    f"{name} не читается: архив с паролем, повреждён или сжат неизвестным методом"
                ) from e
            out.append((name, path))
    if not out:
        raise ArchiveError("В архиве нет PDF")
    return out, used
