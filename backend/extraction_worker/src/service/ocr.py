"""Подпроцесс: скан PDF из stdin → JSON {pages: [...]} в stdout, текст первых страниц через Tesseract.

pypdfium2 рисует страницу в оттенках серого, Tesseract читает её как PGM из stdin: без Pillow и временных файлов.
Как и pdf_text — отдельным процессом: зависший рендер чужого PDF убивает родитель.
"""

import json
import os
import shutil
import subprocess
import sys

import pypdfium2

OCR_PAGES = 3  # титульник и две следующие: их и читает select_text, реферат на скане ищем не дальше
DPI = 300  # меньше — Tesseract путает буквы на титульнике с мелким шрифтом
PAGE_TIMEOUT = 40
MEMORY_LIMIT = 2 * 1024**3  # страница A4 в 300 dpi — ~9 МБ, остальное — PDFium и запас на PDF-бомбу


def pgm(bitmap: pypdfium2.PdfBitmap) -> bytes:
    """Серый битмап → PGM (P5). Строки в буфере выровнены до stride — лишнее в конце строки отрезаем."""
    w, h, stride = bitmap.width, bitmap.height, bitmap.stride
    raw = bytes(bitmap.buffer)
    rows = raw if stride == w else b"".join(raw[y * stride : y * stride + w] for y in range(h))
    return b"P5\n%d %d\n255\n" % (w, h) + rows


def page_text(image: bytes) -> str:
    out = subprocess.run(  # noqa: S603 — аргументы постоянные, чужой PDF идёт только в stdin
        [shutil.which("tesseract") or "tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", "1"],
        input=image,
        capture_output=True,
        timeout=PAGE_TIMEOUT,
        check=True,
        env=os.environ | {"OMP_THREAD_LIMIT": "1"},  # воркер и так разбирает файлы параллельно
    )
    return out.stdout.decode("utf-8", errors="replace")


def main() -> None:
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    doc = pypdfium2.PdfDocument(sys.stdin.buffer.read())
    pages = []
    for i in range(min(len(doc), OCR_PAGES)):
        bitmap = doc[i].render(scale=DPI / 72, grayscale=True)
        pages.append(page_text(pgm(bitmap)))
    sys.stdout.buffer.write(json.dumps({"pages": pages}, ensure_ascii=False).encode())  # UTF-8 и на Windows


if __name__ == "__main__":
    main()
