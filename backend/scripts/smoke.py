"""Смоук живого стека через nginx: python scripts/smoke.py [--archive] [--grafana].

Нужен поднятый compose с демо-данными (seed demo) и dev-учётками из HANDOFF.md; другие — через SMOKE_MODERATOR
и SMOKE_AUTHOR в виде login:password. --archive гоняет ВКР через extraction_worker (нужна LLM из LLM_URL),
--grafana — запрос каждой панели (Grafana пускает хост через STATS_ALLOW). Первый упавший assert останавливает скрипт.
Скрипт пишет в базу: черновик из архива, отклонённый материал автора.
"""

import argparse
import io
import json
import os
import ssl
import sys
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://localhost"
MODERATOR = os.environ.get("SMOKE_MODERATOR", "moderator:moderator123").split(":", 1)
AUTHOR = os.environ.get("SMOKE_AUTHOR", "orlov:orlov12345").split(":", 1)
CA = ROOT / "certs" / "fullchain.pem"  # самоподписанный сертификат стенда из dev-secrets.sh — он же и корень
TLS = ssl.create_default_context(cafile=CA)


def client(login: str = "", password: str = "") -> httpx.Client:
    c = httpx.Client(base_url=BASE, verify=TLS, headers={"Origin": BASE}, timeout=120)
    if login:
        r = c.post("/api/auth/login", json={"login": login, "password": password})
        assert r.status_code == 200, r.text
    return c


def loopback(url: httpx.URL) -> httpx.Response:
    """GET на files.localhost: его резолвят браузеры, а не ОС. Идём на 127.0.0.1, имя — в SNI и Host."""
    with httpx.Client(verify=TLS, timeout=60) as c:
        return c.get(url.copy_with(host="127.0.0.1"), headers={"Host": url.host}, extensions={"sni_hostname": url.host})


def ok(r: httpx.Response, status: int = 200) -> httpx.Response:
    assert r.status_code == status, f"{r.request.method} {r.request.url}: {r.status_code} {r.text[:300]}"
    return r


def rows(xlsx: bytes) -> list[tuple]:
    return list(load_workbook(io.BytesIO(xlsx), read_only=True).worksheets[0].iter_rows(values_only=True))


def public() -> None:
    anon = client()
    for path in ("/api/stats", "/api/departments", "/api/works/latest", "/api/people", "/api/upload-limits"):
        ok(anon.get(path))
    found = ok(anon.get("/api/works", params={"q": "нейронные сети"})).json()
    assert found["total"] > 0
    ok(anon.get(f"/api/works/{found['items'][0]['id']}"))
    ok(anon.get("/api/admin/works"), 401)
    ok(anon.post("/api/auth/logout", headers={"Origin": "https://evil.example"}), 403)


def exports(m: httpx.Client) -> None:
    for query in ({}, {"type": "vkr"}, {"type": "publication", "status": "published"}, {"type": "talk"}):
        table = rows(ok(m.get("/api/admin/exports/works", params=query)).content)
        assert len(table) > 1, query
    csv = ok(m.get("/api/admin/exports/works", params={"type": "publication", "format": "csv"})).content
    assert csv.startswith(b"\xef\xbb\xbf") and b";" in csv.splitlines()[0]
    z = zipfile.ZipFile(io.BytesIO(ok(m.get("/api/admin/exports/works.zip", params={"type": "vkr"})).content))
    assert z.testzip() is None and len(z.namelist()) > 1
    assert len(rows(z.read("works.xlsx"))) > 1
    assert len(rows(ok(m.get("/api/admin/exports/people")).content)) > 1


