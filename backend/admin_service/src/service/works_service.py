"""Рабочее место модератора: редактор, проверки, публикация, bulk, очередь проверки, DOI, файлы и материалы."""

import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on

from admin_service.src.service import doi as crossref
from shared.auth.session import SessionUser
from shared.config import UPLOAD_LIMITS
from shared.db.base import now
from shared.exceptions import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from shared.logging import get_logger
from shared.refdata import Classifier
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository, admin_options, matches
from shared.storage import Storage, detect_type
from shared.uow import UnitOfWork, UnitOfWorkFactory
from shared.works import STUDENT_TYPES, WORK_TYPES, Material, Work, WorkPerson
from shared.works import labels as L
from shared.works.checks import ACTIONS, admin_dict, blank, check_work, errors
from shared.works.schemas import RELATED, CommonIn, apply_write, comparable, parse_write, template, work_dict

logger = get_logger(__name__)

DOI_FIELDS = ("title", "authors", "venue", "year", "volume", "issue", "pages", "issn", "kind", "language", "indexing")
CONTENT_TYPES = {"pdf": "application/pdf"}


def same(a: Any, b: Any) -> bool:
    return (blank(a) and blank(b)) or a == b


def _no_authors(v: Any) -> bool:
    """Пустое поле для DOI; строка авторов без ФИО ({displayName: ''}) тоже пустая."""
    return blank(v) or (isinstance(v, list) and all(isinstance(x, dict) and not x.get("displayName") for x in v))


def _shown(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x.get("displayName") if isinstance(x, dict) else x) for x in v)
    return "—" if blank(v) else str(v)


