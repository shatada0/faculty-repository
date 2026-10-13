// Deterministic mock database (section 8). Rebuilt on every page load.
import type {
  AdminWork, AuthorRef, FieldMeta, Import, ImportItem, Indexing, Person, PersonKind,
  Publication, PublicationKind, StudentWork, Talk, TalkEvent, TalkKind,
} from '@/api/types'
import { parseName, shortName, translit } from './names'
import {
  AWARDS, BOOKS, CLASSIFIERS, CONFERENCES, DEPT_SUBJECTS, DEPTS, EXTERNAL, FEMALE, JOURNALS, MALE, PATRONYMICS, STAFF, SURNAMES, SYSTEMS,
  type Conference, type Dept,
} from './data'

export interface MockPerson extends Person { mergedInto?: string }
export interface MockItem extends ImportItem {
  readyAt?: number
  result?: Partial<ImportItem> // revealed when the simulated AI finishes the item
  draft?: AdminWork
}
export interface MockImport extends Import { items: MockItem[] }
export interface Db { people: MockPerson[]; works: AdminWork[]; imports: MockImport[]; seq: number }

export const stats = (items: ImportItem[]) => ({
  total: items.length,
  processed: items.filter((x) => x.status !== 'pending' && x.status !== 'extracted').length,
  unmatched: items.filter((x) => x.unmatchedAuthors?.length).length,
  filled: items.filter((x) => x.status === 'filled').length,
  duplicates: items.filter((x) => x.status === 'duplicate').length,
  errors: items.filter((x) => x.status === 'error').length,
})

/** Generators reused by the import simulation. */
export let gen: {
  studentWork: (type: 'vkr' | 'coursework', d: number, year: number, n: number) => AdminWork
  newStudent: (d: number, kind: PersonKind) => MockPerson
}

// ---- random helpers (mulberry32) ----
let s = 20260601
export function rand() {
  s |= 0; s = (s + 0x6d2b79f5) | 0
  let t = Math.imul(s ^ (s >>> 15), 1 | s)
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296
}
const int = (a: number, b: number) => a + Math.floor(rand() * (b - a + 1))
const pick = <T,>(a: readonly T[]): T => a[Math.floor(rand() * a.length)]
const chance = (p: number) => rand() < p
const pad = (n: number, w = 2) => String(n).padStart(w, '0')
const cap = (x: string) => x.charAt(0).toUpperCase() + x.slice(1)
const conf = (lo = 0.45, hi = 0.98) => Math.round((lo + rand() * (hi - lo)) * 100) / 100

export const latinName = (fullName: string) => {
  const { surname, initials } = parseName(fullName)
  return [...initials.map((i) => translit(i) + '.'), translit(surname)].join(' ')
}

const GENERIC_KW = [
  ['машинное обучение', 'обработка естественного языка', 'анализ данных'],
  ['математическое моделирование', 'оптимизация', 'численные методы'],
  ['разработка программного обеспечения', 'качество ПО', 'архитектура ПО'],
]

