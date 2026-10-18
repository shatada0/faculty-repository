"""Рабочее место модератора: редактор, проверки, публикация, bulk, очередь проверки, DOI, файлы и материалы."""

import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import and_, func, or_, select, update

from admin_service import doi as crossref
from shared import labels as L
from shared.auth import SessionUser
from shared.checks import ACTIONS, admin_dict, check_work, errors
from shared.config import UPLOAD_LIMITS
from shared.db.base import now
from shared.db.uow import UnitOfWork, UnitOfWorkFactory
from shared.exceptions import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from shared.logging import get_logger
from shared.models import STUDENT_TYPES, WORK_TYPES, Classifier, Material, Work, WorkPerson
from shared.repository.people import PersonRepository
from shared.repository.works import WorkRepository, admin_options, matches
from shared.storage import Storage, detect_type
from shared.works import RELATED, CommonIn, apply_write, comparable, parse_write, template, work_dict

logger = get_logger(__name__)

DOI_FIELDS = ("title", "authors", "venue", "year", "volume", "issue", "pages", "issn", "kind", "language", "indexing")
CONTENT_TYPES = {"pdf": "application/pdf"}


def _blank(v: Any) -> bool:
    return v is None or v == ""


def same(a: Any, b: Any) -> bool:
    return (_blank(a) and _blank(b)) or a == b


def _empty(v: Any) -> bool:
    """Пустая строка авторов ({displayName: ''}) тоже пустая."""
    return _blank(v) or (isinstance(v, list) and all(isinstance(x, dict) and not x.get("displayName") for x in v))


def _shown(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x.get("displayName") if isinstance(x, dict) else x) for x in v)
    return "—" if _blank(v) else str(v)


async def check_upload(file: UploadFile, rule: str) -> tuple[str, str]:
    """Расширение, размер и сигнатура. Тип по содержимому, ContentType ставит сервер. → (расширение, content-type)."""
    limits = UPLOAD_LIMITS[rule]
    name = file.filename or ""
    ext = name[name.rfind(".") :].lower() if "." in name else ""
    head = await file.read(8)
    await file.seek(0)
    kind = detect_type(head)
    fits = ext in limits["accept"] and (file.size or 0) <= limits["maxBytes"]
    if not fits or kind is None or (ext == ".pdf") != (kind == "pdf"):
        raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
    return ext, CONTENT_TYPES.get(kind, "application/octet-stream")


