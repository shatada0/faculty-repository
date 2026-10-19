"""Подпроцесс: PDF из stdin → JSON {pages: [...]} в stdout.

pypdf синхронный, и asyncio-таймаут его не прервёт: зависший разбор чужого PDF убивает родитель вместе с процессом.
"""

import io
import json
import sys

from pypdf import PdfReader

MAX_PAGES = 15  # титульник, реферат и запас: всю ВКР читать незачем


def main() -> None:
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if reader.is_encrypted:
        reader.decrypt("")
    pages = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
    sys.stdout.write(json.dumps({"pages": pages}, ensure_ascii=False))


if __name__ == "__main__":
    main()