export function buildDb(): Db {
  s = 20260601
  const db: Db = { people: [], works: [], imports: [], seq: 1000 }
  const people = db.people

  // ---- people ----
  const staff: MockPerson[] = STAFF.map(([fullName, d, position, academicDegree, academicTitle, orcid], i) => ({
    id: `p-s${i + 1}`, kind: 'staff', fullName, department: DEPTS[d].name, position, academicDegree, academicTitle, orcid,
    aliases: [shortName(fullName), latinName(fullName)],
  }))
  const staffDept = (d: number) => staff.filter((p) => p.department === DEPTS[d].name)
  const external: MockPerson[] = EXTERNAL.map(([fullName, affiliation], i) => ({
    id: `p-e${i + 1}`, kind: 'external', fullName, affiliation, aliases: [shortName(fullName), latinName(fullName)],
  }))
  people.push(...staff, ...external)

  const used = new Set(people.map((p) => p.fullName))
  let studentN = 0
  function newStudent(d: number, kind: PersonKind): MockPerson {
    let fullName = ''
    do {
      const female = chance(0.45)
      const sur = pick(SURNAMES)
      const pat = pick(PATRONYMICS)
      fullName = female
        ? `${sur}а ${pick(FEMALE)} ${pat.replace(/ич$/, 'на')}`
        : `${sur} ${pick(MALE)} ${pat}`
    } while (used.has(fullName))
    used.add(fullName)
    const p: MockPerson = { id: `p-st${++studentN}`, kind, fullName, department: DEPTS[d].name, aliases: [shortName(fullName)] }
    people.push(p)
    return p
  }

  const ref = (p: Person, en = false): AuthorRef => ({ personId: p.id, displayName: en ? latinName(p.fullName) : shortName(p.fullName), kind: p.kind })

  // ---- titles ----
  const titles = new Set<string>()
  type Topic = { m: Dept['methods'][0]; t: Dept['tasks'][0] }
  function unique<T extends { title: string }>(make: () => T): T {
    let r = make()
    for (let i = 0; i < 30 && titles.has(r.title); i++) r = make()
    titles.add(r.title)
    return r
  }
  const topic = (d: Dept): Topic => ({ m: pick(d.methods), t: pick(d.tasks) })
  const ruTitle = (d: Dept) => unique(() => {
    const { m, t } = topic(d)
    return {
      m, t, title: pick([
        `${m.nom} в задаче ${t.gen}`, `Применение ${m.gen} для ${t.gen}`, `Метод ${t.gen} на основе ${m.gen}`,
        `Сравнительный анализ ${m.gen} для ${t.gen}`, `Об эффективности ${m.gen} в задачах ${t.gen}`,
        `Модель ${t.gen} с использованием ${m.gen}`,
      ]),
    }
  })
  const enTitle = (d: Dept) => unique(() => {
    const { m, t } = topic(d)
    return { m, t, title: pick([`${cap(m.en)} for ${t.en}`, `Applying ${m.en} to ${t.en}`, `An empirical study of ${m.en} for ${t.en}`]) }
  })
  const studentTitle = (d: Dept) => unique(() => {
    const { m, t } = topic(d)
    const sys = pick(SYSTEMS)
    const title = pick([
      `Разработка ${sys} для ${t.gen}`, `Разработка ${sys} ${t.gen} с использованием ${m.gen}`,
      `Исследование ${m.gen} в задаче ${t.gen}`, `Применение ${m.gen} для ${t.gen}`,
    ])
    const annotation = title.startsWith('Разработка')
      ? `Работа посвящена разработке ${sys} для ${t.gen}. Выполнен анализ предметной области и существующих решений, спроектирована архитектура, реализован прототип с применением ${m.gen}. Проведено тестирование и оценка качества работы системы.`
      : `В работе исследуется применимость ${m.gen} для ${t.gen}. Разработан программный прототип, выполнено сравнение с альтернативными решениями по точности и времени работы.`
    return { m, t, title, annotation }
  })
  const annRu = (m: Topic['m'], t: Topic['t']) => pick([
    `Рассматривается задача ${t.gen}. Предложен подход на основе ${m.gen}. Проведены вычислительные эксперименты на данных факультета; результаты показывают преимущество предложенного подхода перед базовыми методами.`,
    `Статья посвящена вопросам ${t.gen}. Описаны существующие подходы и их ограничения, предложена модификация ${m.gen}. Приведены результаты экспериментальной проверки и рекомендации по практическому применению.`,
    `Исследуется применимость ${m.gen} для ${t.gen}. Разработан программный прототип, выполнено сравнение с альтернативными решениями по точности и времени работы.`,
  ])
  const annEn = (m: Topic['m'], t: Topic['t']) =>
    `The paper addresses ${t.en}. We propose an approach based on ${m.en} and evaluate it on real data. The results show that the approach outperforms the baselines.`
  const annTalk = (m: Topic['m'], t: Topic['t']) =>
    `В докладе представлены результаты исследования ${m.gen} для ${t.gen}. Обсуждаются ограничения подхода и направления дальнейшей работы.`
  const kw = (d: number, m: Topic['m'], t: Topic['t'], en = false) =>
    en ? [m.en, t.en] : [m.kw, t.kw, pick(GENERIC_KW[d])]

  const meta = (publishedAt: string, extra: Partial<AdminWork> = {}) => ({
    status: 'published' as const, fieldMeta: {}, createdAt: publishedAt, updatedAt: publishedAt, publishedAt, ...extra,
  })
  const cap2026 = (iso: string) => (iso > '2026-10-05' ? '2026-10-05' : iso)
  const addDays = (iso: string, n: number) => {
    const dt = new Date(iso + 'T12:00:00Z')
    dt.setUTCDate(dt.getUTCDate() + n)
    return dt.toISOString().slice(0, 10)
  }

  // ---- student works ----
  const pickSupervisor = (d: number) => pick(staffDept(d).filter((p) => !p.position?.startsWith('ассистент')))
  function studentWork(type: 'vkr' | 'coursework', d: number, year: number, n: number): AdminWork {
    const dept = DEPTS[d]
    const dirs = type === 'coursework' ? dept.directions.filter((x) => x.level === 'bachelor') : dept.directions
    const dir = dirs.length ? pick(dirs) : dept.directions[0]
    const sup = pickSupervisor(d)
    const sups = [ref(sup)]
    if (type === 'vkr' && chance(0.1)) {
      const other = staffDept(d).find((p) => p !== sup)
      if (other) sups.push(ref(other))
    }
    const author = newStudent(d, type === 'vkr' || year < 2023 ? 'graduate' : 'student')
    const { title, m, t, annotation } = studentTitle(dept)
    const consent = type === 'vkr' && chance(0.7)
    const publishedAt = `${year}-${type === 'vkr' ? '07' : pick(['06', '12'])}-${pad(int(3, 25))}`
    const w: StudentWork = {
      id: `${type === 'vkr' ? 'vkr' : 'cw'}-${n}`, type, title, authors: [ref(author)], year, department: dept.name,
      annotation, keywords: kw(d, m, t), language: 'ru',
      accessLevel: consent ? 'full' : 'card_only', hasFile: true,
      supervisors: sups, level: dir.level, direction: { code: dir.code, name: dir.name }, profile: dir.profile,
      discipline: type === 'coursework' ? pick(dept.disciplines) : undefined,
      pages: type === 'vkr' ? int(45, 110) : int(24, 45), consent,
    }
    return { ...w, ...meta(cap2026(publishedAt)), originalFileName: `${parseName(author.fullName).surname}_${type === 'vkr' ? 'ВКР' : 'КР'}_${year}.pdf` } as AdminWork
  }
  let vn = 0
  ;[6, 7, 7, 8, 8, 8, 8, 8].forEach((count, yi) => {
    for (let k = 0; k < count; k++) db.works.push(studentWork('vkr', vn % 3, 2019 + yi, ++vn))
  })
  let cn = 0
  ;[3, 3, 4, 4, 4, 4, 4, 4].forEach((count, yi) => {
    for (let k = 0; k < count; k++) db.works.push(studentWork('coursework', cn % 3, 2019 + yi, ++cn))
  })
  db.works.find((w) => w.id === 'vkr-14')!.accessLevel = 'hidden'
  db.works.find((w) => w.id === 'vkr-42')!.accessLevel = 'hidden'
  Object.assign(db.works.find((w) => w.id === 'cw-7')!, { status: 'rejected', rejectReason: 'Работа загружена повторно с ошибкой в теме' })

  // ---- coauthors ----
  const coauthorPool: MockPerson[] = []
  const coStudent = (d: number) => {
    const pool = coauthorPool.filter((p) => p.department === DEPTS[d].name)
    if (pool.length && (coauthorPool.length >= 30 || chance(0.4))) return pick(pool)
    const p = newStudent(d, 'student')
    coauthorPool.push(p)
    return p
  }
  function authorsFor(d: number, lead: Person, max = 3): Person[] {
    const list: Person[] = [lead]
    const n = int(0, max)
    for (let i = 0; i < n; i++) {
      const r = rand()
      const c = r < 0.4 ? pick(staffDept(d)) : r < 0.55 ? pick(staff) : r < 0.8 ? coStudent(d) : pick(external)
      if (!list.includes(c)) list.push(c)
    }
    return list
  }

  // ---- talks + linked publications ----
  const events = new Map<string, TalkEvent>()
  function eventFor(c: Conference, year: number): TalkEvent {
    const key = `${c.short}-${year}`
    if (!events.has(key)) {
      const start = `${year}-${pad(c.month)}-${pad(int(10, 24))}`
      const days = c.short.startsWith('Искусственный') ? 0 : int(1, 2)
      events.set(key, {
        name: c.name(c.base ? year - c.base + 1 : 1, year), level: c.level, city: c.city, country: c.country,
        dateStart: start, dateEnd: days ? addDays(start, days) : undefined, url: c.url,
      })
    }
    return events.get(key)!
  }
  let pn = 0
  const doiAbbr: Record<string, string> = {}
  const doiFor = (venue: string, year: number) => {
    const abbr = (doiAbbr[venue] ??= venue.split(/[\s.:]+/).filter((x) => x.length > 3).map((x) => translit(x[0]).toLowerCase()).join('').slice(0, 5))
    return `10.12345/${abbr}.${year}.${pad(int(1, 999), 4)}`
  }
  const talkKinds: TalkKind[] = ['section', 'section', 'section', 'section', 'plenary', 'invited', 'poster', 'poster']

  for (let i = 0; i < 45; i++) {
    const c = CONFERENCES[i % CONFERENCES.length]
    let year = int(2019, 2026)
    if (year === 2026 && c.month > 9) year = 2025
    const ev = eventFor(c, year)
    const d = pick(c.depts)
    const student = c.level === 'university'
    const lead = student ? coStudent(d) : pick(staffDept(d))
    const people_ = student ? [lead, pickSupervisor(d)] : authorsFor(d, lead, 2)
    const en = !!c.en
    const { title, m, t } = en ? enTitle(DEPTS[d]) : ruTitle(DEPTS[d])
    const presenters = [ref(lead, en)]
    if (!student && people_[1]?.kind === 'staff' && chance(0.2)) presenters.push(ref(people_[1], en))
    const fmt = year === 2020 || year === 2021 ? 'online' : chance(0.8) ? 'offline' : pick(['hybrid', 'online'] as const)
    const award = student ? (chance(0.5) ? pick(AWARDS) : undefined) : chance(0.05) ? AWARDS[3] : undefined
    const talk: Talk = {
      id: `talk-${i + 1}`, type: 'talk', title, authors: people_.map((p) => ref(p, en)), year, department: DEPTS[d].name,
      annotation: en ? annEn(m, t) : annTalk(m, t), keywords: kw(d, m, t, en), language: en ? 'en' : 'ru',
      accessLevel: 'full', hasFile: chance(0.6), event: ev, kind: student ? 'section' : pick(talkKinds), format: fmt,
      presenters, award,
    }
    db.works.push({ ...talk, ...meta(cap2026(addDays(ev.dateStart, int(10, 40)))) } as AdminWork)

    // first 22 talks get abstracts, next 6 get full papers in proceedings
    if (i < 28) {
      const abstract = i < 22
      const pub: Publication = {
        id: `pub-${++pn}`, type: 'publication', title, authors: talk.authors, year, department: talk.department,
        annotation: talk.annotation, keywords: talk.keywords, language: talk.language,
        accessLevel: abstract && chance(0.5) ? 'full' : 'card_only', hasFile: false,
        kind: abstract ? 'abstract' : 'proceedings_paper',
        venue: `${ev.name} : ${en ? 'proceedings' : abstract ? 'тезисы докладов' : 'сборник трудов'}`,
        pages: abstract ? (() => { const a = int(20, 300); return `${a}–${a + 1}` })() : (() => { const a = int(10, 400); return `${a}–${a + int(6, 11)}` })(),
        publisher: en ? 'Springer' : BOOKS.publisher, indexing: en ? ['scopus'] : ['rinc'],
        doi: en ? doiFor('DAKE', year) : undefined, isbn: `978-5-${int(1000, 9999)}-${int(1000, 9999)}-${int(0, 9)}`,
        relatedTalkId: talk.id,
      }
      pub.hasFile = pub.accessLevel === 'full'
      talk.relatedPublicationId = pub.id
      db.works[db.works.length - 1] = { ...db.works[db.works.length - 1], relatedPublicationId: pub.id } as AdminWork
      db.works.push({ ...pub, ...meta(cap2026(addDays(ev.dateEnd ?? ev.dateStart, int(30, 90)))) } as AdminWork)
    }
  }

  // ---- other publications ----
  function publication(kind: PublicationKind, d: number): AdminWork {
    const year = int(2019, 2026)
    const lead = pick(staffDept(d))
    const authors = authorsFor(d, lead, kind === 'journal_article' ? 3 : 2)
    let venue = ''
    let indexing: Indexing[] = ['rinc']
    let en = false
    let volume: string | undefined
    let issue: string | undefined
    let pages: string | undefined
    let issn: string | undefined
    let isbn: string | undefined
    let publisher: string | undefined
    let title: string
    let m: Topic['m']
    let t: Topic['t']
    if (kind === 'journal_article') {
      const j = pick(JOURNALS.filter((x) => x.depts.includes(d)))
      venue = j.name; indexing = j.indexing; en = !!j.en; issn = j.issn
      volume = chance(0.7) ? String(int(5, 30)) : undefined
      issue = String(int(1, 6))
      const a = int(10, 180); pages = `${a}–${a + int(8, 16)}`
      ;({ title, m, t } = en ? enTitle(DEPTS[d]) : ruTitle(DEPTS[d]))
    } else if (kind === 'proceedings_paper') {
      const c = pick(CONFERENCES.filter((x) => x.depts.includes(d) && !x.en && x.base))
      venue = `${eventFor(c, year).name} : сборник трудов`
      publisher = BOOKS.publisher
      const a = int(10, 300); pages = `${a}–${a + int(5, 9)}`
      ;({ title, m, t } = ruTitle(DEPTS[d]))
    } else if (kind === 'book_chapter') {
      venue = BOOKS.collective; publisher = BOOKS.publisher
      const a = int(10, 200); pages = `${a}–${a + int(15, 30)}`
      isbn = `978-5-${int(1000, 9999)}-${int(1000, 9999)}-${int(0, 9)}`
      ;({ title, m, t } = ruTitle(DEPTS[d]))
    } else {
      ;({ title, m, t } = ruTitle(DEPTS[d]))
      title = `Методические указания к лабораторным работам по дисциплине «${pick(DEPTS[d].disciplines)}»`
      titles.add(title)
      venue = 'Методические указания'; publisher = BOOKS.publisher; pages = String(int(32, 64))
    }
    const doi = (kind === 'journal_article' || kind === 'proceedings_paper') && chance(0.55) ? doiFor(venue, year) : undefined
    const open = chance(0.2)
    const p: Publication = {
      id: `pub-${++pn}`, type: 'publication', title, authors: authors.map((a) => ref(a, en)), year, department: DEPTS[d].name,
      annotation: en ? annEn(m, t) : annRu(m, t), keywords: kw(d, m, t, en), language: en ? 'en' : 'ru',
      accessLevel: open ? 'full' : 'card_only', hasFile: open, kind, venue, volume, issue, pages, publisher, doi, issn, isbn, indexing,
      externalUrl: !doi && kind === 'journal_article' && chance(0.6) ? `https://journal.example.ru/articles/${year}/${int(100, 999)}` : undefined,
    }
    return { ...p, ...meta(cap2026(`${year}-${pad(int(1, 12))}-${pad(int(1, 28))}`)) } as AdminWork
  }
  for (let i = 0; i < 40; i++) db.works.push(publication('journal_article', i % 3))
  for (let i = 0; i < 12; i++) db.works.push(publication('proceedings_paper', i % 3))
  for (let i = 0; i < 3; i++) db.works.push(publication('book_chapter', i % 3))
  for (let i = 0; i < 2; i++) db.works.push(publication('other', i))
  for (const [list, kind] of [[BOOKS.monographs, 'monograph'], [BOOKS.textbooks, 'textbook']] as const) {
    for (const b of list) {
      const year = int(2019, 2025)
      const lead = pick(staffDept(b.dept))
      const authors = [lead, ...staffDept(b.dept).filter((p) => p !== lead).slice(0, int(0, 2))]
      const { m, t } = topic(DEPTS[b.dept])
      db.works.push({
        id: `pub-${++pn}`, type: 'publication', title: b.title, authors: authors.map((a) => ref(a)), year, department: DEPTS[b.dept].name,
        annotation: kind === 'monograph'
          ? `В монографии систематизированы результаты исследований авторов в области ${t.gen}. Рассмотрены подходы на основе ${m.gen}, приведены примеры практического применения.`
          : `Учебное пособие содержит теоретический материал, примеры и задания для самостоятельной работы. Предназначено для студентов направлений кафедры.`,
        keywords: kw(b.dept, m, t), language: 'ru', accessLevel: kind === 'textbook' ? 'full' : 'card_only', hasFile: kind === 'textbook',
        kind, venue: '', pages: b.pages, publisher: BOOKS.publisher, isbn: `978-5-${int(1000, 9999)}-${int(1000, 9999)}-${int(0, 9)}`,
        indexing: ['rinc'], ...meta(`${year}-${pad(int(2, 11))}-${pad(int(1, 28))}`),
      } as AdminWork)
    }
  }
  db.works.find((w) => w.id === 'pub-60')!.accessLevel = 'hidden'

  // ---- imports and drafts ----
  const archiveMeta = (lowConf: boolean): Record<string, FieldMeta> => ({
    title: { source: 'table' }, authors: { source: 'table' }, supervisors: { source: 'table' }, year: { source: 'table' },
    level: { source: 'table' }, direction: { source: 'table' }, department: { source: 'table' }, profile: { source: 'table' },
    annotation: { source: 'ai', confidence: lowConf ? 0.48 : conf(0.72, 0.97) },
    keywords: { source: 'ai', confidence: lowConf ? 0.62 : conf(0.7, 0.95) },
    pages: { source: 'ai', confidence: 0.99 }, language: { source: 'ai', confidence: 0.99 },
  })
  const allAi = (): Record<string, FieldMeta> => Object.fromEntries(
    ['title', 'authors', 'supervisors', 'year', 'level', 'direction', 'department', 'discipline', 'annotation', 'keywords', 'pages', 'language']
      .map((k) => [k, { source: 'ai', confidence: conf(0.5, 0.97) }]),
  )
  const text = (displayName: string, kind: PersonKind = 'staff'): AuthorRef => ({ displayName, kind })
  const draftMeta = (importId: string, createdAt: string, fieldMeta: Record<string, FieldMeta>, extra: Partial<AdminWork> = {}) => ({
    status: 'draft' as const, importId, createdAt, updatedAt: createdAt, fieldMeta, publishedAt: undefined, ...extra,
  })

  // imp-0: last year's archive, already published
  const vkr2025 = db.works.filter((w) => w.type === 'vkr' && w.year === 2025)
  vkr2025.forEach((w) => { w.importId = 'imp-0' })
  const items0: MockItem[] = vkr2025.map((w) => ({ ref: w.originalFileName!, title: w.title, status: 'filled', workId: w.id }))
  db.imports.push({ id: 'imp-0', kind: 'archive', workType: 'vkr', fileName: 'ВКР_2025_все_кафедры.zip', metaTableName: 'сведения_ВКР_2025.xlsx', status: 'done', createdAt: '2025-07-01T09:30:00Z', createdBy: 'Модератор', items: items0, stats: stats(items0) })

  // imp-1: VKR archive 2026 with meta table
  const t1 = '2026-06-28T10:15:00Z'
  const items1: MockItem[] = []
  for (let k = 0; k < 8; k++) {
    if (k === 2) { items1.push({ ref: 'Скан_Тарасов_ВКР_2026.pdf', status: 'error', error: 'Скан без текстового слоя' }); continue }
    const w = studentWork('vkr', 0, 2026, ++vn)
    const supText = k === 0 ? 'Белова Н.' : k === 3 ? 'Кузнецов А.' : undefined
    if (supText) (w as StudentWork).supervisors = [text(supText)]
    Object.assign(w, draftMeta('imp-1', t1, archiveMeta(k === 4)))
    if (k === 6) { // a row of the meta table was empty: required fields are missing until the moderator fills them
      Object.assign(w, { direction: { code: '', name: '' }, department: '' })
      w.fieldMeta.direction = { source: 'ai', confidence: 0.31 }
    }
    const item: MockItem = { ref: w.originalFileName!, title: w.title, status: 'filled', workId: w.id, unmatchedAuthors: supText ? [supText] : undefined }
    if (k === 5) {
      const orig = db.works.find((x) => x.type === 'vkr' && x.status === 'published' && x.department === DEPTS[0].name && x.year === 2026)!
      Object.assign(w, { title: orig.title, annotation: orig.annotation, duplicateOf: orig.id })
      Object.assign(item, { status: 'duplicate', title: orig.title, duplicateOf: orig.id })
    }
    db.works.push(w)
    items1.push(item)
  }
  db.imports.push({ id: 'imp-1', kind: 'archive', workType: 'vkr', fileName: 'ВКР_2026_кафедра_ИС.zip', metaTableName: 'сведения_ВКР_2026_ИС.xlsx', status: 'done', createdAt: t1, createdBy: 'Модератор', items: items1, stats: stats(items1) })

  // imp-2: publications table
  const t2 = '2026-01-20T08:40:00Z'
  const tableMeta = (keys: string[]) => Object.fromEntries(keys.map((k) => [k, { source: 'table' as const }]))
  const pubKeys = ['title', 'authors', 'kind', 'venue', 'year', 'volume', 'issue', 'pages', 'doi', 'issn', 'indexing', 'department']
  const S = (i: number) => staff[i]
  const rows2: { authors: AuthorRef[]; d: number; kind: PublicationKind; error?: string; dup?: boolean; en?: boolean }[] = [
    { authors: [ref(S(5)), text('Иванов П. С.', 'external'), text('Миронов К.', 'external')], d: 1, kind: 'journal_article' },
    { authors: [text('N. V. Belova'), text('Zakharov I.'), ref(S(6), true)], d: 1, kind: 'journal_article', en: true },
    { authors: [ref(S(7))], d: 1, kind: 'journal_article', error: 'Не указан год' },
    { authors: [ref(S(7)), text('Шмидт К.', 'external')], d: 1, kind: 'proceedings_paper' },
    { authors: [], d: 1, kind: 'journal_article', dup: true },
    { authors: [ref(S(8)), text('Белова Н.')], d: 1, kind: 'journal_article' },
  ]
  const items2: MockItem[] = rows2.map((r, i) => {
    const rowRef = `Строка ${i + 2}`
    const w = publication(r.kind, r.d) as AdminWork & Publication
    if (r.en) {
      const j = JOURNALS.find((x) => x.en && x.depts.includes(1))!
      const { title, m, t } = enTitle(DEPTS[1])
      Object.assign(w, { title, venue: j.name, indexing: j.indexing, issn: j.issn, language: 'en', annotation: annEn(m, t), keywords: [m.en, t.en] })
    }
    if (r.error) return { ref: rowRef, title: w.title, status: 'error', error: r.error }
    Object.assign(w, draftMeta('imp-2', t2, tableMeta(pubKeys)), { annotation: undefined, keywords: [], year: 2025, hasFile: false, accessLevel: 'card_only' })
    if (r.dup) {
      const orig = db.works.find((x) => x.type === 'publication' && x.status === 'published' && x.kind === 'journal_article' && x.department === DEPTS[1].name)!
      Object.assign(w, { title: orig.title, authors: orig.authors, venue: (orig as Publication).venue, year: orig.year, duplicateOf: orig.id })
      db.works.push(w)
      return { ref: rowRef, title: orig.title, status: 'duplicate', workId: w.id, duplicateOf: orig.id }
    }
    w.authors = r.authors
    db.works.push(w)
    const unmatched = r.authors.filter((a) => !a.personId).map((a) => a.displayName)
    return { ref: rowRef, title: w.title, status: 'filled', workId: w.id, unmatchedAuthors: unmatched.length ? unmatched : undefined }
  })
  db.imports.push({ id: 'imp-2', kind: 'table', workType: 'publication', fileName: 'публикации_2025_кафедра_ПМ.xlsx', status: 'done', createdAt: t2, createdBy: 'Модератор', items: items2, stats: stats(items2) })

  // imp-3: coursework archive without a meta table — everything filled by AI
  const t3 = '2026-06-15T12:00:00Z'
  const items3: MockItem[] = []
  for (let k = 0; k < 4; k++) {
    if (k === 1) { items3.push({ ref: 'Курсовая_Жуков_ПИ.docx', status: 'error', error: 'Файл повреждён, текст не извлекается' }); continue }
    const w = studentWork('coursework', 2, 2026, ++cn)
    const fm = allAi()
    if (k === 2) { fm.title = { source: 'ai', confidence: 0.58 }; fm.discipline = { source: 'ai', confidence: 0.52 } }
    Object.assign(w, draftMeta('imp-3', t3, fm))
    const item: MockItem = { ref: w.originalFileName!, title: w.title, status: 'filled', workId: w.id }
    if (k === 0) {
      ;(w as StudentWork).supervisors = [text('Сидоренко Е. А.')]
      item.unmatchedAuthors = ['Сидоренко Е. А.']
    }
    if (k === 3) {
      const orig = db.works.find((x) => x.type === 'coursework' && x.status === 'published' && x.department === DEPTS[2].name)!
      Object.assign(w, { title: orig.title, duplicateOf: orig.id })
      Object.assign(item, { status: 'duplicate', title: orig.title, duplicateOf: orig.id })
    }
    db.works.push(w)
    items3.push(item)
  }
  db.imports.push({ id: 'imp-3', kind: 'archive', workType: 'coursework', fileName: 'Курсовые_2026_ПИ.zip', status: 'done', createdAt: t3, createdBy: 'Модератор', items: items3, stats: stats(items3) })

  // Rubrics by department, every third coursework a project, citations growing with age
  // (the real backend takes citations from Crossref / Scopus / РИНЦ).
  const rubric = (scheme: keyof typeof CLASSIFIERS, code: string) => CLASSIFIERS[scheme].find((x) => x.code === code)!
  db.works.forEach((w, i) => {
    const [grnti, oecd, wos, asjc] = DEPT_SUBJECTS[Math.max(0, DEPTS.findIndex((x) => x.name === w.department))]
    w.subjects = w.type === 'publication'
      ? [rubric('grnti', grnti), rubric('oecd', oecd), rubric('wos', wos), rubric('asjc', asjc)]
      : [rubric('grnti', grnti)]
    if (w.type === 'coursework' && i % 3 === 0) w.kind = 'project'
    if (w.type === 'publication') w.citations = ((i * 7) % 11) * Math.max(1, new Date().getFullYear() - w.year)
    if (w.type === 'talk' && i % 4 === 0) {
      w.materials = [{ id: `m-${i}`, name: 'Презентация.pdf', size: 2_400_000, kind: 'slides', status: 'approved', uploadedBy: 'Модератор', uploadedAt: w.createdAt }]
    }
  })
  // One author upload waiting for a moderator (Орлов Д. С. signs in as an author in the mocks).
  const orlov = db.works.find((w) => w.type === 'publication' && w.status === 'published' && w.authors.some((a) => a.personId === 'p-s1'))
  if (orlov) orlov.materials = [{ id: 'm-orlov', name: 'Авторская версия статьи.pdf', size: 1_150_000, kind: 'text', status: 'pending', uploadedBy: 'Орлов Д. С.', uploadedAt: '2026-10-01T10:00:00Z' }]

  gen = { studentWork, newStudent }
  return db
}

export const db: Db = buildDb()