def author_cabinet(m: httpx.Client) -> None:
    a = client(*AUTHOR)
    pdf = (ROOT / "fixtures" / "sample.pdf").read_bytes()
    work = next(i for i in ok(a.get("/api/my/works")).json()["items"] if i["canUpload"])
    url = f"/api/my/works/{work['id']}/materials"
    first = ok(a.post(url, data={"kind": "slides"}, files={"file": ("слайды.pdf", pdf, "application/pdf")}), 201).json()
    second = ok(a.post(url, files={"file": ("ещё.pdf", pdf, "application/pdf")}), 201).json()
    ok(a.post(url, files={"file": ("x.pdf", b"MZ\x90\x00", "application/pdf")}), 400)
    ok(a.post(f"/api/my/works/{uuid.uuid4()}/materials", files={"file": ("a.pdf", pdf, "application/pdf")}), 404)
    ok(a.delete(f"{url}/{second['id']}"), 204)
    link = httpx.URL(ok(m.get(f"/api/admin/works/{work['id']}/materials/{first['id']}")).json()["url"])
    assert ok(loopback(link)).content == pdf
    ok(m.post(f"/api/admin/works/{work['id']}/materials/{first['id']}", json={"status": "rejected"}))
    ok(a.delete(f"{url}/{first['id']}"), 409)
    ok(a.get("/api/admin/works"), 403)
    ok(m.get("/api/my/works"), 403)


def archive(m: httpx.Client) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(ROOT / "fixtures" / "vkr-title.pdf", "ВКР/Калинин_ВКР.pdf")
        z.writestr("ВКР/битый.pdf", b"not a pdf")
        z.writestr("readme.txt", b"skip me")
    form = {"kind": "archive", "workType": "vkr"}
    created = ok(m.post("/api/admin/imports", data=form, files={"file": ("vkr.zip", buf.getvalue())}), 201).json()
    for _ in range(60):
        detail = ok(m.get(f"/api/admin/imports/{created['id']}")).json()
        if detail["import"]["status"] == "done":
            break
        time.sleep(3)
    items = {i["ref"]: i for i in detail["items"]}
    assert items["битый.pdf"]["error"] == "Файл не является PDF"
    good = items["Калинин_ВКР.pdf"]
    assert good["status"] in ("published", "duplicate"), good  # чистая — сразу на сайт; повторный прогон — дубль
    if good["status"] == "published":
        work = ok(m.get(f"/api/admin/works/{good['workId']}")).json()
        assert work["status"] == "published" and work["accessLevel"] == "card_only" and work["authors"][0]["personId"]
        assert work["supervisors"][0]["displayName"] == "Никитина Анна Сергеевна", work["supervisors"]
        assert work["direction"]["code"] == "09.03.04" and work["year"] == 2026 and work["pages"] == 64
    # убрать за собой: иначе следующий прогон (и коллекция Bruno) увидит дубль вместо публикации
    ok(m.post(f"/api/admin/works/{good['workId']}/reject", json={"reason": "Смоук-тест"}))


def grafana() -> None:
    password = (ROOT / "secrets" / "grafana_admin").read_text().strip()
    # как loopback(), только POST
    g = httpx.Client(base_url="https://127.0.0.1", verify=TLS, auth=("admin", password), timeout=60)
    sni = {"sni_hostname": "stats.localhost"}
    for f in sorted((ROOT / "monitoring" / "grafana" / "dashboards").glob("*.json")):
        for panel in json.loads(f.read_text(encoding="utf-8"))["panels"]:
            for t in panel.get("targets", []):
                query = t | {"datasource": t.get("datasource") or panel["datasource"], "intervalMs": 60000}
                r = g.post(
                    "/api/ds/query",
                    json={"queries": [query], "from": "now-24h", "to": "now"},
                    headers={"Host": "stats.localhost"},
                    extensions=sni,
                )
                error = r.json().get("results", {}).get(t["refId"], {}).get("error") if r.status_code == 200 else r.text
                assert not error, f"{f.stem} / {panel['title']}: {error}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--grafana", action="store_true")
    args = parser.parse_args()
    m = client(*MODERATOR)
    steps = [
        ("публичный каталог", public),
        ("выгрузки", lambda: exports(m)),
        ("кабинет автора", lambda: author_cabinet(m)),
    ]
    if args.archive:
        steps.append(("архив через LLM", lambda: archive(m)))
    if args.grafana:
        steps.append(("панели Grafana", grafana))
    for name, run in steps:
        started = time.perf_counter()
        run()
        print(f"ok  {name} ({time.perf_counter() - started:.1f} с)")


if __name__ == "__main__":
    sys.exit(main())
