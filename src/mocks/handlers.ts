// MSW handlers implementing the API contract (section 7) over the in-memory db.
import { delay, http, HttpResponse, type DefaultBodyType, type HttpResponseResolver, type PathParams } from 'msw'
import type { AccessLevel, AdminWork, MaterialKind, MaterialStatus, Person, PersonKind, SubjectScheme, User, WorkType } from '@/api/types'
import { DEPTS } from './data'
import { gostCitation } from './gost'
import { shortName } from './names'
import { templateCsv, templates, toCsv } from './table'
import { db, type MockPerson } from './seed'
import {
  adminPeople, adminWorks, advance, allAuthorRefs, applyEdit, asAuthor, canDo, catalog, createImport, departments, DoiError, facets, fillFromDoi,
  fits, ImportError, isVisible, latest, match, merge, metrics, myWorks, nextInQueue, personCard, personWorks, previewTable, profile,
  publicImport, publicItem, relatedOptions, resolvePerson, searchClassifier, staffList, suggest, summary, syncRelated, template, toPublic, unmatched,
  UPLOAD_LIMITS, withChecks,
} from './logic'

// Departments are a reference list, as on the backend: anything else is a 400, not a new department.
const unknownDepartment = (d: unknown) =>
  d && !DEPTS.some((x) => x.name === d) ? err(400, 'bad_department', `Кафедры «${d}» нет в справочнике: выберите из списка`) : undefined