@dataclass(slots=True)
class WorkService:
    uow_factory: UnitOfWorkFactory
    works: WorkRepository
    people: PersonRepository
    storage: Storage

    async def _load(self, uow: UnitOfWork, work_id: UUID) -> Work:
        w = await self.works.get(uow, work_id, admin=True)
        if w is None:
            raise NotFoundError("Работа не найдена")
        return w

    async def _admin(self, uow: UnitOfWork, works: list[Work]) -> list[dict]:
        lists = await self.works.journal_lists(uow, works)
        return [
            admin_dict(w, [L.JOURNAL_LISTS.get(c, c) for c in lists[w.id]] if w.id in lists else None) for w in works
        ]

    async def get(self, work_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            return (await self._admin(uow, [await self._load(uow, work_id)]))[0]

    # ---------- списки ----------

    async def summary(self) -> dict:
        async with self.uow_factory() as uow:
            drafts = (await uow.session.execute(select(Work).where(Work.status == "draft"))).scalars().all()
            pending = (
                await uow.session.execute(
                    select(Material.work_id, Work.title, Material.name)
                    .join(Work, Work.id == Material.work_id)
                    .where(Material.status == "pending")
                    .order_by(Material.uploaded_at)
                )
            ).all()
        by_type = dict.fromkeys(WORK_TYPES, 0)
        for w in drafts:
            by_type[w.type] += 1
        return {
            "drafts": {
                "total": len(drafts),
                "withIssues": sum(1 for w in drafts if check_work(w)[1]),
                "byType": by_type,
            },
            "pendingMaterials": [{"workId": str(wid), "title": title, "name": name} for wid, title, name in pending],
        }

    @staticmethod
    def _conditions(
        *,
        type_: str | None = None,
        status: str | None = None,
        import_id: UUID | None = None,
        year: int | None = None,
        department: str | None = None,
        q: str | None = None,
    ) -> list:
        conditions = []
        if type_:
            conditions.append(Work.type == type_)
        if status:
            conditions.append(Work.status == status)
        if import_id:
            conditions.append(Work.import_id == import_id)
        if year:
            conditions.append(Work.year == year)
        if department:
            conditions.append(Work.department == department)
        if q:
            conditions.append(or_(matches(q), Work.original_file_name.icontains(q, autoescape=True)))
        return conditions

    async def _queue(self, uow: UnitOfWork, conditions: list) -> list[Work]:
        """Порядок «больше замечаний — выше». ponytail: проверки считаются в Python по всем черновикам;
        при тысячах черновиков число замечаний придётся хранить колонкой."""
        works = (await uow.session.execute(select(Work).where(*conditions))).scalars().all()
        return sorted(works, key=lambda w: (-len(check_work(w)[1]), w.created_at, str(w.id)))

    async def list(self, *, sort: str | None, page: int, page_size: int, **filters: Any) -> dict:
        conditions = self._conditions(**filters)
        async with self.uow_factory() as uow:
            if sort == "problems":
                ordered = await self._queue(uow, conditions)
                total, ids = len(ordered), [w.id for w in ordered[(page - 1) * page_size : page * page_size]]
                page_works = {
                    w.id: w
                    for w in await self.works.all(uow, select(Work).where(Work.id.in_(ids)).options(*admin_options()))
                }
                works = [page_works[i] for i in ids]
            else:
                stmt = (
                    select(Work)
                    .where(*conditions)
                    .options(*admin_options())
                    .order_by(Work.updated_at.desc(), Work.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                works = list(await self.works.all(uow, stmt))
                total = await uow.session.scalar(select(func.count()).select_from(Work).where(*conditions))
            return {"items": await self._admin(uow, works), "total": total}

    async def next_in_queue(self, after: UUID | None, type_: str | None, import_id: UUID | None) -> dict:
        """Следующий черновик очереди после after; после только что опубликованного — голова очереди."""
        async with self.uow_factory() as uow:
            queue = await self._queue(uow, self._conditions(status="draft", type_=type_, import_id=import_id))
        ids = [w.id for w in queue]
        if after in ids:
            i = ids.index(after)
            nxt = ids[i + 1] if i + 1 < len(ids) else None
        else:
            nxt = next((i for i in ids if i != after), None)
        return {"id": str(nxt) if nxt else None}

    async def related_options(self, type_: str | None, authors: list[UUID], include: UUID | None) -> list[dict]:
        """Работы другого типа с общими авторами — для поля «связанный доклад / публикация»."""
        if type_ not in ("publication", "talk"):
            return []
        shared_author = Work.id.in_(
            select(WorkPerson.work_id).where(WorkPerson.role == "author", WorkPerson.person_id.in_(authors))
        )
        stmt = (
            select(Work.id, Work.year, Work.title)
            .where(Work.type == type_, or_(Work.id == include, shared_author))
            .order_by(Work.updated_at.desc())
            .limit(200)
        )
        async with self.uow_factory() as uow:
            rows = (await uow.session.execute(stmt)).all()
        return [{"id": str(i), "label": f"{year or '—'} · {title[:90]}"} for i, year, title in rows]

    async def events(self, q: str) -> list[dict]:
        """Конференции из уже введённых докладов — для автозаполнения."""
        name = Work.details[("event", "name")].astext
        stmt = (
            select(Work.details["event"])
            .where(Work.type == "talk", name.icontains(q, autoescape=True))
            .order_by(name, Work.updated_at.desc())
            .limit(200)
        )
        async with self.uow_factory() as uow:
            rows = (await uow.session.execute(stmt)).scalars().all()
        seen: dict[str, dict] = {}
        for event in rows:
            seen.setdefault(event["name"], event)
        return list(seen.values())[:15]

    async def classifiers(self, scheme: str, q: str) -> list[dict]:
        stmt = select(Classifier).where(Classifier.scheme == scheme)
        if q.strip():
            stmt = stmt.where(
                or_(
                    Classifier.code.istartswith(q.strip(), autoescape=True),
                    Classifier.name.icontains(q.strip(), autoescape=True),
                )
            )
        async with self.uow_factory() as uow:
            rows = (await uow.session.execute(stmt.order_by(Classifier.code).limit(20))).scalars()
            return [{"scheme": c.scheme, "code": c.code, "name": c.name} for c in rows]

    # ---------- запись ----------

    def template(self, work_type: str) -> dict:
        data = template(work_type, now().year)
        # те же поля, что у сохранённой работы: форма редактора одна
        return data | {
            "fieldMeta": {},
            "errors": [],
            "issues": [],
            "actions": ["publish"],
            "materials": [],
            "status": "draft",
        }

    async def _prepare_refs(self, uow: UnitOfWork, model: CommonIn) -> None:
        """personId должен существовать; вид ссылки — как у человека (объединённый — на основного)."""
        for field in ("authors", "supervisors", "presenters"):
            for i, ref in enumerate(getattr(model, field, None) or []):
                if ref.personId is None:
                    continue
                person = await self.people.resolve(uow, ref.personId)
                if person is None:
                    raise UnprocessableError(
                        "Человек не найден", fields=[{"field": f"{field}.{i}.personId", "message": "Человек не найден"}]
                    )
                ref.personId, ref.kind = person.id, person.kind

    async def _check_subjects(self, uow: UnitOfWork, model: CommonIn) -> None:
        if "subjects" not in model.model_fields_set or not model.subjects:
            return
        pairs = {(s.scheme, s.code) for s in model.subjects}
        found = (
            await uow.session.execute(
                select(Classifier.scheme, Classifier.code).where(
                    or_(*(and_(Classifier.scheme == s, Classifier.code == c) for s, c in pairs))
                )
            )
        ).all()
        if missing := pairs - set(map(tuple, found)):
            raise UnprocessableError(
                f"Нет в классификаторе: {', '.join(c for _, c in sorted(missing))}",
                fields=[{"field": "subjects", "message": "Нет в классификаторе"}],
            )

    async def _sync_related(self, uow: UnitOfWork, w: Work, previous: UUID | None) -> None:
        """Связь публикация ↔ доклад симметрична; старая пара разрывается в той же транзакции."""
        if w.type not in RELATED or previous == w.related_id:
            return
        if previous and (old := await uow.session.get(Work, previous)) and old.related_id == w.id:
            old.related_id = None
        if w.related_id:
            target = await uow.session.get(Work, w.related_id)
            other = "talk" if w.type == "publication" else "publication"
            if target is None or target.type != other:
                raise UnprocessableError(
                    "Связанная работа не найдена", fields=[{"field": RELATED[w.type], "message": "Работа не найдена"}]
                )
            if (
                target.related_id
                and target.related_id != w.id
                and (stale := await uow.session.get(Work, target.related_id))
            ):
                stale.related_id = None
            target.related_id = w.id

    async def _save(
        self, uow: UnitOfWork, w: Work, model: CommonIn, before: CommonIn, *, source: str = "manual"
    ) -> None:
        """Поля, отличающиеся от прежней версии (у новой — от шаблона), получают источник source."""
        await self._prepare_refs(uow, model)
        await self._check_subjects(uow, model)
        old, new = comparable(before), comparable(model)
        meta = dict(w.field_meta)
        for key in model.model_fields_set:
            if key == "event":
                for sub, value in new["event"].items():
                    if not same(old["event"].get(sub), value):
                        meta[f"event.{sub}"] = {"source": source}
            elif not same(old.get(key), new[key]):
                meta[key] = {"source": source}
        previous_related = w.related_id
        apply_write(w, model)
        if w.type in STUDENT_TYPES and w.access_level == "full" and not w.consent:
            message = "Нет согласия студента на открытый доступ к тексту"
            raise UnprocessableError(message, fields=[{"field": "accessLevel", "message": message}])
        await self.works.ensure_department(uow, w.department)
        w.field_meta = meta
        w.updated_at = now()
        uow.session.add(w)
        await self._sync_related(uow, w, previous_related)
        await self.works.refresh_search(uow, [w.id])

    async def create(self, body: dict, user: SessionUser) -> dict:
        work_type = body.get("type")
        if work_type not in WORK_TYPES:
            raise BadRequestError("Неизвестный тип работы")
        model = parse_write(work_type, body)
        before = parse_write(work_type, template(work_type, now().year))
        w = Work(
            id=uuid.uuid7(), type=work_type, status="draft", details={}, field_meta={}, keywords=[], created_by=user.id
        )
        async with self.uow_factory() as uow:
            await self._save(uow, w, model, before)
        logger.info("Работа создана", extra={"work_id": str(w.id), "type": work_type})
        return await self.get(w.id)

    async def patch(self, work_id: UUID, body: dict) -> dict:
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            await self._save(uow, w, parse_write(w.type, body), parse_write(w.type, work_dict(w)))
        return await self.get(work_id)

    async def _transition(self, work_id: UUID, action: str, user: SessionUser, **changes: Any) -> dict:
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            if action not in ACTIONS[w.status]:
                raise ConflictError(
                    {
                        "publish": "Работа уже опубликована",
                        "reject": "Работа уже отклонена",
                        "unpublish": "Работа не опубликована",
                    }[action],
                    code="bad_state",
                )
            if action == "publish" and (errs := errors(work_dict(w))):
                raise UnprocessableError("; ".join(e["message"] for e in errs), fields=errs)
            for k, v in changes.items():
                setattr(w, k, v)
            if action == "publish":
                w.published_at = w.published_at or now()
            w.updated_at = now()
        logger.info(
            "Статус работы изменён",
            extra={"audit": True, "action": action, "work_id": str(work_id), "by": str(user.id)},
        )
        return await self.get(work_id)

    async def publish(self, work_id: UUID, user: SessionUser) -> dict:
        return await self._transition(work_id, "publish", user, status="published", reject_reason=None)

    async def unpublish(self, work_id: UUID, user: SessionUser) -> dict:
        return await self._transition(work_id, "unpublish", user, status="draft")

    async def reject(self, work_id: UUID, reason: str, user: SessionUser) -> dict:
        return await self._transition(work_id, "reject", user, status="rejected", reject_reason=reason)

    async def bulk(self, ids: list[UUID], action: str, access_level: str | None, user: SessionUser) -> dict:
        """Каждая работа проверяется по результату: открыть текст студенческой работы можно только с согласием."""
        done, skipped = 0, []
        async with self.uow_factory() as uow:
            works = (await uow.session.execute(select(Work).where(Work.id.in_(ids)))).scalars().all()
            for w in works:
                overrides = {"accessLevel": access_level} if action == "access" else {}
                if check_work(w, **overrides)[0]:
                    skipped.append(w.title)
                    continue
                if action == "publish":
                    w.status, w.reject_reason = "published", None
                    w.published_at = w.published_at or now()
                else:
                    w.access_level = access_level
                w.updated_at = now()
                done += 1
        logger.info(
            "Массовое действие",
            extra={
                "audit": True,
                "action": action,
                "access_level": access_level,
                "ids": [str(i) for i in ids],
                "done": done,
                "by": str(user.id),
            },
        )
        return {"done": done, "skipped": skipped}

    # ---------- DOI ----------

    async def fill_from_doi(self, work_id: UUID, raw: str, overwrite: list[str]) -> dict:
        """Пустые и нетронутые поля заполняются (источник doi); заполненные и отличающиеся — конфликты, пока их не перечислят."""
        doi = crossref.normalize_doi(raw)
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            if w.type != "publication":
                raise BadRequestError("DOI бывает только у публикаций")
        incoming = await crossref.lookup(doi)
        conflicts = []
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            if "authors" in incoming:
                incoming["authors"] = [await self._ref_to(uow, name) for name in incoming["authors"]]
            current = work_dict(w)
            changes: dict[str, Any] = {"doi": doi}
            for key in DOI_FIELDS:
                if key not in incoming:
                    continue
                if not _empty(current[key]) and key in w.field_meta:
                    if same(current[key], incoming[key]):
                        continue
                    if key not in overwrite:
                        conflicts.append(
                            {
                                "field": key,
                                "label": L.FIELD.get(key, key),
                                "current": _shown(current[key]),
                                "incoming": _shown(incoming[key]),
                            }
                        )
                        continue
                changes[key] = incoming[key]
            await self._save(
                uow, w, parse_write("publication", changes), parse_write("publication", current), source="doi"
            )
        return {"work": await self.get(work_id), "conflicts": conflicts}

    async def _ref_to(self, uow: UnitOfWork, name: str, fallback: str = "external") -> dict:
        person = await self.people.match_exact(uow, name)
        if person:
            return {"personId": str(person.id), "displayName": name, "kind": person.kind}
        return {"personId": None, "displayName": name, "kind": fallback}

    # ---------- файлы и материалы ----------

    async def upload_file(self, work_id: UUID, file: UploadFile) -> dict:
        ext, content_type = await check_upload(file, "workFile")
        async with self.uow_factory() as uow:
            await self._load(uow, work_id)
        key = f"works/{work_id}/{uuid.uuid4()}{ext}"
        await self.storage.put(key, file.file, content_type)
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            w.file_key, w.original_file_name, w.updated_at = key, file.filename, now()
        return await self.get(work_id)

    async def file_link(self, work_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            w = await self.works.get(uow, work_id)
        if w is None or not w.file_key:
            raise NotFoundError("Файл не загружен")
        return await self.storage.presign(
            w.file_key, w.original_file_name or f"{w.id}.pdf", inline=w.file_key.endswith(".pdf")
        )

    async def _material(self, uow: UnitOfWork, work_id: UUID, material_id: UUID) -> Material:
        m = await uow.session.get(Material, material_id)
        if m is None or m.work_id != work_id:
            raise NotFoundError("Материал не найден")
        return m

    async def material_link(self, work_id: UUID, material_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            m = await self._material(uow, work_id, material_id)
        return await self.storage.presign(m.file_key, m.name, inline=False)

    async def review_material(self, work_id: UUID, material_id: UUID, status: str, user: SessionUser) -> dict:
        async with self.uow_factory() as uow:
            m = await self._material(uow, work_id, material_id)
            m.status, m.decided_by, m.decided_at = status, user.id, now()
        logger.info(
            "Решение по материалу",
            extra={
                "audit": True,
                "work_id": str(work_id),
                "material_id": str(material_id),
                "status": status,
                "by": str(user.id),
            },
        )
        return await self.get(work_id)


async def relink_unmatched(uow: UnitOfWork, display_name: str, person_id: UUID, kind: str) -> list[UUID]:
    """Нераспознанный автор с этим написанием → человек во всех работах. Возвращает затронутые работы."""
    rows = await uow.session.execute(
        update(WorkPerson)
        .where(WorkPerson.person_id.is_(None), WorkPerson.display_name == display_name)
        .values(person_id=person_id, kind=kind)
        .returning(WorkPerson.work_id)
    )
    return list({r[0] for r in rows})
