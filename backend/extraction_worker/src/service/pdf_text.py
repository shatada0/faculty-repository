"""Подпроцесс: PDF из stdin → JSON {pages: [...]} в stdout.

pypdf синхронный, и asyncio-таймаут его не прервёт: зависший разбор чужого PDF убивает родитель вместе с процессом.
"""

import io
import json
import sys

from pypdf import PasswordType, PdfReader

MAX_PAGES = 15  # титульник, реферат и запас: всю ВКР читать незачем
MEMORY_LIMIT = 1024**3  # PDF-бомба упрётся сама и не утянет за собой воркер по OOM контейнера
PASSWORD_EXIT = 3


def main() -> None:
    if sys.platform != "win32":  # resource есть только в Unix; на Windows — только локальные тесты
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        sys.exit(PASSWORD_EXIT)
    pages = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
    sys.stdout.buffer.write(json.dumps({"pages": pages}, ensure_ascii=False).encode())  # UTF-8 и на Windows


if __name__ == "__main__":
    main()