def conditions(
    *,
    type_: str | None = None,
    status: str | None = None,
    import_id: UUID | None = None,
    year: int | None = None,
    department: str | None = None,
    q: str | None = None,
) -> list:
    """Фильтры «Всех работ»: те же у списка, очереди и выгрузок."""
    out = []
    if type_:
        out.append(Work.type == type_)
    if status:
        out.append(Work.status == status)
    if import_id:
        out.append(Work.import_id == import_id)
    if year:
        out.append(Work.year == year)
    if department:
        out.append(Work.department == department)
    if q:
        out.append(or_(matches(q), Work.original_file_name.icontains(q, autoescape=True)))
    return out


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

    async def _queue(self, uow: UnitOfWork, where: list) -> list[Work]:
        """Порядок «больше замечаний — выше». ponytail: проверки считаются в Python по всем черновикам;
        при тысячах черновиков число замечаний придётся хранить колонкой."""
        works = (await uow.session.execute(select(Work).where(*where))).scalars().all()
        return sorted(works, key=lambda w: (-len(check_work(w)[1]), w.created_at, str(w.id)))

    async def list(self, *, sort: str | None, page: int, page_size: int, **filters: Any) -> dict:
        if sort == "problems":
            filters["status"] = filters.get("status") or "draft"  # очередь — черновики, а не вся таблица в памяти
        where = conditions(**filters)
        async with self.uow_factory() as uow:
            if sort == "problems":
                ordered = await self._queue(uow, where)
                total, ids = len(ordered), [w.id for w in ordered[(page - 1) * page_size : page * page_size]]
                page_works = {
                    w.id: w
                    for w in await self.works.all(uow, select(Work).where(Work.id.in_(ids)).options(*admin_options()))
                }
                works = [page_works[i] for i in ids]
            else:
                stmt = (
                    select(Work)
                    .where(*where)
                    .options(*admin_options())
                    .order_by(Work.updated_at.desc(), Work.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                works = list(await self.works.all(uow, stmt))
                total = await uow.session.scalar(select(func.count()).select_from(Work).where(*where))
            return {"items": await self._admin(uow, works), "total": total}

    async def next_in_queue(self, after: UUID | None, type_: str | None, import_id: UUID | None) -> dict:
        """Следующий черновик очереди после after; после только что опубликованного — голова очереди."""
        async with self.uow_factory() as uow:
            queue = await self._queue(uow, conditions(status="draft", type_=type_, import_id=import_id))
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
            .ext(distinct_on(name))  # по конференции — последний введённый доклад: город и даты из него
            .where(Work.type == "talk", name.icontains(q, autoescape=True))
            .order_by(name, Work.updated_at.desc())
            .limit(15)
        )
        async with self.uow_factory() as uow:
            return list((await uow.session.execute(stmt)).scalars())

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
    ) -> list[str]:
        """Поля, отличающиеся от прежней версии (у новой — от шаблона), получают источник source. → изменённые поля."""
        await self._prepare_refs(uow, model)
        await self._check_subjects(uow, model)
        old, new = comparable(before), comparable(model)
        meta = dict(w.field_meta)
        changed = []
        for key in model.model_fields_set:
            if key == "event":
                changed += [f"event.{sub}" for sub, v in new["event"].items() if not same(old["event"].get(sub), v)]
            elif not same(old.get(key), new[key]):
                changed.append(key)
        meta |= {key: {"source": source} for key in changed}
        previous_related = w.related_id
        apply_write(w, model)
        if w.type in STUDENT_TYPES and w.access_level == "full" and not w.consent:
            message = "Нет согласия студента на открытый доступ к тексту"
            raise UnprocessableError(message, fields=[{"field": "accessLevel", "message": message}])
        if w.department and not await self.works.known_department(uow, w.department):
            raise BadRequestError(
                f"Кафедры «{w.department}» нет в справочнике: выберите из списка", code="bad_department"
            )
        w.field_meta = meta
        w.updated_at = now()
        uow.session.add(w)
        await self._sync_related(uow, w, previous_related)
        await self.works.refresh_search(uow, [w.id])
        # Публикацию проверяет publish, но правка уже опубликованной иначе оставила бы в каталоге работу без названия.
        if w.status == "published":
            fresh = await self._load(uow, w.id)  # подписи рубрик у новых строк приходят только запросом
            if errs := errors(work_dict(fresh)):
                message = "Опубликованная работа должна оставаться полной: " + "; ".join(e["message"] for e in errs)
                raise UnprocessableError(message, fields=errs)
        return changed

    @staticmethod
    def _audit_edit(work_id: UUID, changed: list[str], user: SessionUser, source: str = "manual") -> None:
        logger.info(
            "Работа изменена",
            extra={"audit": True, "work_id": str(work_id), "fields": changed, "source": source, "by": str(user.id)},
        )

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
        logger.info(
            "Работа создана", extra={"audit": True, "work_id": str(w.id), "type": work_type, "by": str(user.id)}
        )
        return await self.get(w.id)

    async def patch(self, work_id: UUID, body: dict, user: SessionUser) -> dict:
        async with self.uow_factory() as uow:
            w = await self._load(uow, work_id)
            # Форма шлёт updatedAt версии, с которой начали: иначе второй модератор молча затёр бы правки первого.
            if (seen := body.get("updatedAt")) and seen != w.updated_at.isoformat():
                raise ConflictError("Работу уже изменили, пока вы её редактировали: обновите страницу", code="stale")
            changed = await self._save(uow, w, parse_write(w.type, body), parse_write(w.type, work_dict(w)))
        self._audit_edit(work_id, changed, user)
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
        """Публикуются только полные работы; уже опубликованные не трогаются.
        Доступ меняется и у неполных черновиков, кроме открытого текста студенческой работы без согласия."""
        done, skipped = 0, []
        async with self.uow_factory() as uow:
            works = (await uow.session.execute(select(Work).where(Work.id.in_(ids)))).scalars().all()
            for w in works:
                if action == "publish":
                    if w.status == "published":
                        continue
                    if check_work(w)[0]:
                        skipped.append(w.title or "Без названия")
                        continue
                    w.status, w.reject_reason = "published", None
                    w.published_at = w.published_at or now()
                else:
                    if access_level == "full" and w.type in STUDENT_TYPES and not w.consent:
                        skipped.append(w.title or "Без названия")
                        continue
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

    async def fill_from_doi(self, work_id: UUID, raw: str, overwrite: list[str], user: SessionUser) -> dict:
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
                if not _no_authors(current[key]) and key in w.field_meta:
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
            changed = await self._save(
                uow, w, parse_write("publication", changes), parse_write("publication", current), source="doi"
            )
        self._audit_edit(work_id, changed, user, "doi")
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
            old = w.file_key
            w.file_key, w.original_file_name, w.updated_at = key, file.filename, now()
        # Прежний файл больше не нужен. Удаляем только свой объект работы: демо-работы делят demo/sample.pdf.
        if old and old.startswith(f"works/{work_id}/"):
            await self.storage.delete(old)
        logger.info("Файл работы заменён", extra={"work_id": str(work_id), "key": key, "previous": old})
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

    async def review_material(
        self, work_id: UUID, material_id: UUID, status: str, user: SessionUser, reason: str = ""
    ) -> dict:
        async with self.uow_factory() as uow:
            m = await self._material(uow, work_id, material_id)
            m.status, m.decided_by, m.decided_at = status, user.id, now()
            m.reject_reason = reason.strip() or None if status == "rejected" else None
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