// ---- dev toggles (mock panel) ----
const KEY = 'mock-settings'
export const settings: { delay: boolean; errors: boolean } = (() => {
  try { return { delay: true, errors: false, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') } } catch { return { delay: true, errors: false } }
})()
export function saveSettings() {
  try { localStorage.setItem(KEY, JSON.stringify(settings)) } catch { /* private mode */ }
}

const USERS: Record<string, { password: string; user: User }> = {
  moderator: { password: 'moderator', user: { id: 'u1', name: 'Модератор', role: 'moderator' } },
  orlov: { password: 'orlov', user: { id: 'u2', name: 'Орлов Д. С.', role: 'author', personId: 'p-s1' } },
}
// The fake server keeps its "cookie session" in sessionStorage so a page reload keeps the moderator signed in.
const SESSION_KEY = 'mock-session'
let session: User | null = (() => {
  try { return USERS[sessionStorage.getItem(SESSION_KEY) ?? '']?.user ?? null } catch { return null }
})()
const setSession = (login: string | null) => {
  session = login ? USERS[login].user : null
  try { if (login) sessionStorage.setItem(SESSION_KEY, login); else sessionStorage.removeItem(SESSION_KEY) } catch { /* private mode */ }
}

const err = (status: number, code: string, message: string) => HttpResponse.json({ error: { code, message } }, { status })
const notFound = (what = 'Не найдено') => err(404, 'not_found', what)
const sp = (request: Request) => new URL(request.url).searchParams
const now = () => new Date().toISOString()

type R = HttpResponseResolver<PathParams, DefaultBodyType>
/** `true` = moderator or admin, 'author' = a signed-in person uploading to own works. */
type Auth = boolean | 'author'
function wrap(resolver: R, auth: Auth = false): R {
  return async (info) => {
    if (settings.delay) await delay(150 + Math.random() * 450)
    if (settings.errors && Math.random() < 0.15 && !info.request.url.includes('/auth/')) return err(500, 'random', 'Случайная ошибка сервера (имитация)')
    if (auth && !session) return err(401, 'unauthorized', 'Требуется вход')
    if (auth === true && session!.role === 'author') return err(403, 'forbidden', 'Нужна роль модератора')
    if (auth === 'author' && !session!.personId) return err(403, 'forbidden', 'Вход не связан с автором')
    return resolver(info)
  }
}
const get = (path: string, r: R, auth: Auth = false) => http.get(`/api${path}`, wrap(r, auth))
const post = (path: string, r: R, auth: Auth = false) => http.post(`/api${path}`, wrap(r, auth))
const patch = (path: string, r: R, auth: Auth = false) => http.patch(`/api${path}`, wrap(r, auth))
const del = (path: string, r: R, auth: Auth = false) => http.delete(`/api${path}`, wrap(r, auth))

const work = (id: unknown) => db.works.find((w) => w.id === id)
const adminJson = (w: AdminWork, status = 200) => HttpResponse.json(withChecks(w), { status })
const publish = (w: AdminWork) => Object.assign(w, { status: 'published', publishedAt: w.publishedAt ?? now(), updatedAt: now(), rejectReason: undefined })
const isWorkType = (t: unknown): t is WorkType => ['vkr', 'coursework', 'publication', 'talk'].includes(t as string)
const fileLink = () => ({ url: `/mock/sample.pdf?token=${Math.random().toString(36).slice(2)}`, expiresAt: new Date(Date.now() + 10 * 60_000).toISOString() })

const csvFile = (name: string, rows: string[][]) => new HttpResponse(toCsv(rows), {
  headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(name)}` },
})

export const handlers = [
  // ---------- public ----------
  get('/stats', () => {
    const byType = { vkr: 0, coursework: 0, publication: 0, talk: 0 } as Record<WorkType, number>
    db.works.filter(isVisible).forEach((w) => byType[w.type]++)
    return HttpResponse.json({ byType, staff: db.people.filter((p) => p.kind === 'staff' && !p.mergedInto).length })
  }),
  get('/works/facets', ({ request }) => HttpResponse.json(facets(sp(request)))),
  get('/works/latest', () => HttpResponse.json(latest())),
  get('/works/suggest', ({ request }) => HttpResponse.json(suggest((sp(request).get('q') ?? '').slice(0, 200)))),
  get('/works', ({ request }) => HttpResponse.json(catalog(sp(request)))),
  get('/works/:id', ({ params }) => {
    const w = work(params.id)
    return w && isVisible(w) ? HttpResponse.json(toPublic(w)) : notFound('Работа не найдена')
  }),
  get('/works/:id/file', ({ params }) => {
    const w = work(params.id)
    if (!w || !isVisible(w)) return notFound('Работа не найдена')
    if (w.accessLevel !== 'full' || !w.hasFile) return err(403, 'forbidden', 'Текст недоступен для открытого просмотра')
    return HttpResponse.json(fileLink())
  }),
  get('/works/:id/citation', ({ params }) => {
    const w = work(params.id)
    return w && isVisible(w) ? HttpResponse.json({ gost: gostCitation(toPublic(w)) }) : notFound()
  }),
  get('/departments', () => HttpResponse.json(departments())),
  get('/people', ({ request }) => HttpResponse.json(staffList(sp(request)))),
  // Teachers, students and graduates have public pages; external authors do not.
  get('/people/:id', ({ params }) => {
    const p = resolvePerson(params.id as string)
    return p && p.kind !== 'external' ? HttpResponse.json(profile(p)) : notFound('Человек не найден')
  }),
  get('/people/:id/works', ({ params, request }) => {
    const p = resolvePerson(params.id as string)
    return p && p.kind !== 'external' ? HttpResponse.json(personWorks(p.id, sp(request))) : notFound()
  }),
  get('/people/:id/metrics', ({ params, request }) => {
    const p = resolvePerson(params.id as string)
    return p && p.kind !== 'external' ? HttpResponse.json(metrics(p.id, sp(request))) : notFound()
  }),
  get('/works/:id/materials/:mid', ({ params }) => {
    const w = work(params.id)
    const m = w && isVisible(w) ? w.materials?.find((x) => x.id === params.mid && x.status === 'approved') : undefined
    return m ? HttpResponse.json(fileLink()) : notFound('Материал не найден')
  }),
  get('/upload-limits', () => HttpResponse.json(UPLOAD_LIMITS)),
  post('/client-errors', () => new HttpResponse(null, { status: 204 })),

  // ---------- author ----------
  get('/my/works', () => HttpResponse.json(myWorks(session!.personId!)), 'author'),
  post('/my/works/:id/materials', async ({ params, request }) => {
    const w = work(params.id)
    if (!w || !asAuthor(w, session!.personId!)) return notFound('Работа не найдена')
    if (w.type !== 'publication' && w.type !== 'talk') return err(400, 'bad_request', 'Материалы добавляются к публикациям и выступлениям')
    const form = await request.formData()
    const file = form.get('file') as File | null
    if (!file || !fits(file, UPLOAD_LIMITS.material)) return err(400, 'bad_file', 'Файл не подходит по формату или размеру')
    const m = { id: `m-${++db.seq}`, name: file.name, size: file.size, kind: (form.get('kind') as MaterialKind) ?? 'other', status: 'pending' as const, uploadedBy: session!.name, uploadedAt: now() }
    w.materials = [...(w.materials ?? []), m]
    return HttpResponse.json(m, { status: 201 })
  }, 'author'),
  del('/my/works/:id/materials/:mid', ({ params }) => {
    const w = work(params.id)
    if (!w || !asAuthor(w, session!.personId!)) return notFound('Работа не найдена')
    const m = w.materials?.find((x) => x.id === params.mid)
    if (!m) return notFound('Материал не найден')
    if (m.status !== 'pending') return err(409, 'bad_state', 'Удалить можно только материал на проверке')
    w.materials = w.materials!.filter((x) => x !== m)
    return new HttpResponse(null, { status: 204 })
  }, 'author'),

  // ---------- auth ----------
  post('/auth/login', async ({ request }) => {
    const { login, password } = (await request.json()) as { login: string; password: string }
    const u = USERS[login]
    if (!u || u.password !== password) return err(401, 'invalid_credentials', 'Неверный логин или пароль')
    setSession(login)
    return HttpResponse.json({ user: u.user })
  }),
  post('/auth/logout', () => {
    setSession(null)
    return new HttpResponse(null, { status: 204 })
  }),
  get('/auth/me', () => (session ? HttpResponse.json({ user: session }) : err(401, 'unauthorized', 'Требуется вход'))),

  // ---------- imports ----------
  post('/admin/imports/preview', async ({ request }) => {
    const form = await request.formData()
    const file = form.get('file') as File | null
    const t = form.get('workType')
    if (!file || !isWorkType(t)) return err(400, 'bad_request', 'Нужны файл и тип работ')
    try {
      return HttpResponse.json(await previewTable(file, t))
    } catch (e) {
      if (e instanceof ImportError) return err(400, 'bad_table', e.message)
      throw e
    }
  }, true),
  post('/admin/imports', async ({ request }) => {
    try {
      const imp = await createImport(await request.formData(), session!.name)
      return HttpResponse.json(publicImport(imp), { status: 201 })
    } catch (e) {
      if (e instanceof ImportError) return err(400, 'bad_table', e.message)
      throw e
    }
  }, true),
  get('/admin/imports', () => {
    db.imports.forEach(advance)
    return HttpResponse.json(db.imports.map(publicImport))
  }, true),
  get('/admin/imports/:id', ({ params, request }) => {
    const imp = db.imports.find((i) => i.id === params.id)
    if (!imp) return notFound('Импорт не найден')
    advance(imp)
    const status = sp(request).get('status')
    return HttpResponse.json({ import: publicImport(imp), items: imp.items.filter((i) => !status || i.status === status).map(publicItem) })
  }, true),
  get('/admin/templates/:workType/columns', ({ params }) => {
    const t = params.workType
    return isWorkType(t) ? HttpResponse.json(templates[t].map((c) => ({ name: c.name, required: !!c.required }))) : notFound()
  }, true),
  get('/admin/templates/:workType', ({ params }) => {
    const t = params.workType
    if (!isWorkType(t)) return notFound()
    return new HttpResponse(templateCsv(t), {
      headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(`шаблон-${t}.csv`)}` },
    })
  }, true),

  // Exports: the backend writes xlsx in the import template's columns and a zip with PDFs; the mock gives a short CSV.
  get('/admin/exports/works', ({ request }) => {
    const q = sp(request)
    q.set('pageSize', '500')
    return csvFile('работы.csv', [['id', 'Тип', 'Название', 'Год', 'Статус'],
      ...adminWorks(q).items.map((w) => [w.id, w.type, w.title ?? '', String(w.year ?? ''), w.status])])
  }, true),
  get('/admin/exports/people', () => csvFile('люди.csv', [['ФИО', 'Варианты написания', 'Кафедра'],
    ...db.people.filter((p) => !p.mergedInto).map((p) => [p.fullName, (p.aliases ?? []).join('; '), p.department ?? ''])]), true),

  // ---------- works ----------
  get('/admin/summary', () => HttpResponse.json(summary()), true),
  get('/admin/works', ({ request }) => HttpResponse.json(adminWorks(sp(request))), true),
  get('/admin/works/template', ({ request }) => {
    const t = sp(request).get('type')
    if (!isWorkType(t)) return err(400, 'bad_request', 'Неизвестный тип работы')
    return HttpResponse.json({ ...template(t), fieldMeta: {}, errors: [], issues: [], actions: ['publish'] })
  }, true),
  get('/admin/classifiers/:scheme', ({ params, request }) => HttpResponse.json(searchClassifier(params.scheme as SubjectScheme, sp(request).get('q') ?? '')), true),
  get('/admin/works/next', ({ request }) => HttpResponse.json({ id: nextInQueue(sp(request)) }), true),
  get('/admin/works/related-options', ({ request }) => HttpResponse.json(relatedOptions(sp(request))), true),
  post('/admin/works/bulk', async ({ request }) => {
    const { ids, action, accessLevel } = (await request.json()) as { ids: string[]; action: 'publish' | 'access'; accessLevel?: AccessLevel }
    let done = 0
    const skipped: string[] = []
    for (const w of ids.map(work)) {
      if (!w) continue
      // Opening the full text of a student work still needs the student's consent: validate the result, not the current state.
      if (withChecks(action === 'access' ? { ...w, accessLevel: accessLevel! } : w).errors.length) { skipped.push(w.title); continue }
      if (action === 'publish') publish(w)
      else Object.assign(w, { accessLevel, updatedAt: now() })
      done++
    }
    return HttpResponse.json({ done, skipped })
  }, true),
  post('/admin/works', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    const type = body.type
    if (!isWorkType(type)) return err(400, 'bad_request', 'Неизвестный тип работы')
    const badDept = unknownDepartment(body.department)
    if (badDept) return badDept
    const prefix = { vkr: 'vkr', coursework: 'cw', publication: 'pub', talk: 'talk' }[type]
    const w = { type, keywords: [], language: 'ru', hasFile: false, fieldMeta: {}, id: `${prefix}-${++db.seq}`, status: 'draft', createdAt: now(), updatedAt: now() } as unknown as AdminWork
    applyEdit(w, body, template(type))
    db.works.push(w)
    syncRelated(w)
    return adminJson(w, 201)
  }, true),
  get('/admin/works/:id', ({ params }) => {
    const w = work(params.id)
    return w ? adminJson(w) : notFound('Работа не найдена')
  }, true),
  patch('/admin/works/:id', async ({ params, request }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    const before = { ...w }
    const body = (await request.json()) as Record<string, unknown>
    const badDept = unknownDepartment(body.department)
    if (badDept) return badDept
    applyEdit(w, body, before as unknown as Record<string, unknown>)
    w.updatedAt = now()
    syncRelated(w, before)
    return adminJson(w)
  }, true),
  post('/admin/works/:id/file', async ({ params, request }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    const file = (await request.formData()).get('file') as File | null
    Object.assign(w, { hasFile: true, originalFileName: file?.name, updatedAt: now() })
    return adminJson(w)
  }, true),
  get('/admin/works/:id/file', ({ params }) => {
    const w = work(params.id)
    return w?.hasFile ? HttpResponse.json(fileLink()) : notFound('Файл не загружен')
  }, true),
  get('/admin/works/:id/materials/:mid', ({ params }) => {
    const m = work(params.id)?.materials?.find((x) => x.id === params.mid)
    return m ? HttpResponse.json(fileLink()) : notFound('Материал не найден')
  }, true),
  post('/admin/works/:id/materials/:mid', async ({ params, request }) => {
    const w = work(params.id)
    const m = w?.materials?.find((x) => x.id === params.mid)
    if (!w || !m) return notFound('Материал не найден')
    m.status = ((await request.json()) as { status: MaterialStatus }).status
    return adminJson(w)
  }, true),
  post('/admin/works/:id/publish', ({ params }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    if (!canDo(w, 'publish')) return err(409, 'bad_state', 'Работа уже опубликована')
    const { errors } = withChecks(w)
    if (errors.length) return err(422, 'validation', errors.map((e) => e.message).join('; '))
    return adminJson(publish(w))
  }, true),
  post('/admin/works/:id/reject', async ({ params, request }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    if (!canDo(w, 'reject')) return err(409, 'bad_state', 'Работа уже отклонена')
    const { reason } = (await request.json()) as { reason: string }
    Object.assign(w, { status: 'rejected', rejectReason: reason, updatedAt: now() })
    return adminJson(w)
  }, true),
  post('/admin/works/:id/unpublish', ({ params }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    if (!canDo(w, 'unpublish')) return err(409, 'bad_state', 'Работа не опубликована')
    Object.assign(w, { status: 'draft', updatedAt: now() })
    return adminJson(w)
  }, true),
  post('/admin/works/:id/doi', async ({ params, request }) => {
    const w = work(params.id)
    if (!w) return notFound('Работа не найдена')
    if (w.type !== 'publication') return err(400, 'bad_request', 'DOI бывает только у публикаций')
    const { doi, overwrite = [] } = (await request.json()) as { doi?: string; overwrite?: string[] }
    try {
      const conflicts = fillFromDoi(w, doi ?? '', overwrite)
      return HttpResponse.json({ work: withChecks(w), conflicts })
    } catch (e) {
      if (e instanceof DoiError) return err(e.status, 'doi', e.message)
      throw e
    }
  }, true),
  get('/admin/events', ({ request }) => {
    const q = (sp(request).get('q') ?? '').toLowerCase()
    const seen = new Map<string, unknown>()
    for (const w of db.works) if (w.type === 'talk' && w.event.name.toLowerCase().includes(q) && !seen.has(w.event.name)) seen.set(w.event.name, w.event)
    return HttpResponse.json([...seen.values()].slice(0, 15))
  }, true),

  // ---------- people ----------
  get('/admin/people', ({ request }) => {
    const s = sp(request)
    return HttpResponse.json(s.get('unmatched') === 'true' ? unmatched() : adminPeople(s))
  }, true),
  post('/admin/people', async ({ request }) => {
    const body = (await request.json()) as Partial<Person>
    if (!body.fullName?.trim()) return err(400, 'bad_name', 'Укажите ФИО')
    const badDept = unknownDepartment(body.department)
    if (badDept) return badDept
    const p = { aliases: [], ...body, id: `p-${++db.seq}` } as MockPerson
    db.people.push(p)
    return HttpResponse.json({ ...p, shortName: shortName(p.fullName) }, { status: 201 })
  }, true),
  post('/admin/people/match', async ({ request }) => {
    const { displayName, personId, kind } = (await request.json()) as { displayName: string; personId: string | null; kind: PersonKind }
    const p = match(displayName, personId, kind)
    return p ? HttpResponse.json(p) : notFound('Человек не найден')
  }, true),
  get('/admin/people/:id', ({ params }) => {
    const p = resolvePerson(params.id as string)
    return p ? HttpResponse.json(personCard(p)) : notFound('Человек не найден')
  }, true),
  patch('/admin/people/:id', async ({ params, request }) => {
    const p = resolvePerson(params.id as string)
    if (!p) return notFound('Человек не найден')
    const body = (await request.json()) as Partial<Person>
    delete body.id
    if (body.fullName !== undefined && !body.fullName.trim()) return err(400, 'bad_name', 'Укажите ФИО')
    if (body.orcid && !/^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(body.orcid)) return err(400, 'bad_orcid', 'ORCID в формате 0000-0000-0000-0000')
    const badDept = unknownDepartment(body.department)
    if (badDept) return badDept
    Object.assign(p, body)
    for (const w of db.works) for (const a of allAuthorRefs(w)) if (a.personId === p.id) a.kind = p.kind
    return HttpResponse.json(personCard(p))
  }, true),
  post('/admin/people/:id/merge', async ({ params, request }) => {
    const main = resolvePerson(params.id as string)
    const { fromId } = (await request.json()) as { fromId: string }
    const from = resolvePerson(fromId)
    if (!main || !from) return notFound('Человек не найден')
    if (main.id === from.id) return err(400, 'same_person', 'Нельзя объединить профиль с самим собой')
    merge(main, from)
    return HttpResponse.json(personCard(main))
  }, true),
]
