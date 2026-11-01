// Mock "backend" logic: search, facets, profiles, author matching, import simulation.
import type {
  AdminWork, AuthorRef, ConferenceLevel, Department, DoiConflict, Facet, FieldMeta, ImportPreview, Indexing, MyWork, Person, PersonCounts,
  PersonKind, PersonMetrics, PersonProfile, ProfileTab, Publication, PublicWork, StatTile, StudentWork, Subject, SubjectScheme, Talk,
  UploadLimits, UploadRule, Work, WorkAction, WorkStatus, WorkType,
} from '@/api/types'
import { ALL_FILTERS, TAB_FILTERS, type FilterKey } from '@/lib/catalog'
import { plural } from '@/lib/format'
import {
  confLevelLabels, courseworkKindLabels, fieldLabels, formatLabels, indexingLabels, levelLabels, personKindLabels, pubKindLabels, talkKindLabels,
} from '@/lib/labels'
import { nameKey, shortName } from './names'
import { fromLabel, missingInRow, readTable, toRecords, missingColumns } from './table'
import { CLASSIFIERS, JOURNAL_LISTS, JOURNALS, KNOWN_DOIS } from './data'
import { schemaFor } from './schemas'
import { workView } from './view'
import { db, gen, stats, type MockImport, type MockItem, type MockPerson } from './seed'

export function allAuthorRefs(w: Work): AuthorRef[] {
  const refs = [...w.authors]
  if (w.type === 'vkr' || w.type === 'coursework') refs.push(...w.supervisors)
  if (w.type === 'talk') refs.push(...w.presenters)
  return refs
}

// ---------- people ----------
export const personById = (id?: string) => db.people.find((p) => p.id === id)
export function resolvePerson(id?: string): MockPerson | undefined {
  let p = personById(id)
  while (p?.mergedInto) p = personById(p.mergedInto)
  return p
}
const activePeople = () => db.people.filter((p) => !p.mergedInto)
const compact = (s: string) => s.toLowerCase().replace(/[\s.,]/g, '').replace(/ё/g, 'е')

/** Exact match on full name or a known variant ("Иванов И.И." = "Иванов И. И."). */
export function matchExact(displayName: string): MockPerson | undefined {
  const key = compact(displayName)
  const found = activePeople().filter((p) => [p.fullName, ...(p.aliases ?? [])].some((n) => compact(n) === key))
  return found.length === 1 ? found[0] : undefined
}

export function suggestions(displayName: string): Person[] {
  const key = nameKey(displayName)
  return activePeople().filter((p) => [p.fullName, ...(p.aliases ?? [])].some((n) => nameKey(n) === key)).slice(0, 5)
}

export const refTo = (name: string, fallbackKind: PersonKind = 'external'): AuthorRef => {
  const p = matchExact(name)
  return p ? { personId: p.id, displayName: name, kind: p.kind } : { displayName: name, kind: fallbackKind }
}

const isStudent = (w: Work): w is StudentWork => w.type === 'vkr' || w.type === 'coursework'
const has = (refs: AuthorRef[], pid: string) => refs.some((a) => a.personId === pid)
export const asAuthor = (w: Work, pid: string) => has(w.authors, pid) || (w.type === 'talk' && has(w.presenters, pid))
export const asSupervisor = (w: Work, pid: string) => isStudent(w) && has(w.supervisors, pid)
export const isVisible = (w: AdminWork) => w.status === 'published' && w.accessLevel !== 'hidden'
const visibleWorks = () => db.works.filter(isVisible)

const journalOf = (w: Publication) => JOURNALS.find((j) => j.name === w.venue || (!!w.issn && j.issn === w.issn))
/** The journal's authority lists come from the journal directory, not from the work. */
const withJournal = <T extends Work>(w: T): T =>
  (w.type === 'publication' ? { ...w, journalLists: journalOf(w)?.lists.map((k) => JOURNAL_LISTS[k]) } : w)

export function toPublic(w: AdminWork): PublicWork {
  const {
    status, importId, originalFileName, fieldMeta, rejectReason, duplicateOf, createdAt, updatedAt, consent, materials, errors, issues, actions,
    ...rest
  } = w as AdminWork & { consent?: boolean }
  const work = withJournal(rest as Work)
  return { ...work, view: workView(work, (materials ?? []).filter((m) => m.status === 'approved')) }
}

function countsFor(pid: string, works = visibleWorks()): PersonCounts {
  const c: PersonCounts = { publication: 0, talk: 0, vkr: 0, coursework: 0 }
  for (const w of works) {
    if ((w.type === 'publication' || w.type === 'talk') && asAuthor(w, pid)) c[w.type]++
    if (isStudent(w) && asSupervisor(w, pid)) c[w.type]++
  }
  return c
}

export function departments(): { items: Department[] } {
  const staff = activePeople().filter((p) => p.kind === 'staff' && p.department).sort((a, b) => a.fullName.localeCompare(b.fullName, 'ru'))
  const works = visibleWorks()
  const m = new Map<string, MockPerson[]>()
  for (const p of staff) m.set(p.department!, [...(m.get(p.department!) ?? []), p])
  return {
    items: [...m].sort(([a], [b]) => a.localeCompare(b, 'ru')).map(([name, people]) => ({
      name,
      staffCount: people.length,
      workCount: works.filter((w) => w.department === name).length, // as the catalog's «Кафедра» facet
      people: people.slice(0, 3).map((p) => ({ id: p.id, shortName: shortName(p.fullName) })),
    })),
  }
}

export function staffList(sp: URLSearchParams) {
  const q = sp.get('q')?.toLowerCase().trim()
  const deps = sp.getAll('department')
  const works = visibleWorks()
  const items = activePeople()
    .filter((p) => p.kind === 'staff' && (!q || nameMatches(q, p.fullName)) && (!deps.length || deps.includes(p.department!)))
    .map((p) => ({ ...p, aliases: undefined, counts: countsFor(p.id, works) }))
  if (sp.get('sort') === 'works') items.sort((a, b) => b.counts.publication + b.counts.talk - (a.counts.publication + a.counts.talk))
  else items.sort((a, b) => a.fullName.localeCompare(b.fullName, 'ru'))
  return { items }
}

const FORMS: Record<WorkType, [string, string, string]> = {
  vkr: ['ВКР', 'ВКР', 'ВКР'], coursework: ['курсовая', 'курсовые', 'курсовых'],
  publication: ['публикация', 'публикации', 'публикаций'], talk: ['выступление', 'выступления', 'выступлений'],
}
const options = <K extends string>(labels: Record<K, string>) => (Object.keys(labels) as K[]).map((value) => ({ value, label: labels[value] }))
const breakdown = <K extends string>(rec: Record<K, number>, labels: Record<K, string>) =>
  (Object.keys(rec) as K[]).filter((k) => rec[k] > 0).map((k) => `${labels[k]} ${rec[k]}`).join(', ')

/** Public page of a teacher, student or graduate: tiles, tabs with their filters, publications and talks by year, coauthors. */
export function profile(p: MockPerson): PersonProfile {
  const works = visibleWorks()
  const staff = p.kind === 'staff'
  const of = (type: WorkType, role: 'author' | 'supervisor') =>
    works.filter((w) => w.type === type && (role === 'supervisor' ? asSupervisor(w, p.id) : asAuthor(w, p.id)))
  const tab = (key: string, label: string, type: WorkType, role: 'author' | 'supervisor', filters: ProfileTab['filters'] = []): ProfileTab => {
    const list = of(type, role)
    const years = [...new Set(list.map((w) => w.year))].sort((a, b) => b - a).map((y) => ({ value: String(y), label: String(y) }))
    return { key, label, count: list.length, query: { type, role }, filters: [{ key: 'year', label: 'Год', all: 'все', options: years }, ...filters] }
  }
  const pubFilters = [
    { key: 'pubKind', label: 'Вид', all: 'все', options: options(pubKindLabels) },
    { key: 'indexing', label: 'Индексация', all: 'любая', options: options(indexingLabels) },
  ]
  const talkFilters = [{ key: 'talkKind', label: 'Вид доклада', all: 'все', options: options(talkKindLabels) }]
  const tabs = staff
    ? [
      tab('publications', 'Публикации', 'publication', 'author', pubFilters), tab('talks', 'Выступления', 'talk', 'author', talkFilters),
      tab('vkr', 'Руководство ВКР', 'vkr', 'supervisor'), tab('coursework', 'Руководство курсовыми', 'coursework', 'supervisor'),
    ]
    : [
      tab('vkr', 'ВКР', 'vkr', 'author'), tab('coursework', 'Курсовые', 'coursework', 'author'),
      tab('publications', 'Публикации', 'publication', 'author', pubFilters), tab('talks', 'Выступления', 'talk', 'author', talkFilters),
    ].filter((t) => t.count > 0)
  const mine = works.filter((w) => (w.type === 'publication' || w.type === 'talk') && asAuthor(w, p.id))
  const byIndexing = { scopus: 0, wos: 0, vak: 0, rinc: 0, core_rinc: 0 } as Record<Indexing, number>
  const byConfLevel = { international: 0, national: 0, regional: 0, university: 0 } as Record<ConferenceLevel, number>
  const years = new Map<number, { publication: number; talk: number }>()
  const co = new Map<string, number>()
  for (const w of mine) {
    if (w.type === 'publication') w.indexing.forEach((i) => byIndexing[i]++)
    if (w.type === 'talk') byConfLevel[w.event.level]++
    const y = years.get(w.year) ?? { publication: 0, talk: 0 }
    y[w.type as 'publication' | 'talk']++
    years.set(w.year, y)
    for (const id of new Set(w.authors.map((a) => a.personId))) {
      const k = personById(id)?.kind
      if (id && id !== p.id && k && k !== 'external') co.set(id, (co.get(id) ?? 0) + 1)
    }
  }
  const ys = [...years.keys()]
  const byYear = []
  if (ys.length) for (let y = Math.min(...ys); y <= Math.max(...ys); y++) byYear.push({ year: y, ...(years.get(y) ?? { publication: 0, talk: 0 }) })
  const coauthors = [...co].sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([id, count]) => ({ id, fullName: personById(id)!.fullName, count }))
  const c = countsFor(p.id, works)
  const tiles: StatTile[] = staff
    ? [
      { value: c.publication, label: plural(c.publication, FORMS.publication), note: breakdown(byIndexing, indexingLabels) },
      { value: c.talk, label: plural(c.talk, FORMS.talk), note: breakdown(byConfLevel, confLevelLabels) },
      { value: c.vkr, label: 'ВКР под руководством' },
      { value: c.coursework, label: `${plural(c.coursework, FORMS.coursework)} под руководством` },
    ]
    : tabs.map((t) => ({ value: t.count, label: plural(t.count, FORMS[t.query.type as WorkType]) }))
  const headline = staff ? p.position : [personKindLabels[p.kind], p.department].filter(Boolean).join(' · ')
  return { ...p, aliases: undefined, headline, tiles, tabs, byYear, coauthors }
}

/** h-index and citations over the person's publications: all of them, or only the works of `year`. */
export function metrics(pid: string, sp: URLSearchParams): PersonMetrics {
  const pubs = visibleWorks().filter((w) => w.type === 'publication' && asAuthor(w, pid)) as (Publication & AdminWork)[]
  if (!pubs.length) return { items: [], years: [] }
  const year = sp.get('year')
  const chosen = year ? pubs.filter((w) => String(w.year) === year) : pubs
  const cites = chosen.map((w) => w.citations ?? 0).sort((a, b) => b - a)
  const h = cites.filter((n, i) => n >= i + 1).length
  const total = cites.reduce((s, n) => s + n, 0)
  return {
    items: [
      { value: h, label: 'индекс Хирша', note: year ? `по работам ${year} года` : 'по всем годам' },
      { value: total, label: plural(total, ['цитирование', 'цитирования', 'цитирований']), note: `${chosen.length} ${plural(chosen.length, FORMS.publication)}` },
    ],
    years: [...new Set(pubs.map((w) => w.year))].sort((a, b) => b - a),
  }
}

export function personWorks(pid: string, sp: URLSearchParams) {
  const type = sp.get('type') as WorkType | null
  const role = sp.get('role') ?? 'author'
  let items = visibleWorks().filter((w) => (!type || w.type === type) && (role === 'supervisor' ? asSupervisor(w, pid) : asAuthor(w, pid)))
  items = filterBy(items, sp, ['year', 'pubKind', 'indexing', 'talkKind', 'confLevel'])
  items.sort((a, b) => b.year - a.year || a.title.localeCompare(b.title, 'ru'))
  return { items: items.map(toPublic), total: items.length }
}

// ---------- catalog ----------
const staffIds = (refs: AuthorRef[]) =>
  refs.filter((a) => a.personId && personById(a.personId)?.kind === 'staff').map((a) => a.personId!)

const codes = (w: Work, scheme: SubjectScheme) => (w.subjects ?? []).filter((x) => x.scheme === scheme).map((x) => x.code)

const values: Record<FilterKey, (w: Work) => string[]> = {
  year: (w) => [String(w.year)],
  department: (w) => (w.department ? [w.department] : []),
  author: (w) => staffIds(w.type === 'talk' ? [...w.authors, ...w.presenters] : w.authors),
  supervisor: (w) => (isStudent(w) ? staffIds(w.supervisors) : []),
  level: (w) => (isStudent(w) ? [w.level] : []),
  direction: (w) => (isStudent(w) ? [w.direction.code] : []),
  discipline: (w) => (w.type === 'coursework' && w.discipline ? [w.discipline] : []),
  pubKind: (w) => (w.type === 'publication' ? [w.kind] : []),
  indexing: (w) => (w.type === 'publication' ? w.indexing : []),
  venue: (w) => (w.type === 'publication' && w.venue ? [w.venue] : []),
  confLevel: (w) => (w.type === 'talk' ? [w.event.level] : []),
  talkKind: (w) => (w.type === 'talk' ? [w.kind] : []),
  format: (w) => (w.type === 'talk' ? [w.format] : []),
  courseKind: (w) => (w.type === 'coursework' ? [w.kind ?? 'work'] : []),
  journalList: (w) => (w.type === 'publication' ? journalOf(w)?.lists ?? [] : []),
  grnti: (w) => codes(w, 'grnti'),
  oecd: (w) => codes(w, 'oecd'),
  wos: (w) => codes(w, 'wos'),
  asjc: (w) => codes(w, 'asjc'),
}

function label(key: FilterKey, v: string): string {
  switch (key) {
    case 'courseKind': return courseworkKindLabels[v as keyof typeof courseworkKindLabels]
    case 'journalList': return JOURNAL_LISTS[v] ?? v
    case 'grnti': case 'oecd': case 'wos': case 'asjc': return `${v} ${CLASSIFIERS[key].find((x) => x.code === v)?.name ?? ''}`
    case 'author': case 'supervisor': return shortName(personById(v)?.fullName ?? v)
    case 'level': return levelLabels[v as keyof typeof levelLabels]
    case 'pubKind': return pubKindLabels[v as keyof typeof pubKindLabels]
    case 'indexing': return indexingLabels[v as Indexing]
    case 'confLevel': return confLevelLabels[v as ConferenceLevel]
    case 'talkKind': return talkKindLabels[v as keyof typeof talkKindLabels]
    case 'format': return formatLabels[v as keyof typeof formatLabels]
    case 'direction': {
      const w = db.works.find((x) => isStudent(x) && x.direction.code === v) as StudentWork | undefined
      return w ? `${v} ${w.direction.name}` : v
    }
    default: return v
  }
}

function filterBy<T extends Work>(works: T[], sp: URLSearchParams, keys: readonly FilterKey[], skip?: FilterKey): T[] {
  return works.filter((w) => keys.every((k) => {
    const sel = sp.getAll(k)
    if (k === skip || !sel.length) return true
    const vs = values[k](w)
    return sel.some((v) => vs.includes(v))
  }))
}

// ponytail: crude prefix stemming for Russian endings; the real backend does proper full-text search
const stem = (t: string) => (t.length >= 4 ? t.slice(0, Math.max(3, t.length - 2)) : t)
const norm = (s: string) => s.toLowerCase().replace(/ё/g, 'е')
const wordsOf = (s: string) => norm(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean)
const FEW = 5 // fewer exact matches than this — similar ones (typos) are added, as on the backend

function edits(a: string, b: string): number {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    const cur = [i]
    for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    prev = cur
  }
  return prev[b.length]
}
/** The word starts with the query word, or (typos) its start is within one edit of it — two for long words. */
export function wordMatch(tok: string, word: string, fuzzy = false): boolean {
  if (word.startsWith(tok)) return true
  if (!fuzzy || tok.length < 4) return false
  return edits(tok, word.slice(0, tok.length)) <= (tok.length >= 7 ? 2 : 1)
}

function haystack(w: Work): [string, number][] {
  const names = allAuthorRefs(w).map((a) => `${a.displayName} ${personById(a.personId)?.fullName ?? ''}`).join(' ')
  const venue = w.type === 'publication' ? w.venue : w.type === 'talk' ? w.event.name : `${w.direction.name} ${w.discipline ?? ''}`
  return [[w.title, 3], [w.keywords.join(' '), 2], [names, 2], [w.annotation ?? '', 1], [venue, 1]]
}

/** Every query word must start a word of the work; with `fuzzy`, typo matches count too but weigh 10× less. */
export function score(w: Work, q: string, fuzzy = false): number {
  const tokens = wordsOf(q).map(stem)
  const fields = haystack(w).map(([t, wt]) => [wordsOf(t), wt] as const)
  let total = 0
  for (const tok of tokens) {
    const best = Math.max(0, ...fields.filter(([ws]) => ws.some((x) => wordMatch(tok, x))).map(([, wt]) => wt))
    const near = best ? 0 : Math.max(0, ...fields.filter(([ws]) => fuzzy && ws.some((x) => wordMatch(tok, x, true))).map(([, wt]) => wt / 10))
    if (!best && !near) return 0
    total += best || near
  }
  return total || 1
}

/** Shared by the catalog, facets, suggestions and the admin list: exact first, similar only when exact are few. */
function matching<T extends Work>(works: T[], q: string) {
  const scored = (fuzzy: boolean) => works.map((w) => ({ w, s: score(w, q, fuzzy) })).filter((x) => x.s > 0)
  const exact = scored(false)
  return exact.length >= FEW ? exact : scored(true)
}

function searched(sp: URLSearchParams) {
  const q = sp.get('q')?.trim()
  return q ? matching(visibleWorks(), q) : visibleWorks().map((w) => ({ w, s: 1 }))
}

/** The title split into parts, matched words marked — what GET /works/suggest returns in `parts`. */
export function highlight(title: string, q: string) {
  const tokens = wordsOf(q).map(stem)
  const parts: { text: string; hit: boolean }[] = []
  for (const piece of title.split(/([\p{L}\p{N}]+)/u).filter(Boolean)) {
    const hit = /[\p{L}\p{N}]/u.test(piece) && tokens.some((t) => wordMatch(t, norm(piece), true))
    const last = parts[parts.length - 1]
    if (last && !last.hit && !hit) last.text += piece
    else parts.push({ text: piece, hit })
  }
  return parts
}

export function suggest(q: string) {
  q = q.trim()
  if (!q) return { works: [], people: [] }
  const works = matching(visibleWorks(), q)
    .sort((a, b) => b.s - a.s || b.w.year - a.w.year)
    .slice(0, 6)
    .map(({ w }) => ({ id: w.id, type: w.type, title: w.title, year: w.year, parts: highlight(w.title, q) }))
  const people = activePeople()
    .filter((p) => p.kind === 'staff' && [p.fullName, ...(p.aliases ?? [])].some((n) => nameMatches(q, n)))
    .slice(0, 4)
    .map((p) => ({ id: p.id, fullName: p.fullName }))
  return { works, people }
}

export function catalog(sp: URLSearchParams) {
  const type = sp.get('type')
  let list = searched(sp).filter((x) => (!type || x.w.type === type) && x.w.id !== sp.get('exclude'))
  const keep = new Set(filterBy(list.map((x) => x.w), sp, ALL_FILTERS))
  list = list.filter((x) => keep.has(x.w))
  const sort = sp.get('sort') ?? (sp.get('q') ? 'relevance' : 'new')
  const byNew = (a: AdminWork, b: AdminWork) => b.year - a.year || (b.publishedAt ?? '').localeCompare(a.publishedAt ?? '')
  if (sort === 'relevance') list.sort((a, b) => b.s - a.s || byNew(a.w, b.w))
  else if (sort === 'title') list.sort((a, b) => a.w.title.localeCompare(b.w.title, 'ru'))
  else list.sort((a, b) => byNew(a.w, b.w))
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const pageSize = Math.min(100, Number(sp.get('pageSize')) || 20)
  const q = sp.get('q')?.trim()
  const items = list.slice((page - 1) * pageSize, page * pageSize).map((x) => (q ? { ...toPublic(x.w), titleParts: highlight(x.w.title, q) } : toPublic(x.w)))
  return { items, total: list.length, page, pageSize }
}

/** Home "new arrivals": the newest work of every type first, then the rest by recency — so the list shows different types. */
export function latest(n = 6): Work[] {
  const added = visibleWorks().sort((a, b) => (b.publishedAt ?? '').localeCompare(a.publishedAt ?? ''))
  const out = (['vkr', 'coursework', 'publication', 'talk'] as const).map((t) => added.find((w) => w.type === t)).filter((w) => !!w)
  for (const w of added) if (out.length < n && !out.includes(w)) out.push(w)
  return out.slice(0, n).sort((a, b) => (b.publishedAt ?? '').localeCompare(a.publishedAt ?? '')).map(toPublic)
}

function count(works: Work[], key: FilterKey, selected: string[]): Facet[] {
  const m = new Map<string, number>()
  for (const w of works) for (const v of new Set(values[key](w))) m.set(v, (m.get(v) ?? 0) + 1)
  for (const v of selected) if (!m.has(v)) m.set(v, 0)
  const out = [...m].map(([value, c]) => ({ value, label: label(key, value), count: c }))
  return key === 'year' ? out.sort((a, b) => Number(b.value) - Number(a.value)) : out.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'ru'))
}

export function facets(sp: URLSearchParams) {
  const type = (sp.get('type') ?? 'all') as keyof typeof TAB_FILTERS
  const base = searched(sp).map((x) => x.w)
  const common = filterBy(base, sp, TAB_FILTERS.all)
  const byType = { vkr: 0, coursework: 0, publication: 0, talk: 0 } as Record<WorkType, number>
  common.forEach((w) => byType[w.type]++)
  const out: Record<string, Facet[]> = {
    type: (Object.keys(byType) as WorkType[]).map((t) => ({ value: t, label: t, count: byType[t] })),
  }
  const ofType = base.filter((w) => type === 'all' || w.type === type)
  for (const k of TAB_FILTERS[type] ?? TAB_FILTERS.all) out[k] = count(filterBy(ofType, sp, TAB_FILTERS[type], k), k, sp.getAll(k))
  return out
}

// ---------- admin ----------
export const LOW_CONFIDENCE = 0.7
const unsure = (m: FieldMeta) => m.source === 'ai' && (m.confidence ?? 1) < LOW_CONFIDENCE
const unfilled = (v: unknown): boolean => v == null || v === '' || (Array.isArray(v) && !v.length) ||
  (typeof v === 'object' && !Array.isArray(v) && Object.values(v as object).every(unfilled))

/** Admin view of a work: what blocks publishing, what a moderator should look at, the import's file name. */
/** A new work with the defaults the editor starts from. */
export function template(type: WorkType): Record<string, unknown> {
  const base = {
    type, title: '', authors: [{ displayName: '', kind: type === 'vkr' || type === 'coursework' ? 'student' : 'staff' }],
    year: new Date().getFullYear(), department: '', annotation: '', keywords: [], language: 'ru', hasFile: false, externalUrl: '', subjects: [],
  }
  if (type === 'vkr' || type === 'coursework') return {
    ...base, accessLevel: 'card_only', supervisors: [{ displayName: '', kind: 'staff' }], level: 'bachelor',
    direction: { code: '', name: '' }, profile: '', discipline: '', pages: undefined, consent: false, ...(type === 'coursework' ? { kind: 'work' } : {}),
  }
  if (type === 'publication') return {
    ...base, accessLevel: 'card_only', kind: 'journal_article', venue: '', volume: '', issue: '', pages: '', publisher: '',
    doi: '', isbn: '', issn: '', indexing: [], relatedTalkId: '',
  }
  return {
    ...base, accessLevel: 'full', event: { name: '', level: 'national', city: '', country: 'Россия', dateStart: '', dateEnd: '', url: '' },
    kind: 'section', format: 'offline', presenters: [], award: '', relatedPublicationId: '',
  }
}

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** Every field present, null when empty — how pydantic serializes, and what the editor's form expects. */
function complete(w: AdminWork): AdminWork {
  const out: Record<string, unknown> = { ...w }
  for (const [k, v] of Object.entries(template(w.type))) {
    const cur = out[k]
    if (isObject(v)) out[k] = Object.fromEntries(Object.keys(v).map((s) => [s, (isObject(cur) ? cur[s] : undefined) ?? null]))
    else out[k] ??= Array.isArray(v) ? [] : null
  }
  return out as unknown as AdminWork
}

const ACTIONS: Record<WorkStatus, WorkAction[]> = { draft: ['publish', 'reject'], published: ['unpublish', 'reject'], rejected: ['publish'] }
export const canDo = (w: AdminWork, a: WorkAction) => ACTIONS[w.status].includes(a)

export function withChecks(w: AdminWork): AdminWork {
  const r = schemaFor(w.type).safeParse(w)
  const errors = r.success ? [] : r.error.issues.map((i) => ({ field: i.path.join('.'), message: i.message }))
  // a source only for a filled field: an unknown department is dropped on import, «ИИ, 85%» must not stay
  const fieldMeta = Object.fromEntries(Object.entries(w.fieldMeta ?? {})
    .filter(([k]) => !unfilled((w as unknown as Record<string, unknown>)[k]))
    .map(([k, m]) => [k, { ...m, lowConfidence: unsure(m) || undefined }]))
  const unmatchedNames = [...new Set(allAuthorRefs(w).filter((a) => !a.personId).map((a) => a.displayName))]
  const issues = [
    ...errors.map((e) => e.message),
    ...Object.keys(fieldMeta).filter((k) => fieldMeta[k].lowConfidence).map((k) => `ИИ не уверен: ${fieldLabels[k] ?? k}`),
    ...unmatchedNames.map((n) => `Не распознан автор: ${n}`),
  ]
  return complete(withJournal({
    ...w, fieldMeta, errors, issues, actions: ACTIONS[w.status], materials: w.materials ?? [],
    importFileName: db.imports.find((i) => i.id === w.importId)?.fileName,
  }))
}

const READ_ONLY = new Set([
  'id', 'type', 'status', 'createdAt', 'updatedAt', 'importId', 'importFileName', 'publishedAt', 'duplicateOf', 'rejectReason',
  'originalFileName', 'hasFile', 'fieldMeta', 'errors', 'issues', 'actions', 'materials', 'citations', 'journalLists',
])
const blank = (v: unknown) => v == null || v === ''
const same = (a: unknown, b: unknown) => (blank(a) && blank(b)) || JSON.stringify(a) === JSON.stringify(b)

/** Apply an editor save. Fields that differ from `before` (the stored work, or the template for a new one) become "manual". */
export function applyEdit(w: AdminWork, body: Record<string, unknown>, before: Record<string, unknown>) {
  const rec = w as unknown as Record<string, unknown>
  const mark = (key: string, was: unknown, now: unknown) => { if (!same(was, now)) w.fieldMeta[key] = { source: 'manual' } }
  for (const [k, v] of Object.entries(body)) {
    if (READ_ONLY.has(k)) continue
    if (k === 'event' && isObject(v)) for (const [s, sv] of Object.entries(v)) mark(`event.${s}`, isObject(before.event) ? before.event[s] : undefined, sv)
    else mark(k, before[k], v)
    rec[k] = v
  }
}

export class DoiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

const DOI_FIELDS = ['title', 'authors', 'venue', 'year', 'volume', 'issue', 'pages', 'issn', 'kind', 'language', 'indexing']
const shown = (v: unknown) => (Array.isArray(v) ? v.map((x) => (isObject(x) ? x.displayName : x)).join(', ') : blank(v) ? '—' : String(v))
// An empty author row ({ displayName: '' }) counts as empty too.
const isEmpty = (v: unknown) => blank(v) || (Array.isArray(v) && v.every((a) => isObject(a) && !a.displayName))

/**
 * Fill a publication from DOI. A field counts as filled if it has a value with a source (import, DOI, a moderator's edit);
 * untouched defaults are replaced. Filled fields that differ come back as conflicts unless listed in `overwrite`.
 */
export function fillFromDoi(w: AdminWork, raw: string, overwrite: string[]): DoiConflict[] {
  const doi = raw.trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, '')
  if (!/^10\.\d{4,9}\/\S+$/.test(doi)) throw new DoiError(400, 'Введите DOI в формате 10.xxxx/...')
  const d = KNOWN_DOIS[doi]
  if (!d) throw new DoiError(404, 'DOI не найден')
  return fillFields(w, 'doi', doi, { ...d, authors: d.authors.map((n) => refTo(n)) }, DOI_FIELDS, overwrite)
}

// Offline there is no Crossref or Open Library: one demo book answers, like the demo DOIs.
const KNOWN_ISBNS: Record<string, Record<string, unknown>> = {
  '9785020138506': { title: 'Базы данных: учебное пособие', authors: ['Орлов Д. С.'], publisher: 'Издательство университета', year: 2021, pages: '212', kind: 'textbook', language: 'ru' },
}

export function fillFromIsbn(w: AdminWork, raw: string, overwrite: string[]): DoiConflict[] {
  const isbn = raw.toUpperCase().replace(/[^0-9X]/g, '')
  if (isbn.length !== 10 && isbn.length !== 13) throw new DoiError(400, 'ISBN с ошибкой: нужно 10 или 13 цифр с верным контрольным знаком')
  const d = KNOWN_ISBNS[isbn]
  if (!d) throw new DoiError(404, 'ISBN не найден ни в Crossref, ни в Open Library. Для демо: 978-5-02-013850-6')
  return fillFields(w, 'isbn', isbn, { ...d, authors: (d.authors as string[]).map((n) => refTo(n)) }, ['title', 'authors', 'year', 'pages', 'publisher', 'kind', 'language'], overwrite)
}

/** DOIs pasted as text → drafts filled from the demo DOIs; known DOIs are skipped. */
export function importDoiList(text: string, user: string) {
  const dois = [...new Set((text.toLowerCase().match(/10\.\d{4,9}\/[^\s,;]+/g) ?? []).map((d) => d.replace(/[.,;)]+$/, '')))]
  if (!dois.length) throw new DoiError(400, 'В тексте нет ни одного DOI (10.xxxx/...)')
  const known = dois.filter((d) => db.works.some((w) => w.type === 'publication' && w.status !== 'rejected' && w.doi?.toLowerCase() === d))
  const fresh = dois.filter((d) => !known.includes(d) && KNOWN_DOIS[d])
  for (const doi of fresh) {
    const w = { type: 'publication', keywords: [], language: 'ru', hasFile: false, fieldMeta: {}, id: `pub-${++db.seq}`, status: 'draft', createdAt: now(), updatedAt: now(), createdBy: user } as unknown as AdminWork
    applyEdit(w, template('publication'), template('publication'))
    fillFromDoi(w, doi, [])
    db.works.push(w)
  }
  const notFound = dois.filter((d) => !known.includes(d) && !KNOWN_DOIS[d])
  const parts = [`Новых черновиков: ${fresh.length}`, `уже в базе: ${known.length}`, ...(notFound.length ? [`нет в Crossref: ${notFound.length}`] : [])]
  return { found: dois.length, created: fresh.length, known: known.length, notFound, failed: [], message: `DOI в списке: ${dois.length}. ${parts.join(', ')}.` }
}

function fillFields(w: AdminWork, by: 'doi' | 'isbn', value: string, pub: Record<string, unknown>, fields: string[], overwrite: string[]): DoiConflict[] {
  const rec = w as unknown as Record<string, unknown>
  const conflicts: DoiConflict[] = []
  rec[by] = value
  for (const k of fields) {
    if (pub[k] === undefined) continue
    if (!isEmpty(rec[k]) && w.fieldMeta[k]) {
      if (same(rec[k], pub[k])) continue
      if (!overwrite.includes(k)) {
        conflicts.push({ field: k, label: fieldLabels[k] ?? k, current: shown(rec[k]), incoming: shown(pub[k]) })
        continue
      }
    }
    rec[k] = pub[k]
    w.fieldMeta[k] = { source: by }
  }
  w.updatedAt = new Date().toISOString()
  return conflicts
}

const MB = 1024 * 1024
export const UPLOAD_LIMITS: UploadLimits = {
  archive: { accept: ['.zip', '.pdf'], maxBytes: 500 * MB },
  table: { accept: ['.xlsx', '.csv'], maxBytes: 10 * MB }, // the server also takes .bib and .ris; the mocks don't parse them
  workFile: { accept: ['.pdf', '.pptx', '.docx'], maxBytes: 100 * MB },
  material: { accept: ['.pdf', '.pptx', '.docx', '.zip'], maxBytes: 50 * MB },
}
export const fits = (f: File, r: UploadRule) => r.accept.includes(f.name.slice(f.name.lastIndexOf('.')).toLowerCase()) && f.size <= r.maxBytes

export function searchClassifier(scheme: SubjectScheme, q: string): Subject[] {
  const t = q.trim().toLowerCase()
  return CLASSIFIERS[scheme].filter((x) => !t || x.code.toLowerCase().startsWith(t) || x.name.toLowerCase().includes(t)).slice(0, 20)
}

/** Works of an author, for "Мои работы"; materials can be added to publications and talks. */
export function myWorks(pid: string): { items: MyWork[] } {
  const items = db.works
    .filter((w) => w.status !== 'rejected' && asAuthor(w, pid))
    .sort((a, b) => b.year - a.year || a.title.localeCompare(b.title, 'ru'))
    .map((w) => ({
      id: w.id, title: w.title, kind: workView(w).kind, year: w.year, public: isVisible(w),
      canUpload: w.type === 'publication' || w.type === 'talk', materials: w.materials ?? [],
    }))
  return { items }
}

export function summary() {
  const drafts = db.works.filter((w) => w.status === 'draft')
  const byType = { vkr: 0, coursework: 0, publication: 0, talk: 0 } as Record<WorkType, number>
  drafts.forEach((w) => byType[w.type]++)
  return {
    drafts: { total: drafts.length, withIssues: drafts.filter((w) => withChecks(w).issues.length).length, byType },
    pendingMaterials: db.works.flatMap((w) => (w.materials ?? []).filter((m) => m.status === 'pending').map((m) => ({ workId: w.id, title: w.title, name: m.name }))),
  }
}

export function adminWorks(sp: URLSearchParams) {
  const q = sp.get('q')?.trim()
  const found = new Set(q ? matching(db.works, q).map((x) => x.w) : [])
  let items = db.works.filter((w) =>
    (!sp.get('type') || w.type === sp.get('type')) &&
    (!sp.get('status') || w.status === sp.get('status')) &&
    (!sp.get('importId') || w.importId === sp.get('importId')) &&
    (!sp.get('year') || String(w.year) === sp.get('year')) &&
    (!q || found.has(w) || (w.originalFileName ?? '').toLowerCase().includes(q.toLowerCase())))
  const checked = items.map(withChecks)
  if (sp.get('sort') === 'problems') {
    checked.sort((a, b) => b.issues.length - a.issues.length || a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id))
  } else checked.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const pageSize = Math.min(500, Number(sp.get('pageSize')) || 20)
  return { items: checked.slice((page - 1) * pageSize, page * pageSize), total: checked.length }
}

/** Review queue order (drafts, most problems first); "next" after a just-published draft is the head of the queue. */
export function nextInQueue(sp: URLSearchParams): string | null {
  const q = new URLSearchParams({ status: 'draft', sort: 'problems', pageSize: '500' })
  for (const k of ['type', 'importId']) if (sp.get(k)) q.set(k, sp.get(k)!)
  const after = sp.get('after')
  const { items } = adminWorks(q)
  const i = items.findIndex((w) => w.id === after)
  return (i >= 0 ? items[i + 1] : items.find((w) => w.id !== after))?.id ?? null
}

export function relatedOptions(sp: URLSearchParams) {
  const authors = sp.getAll('author')
  return db.works
    .filter((w) => w.type === sp.get('type') && (w.id === sp.get('include') || w.authors.some((a) => a.personId && authors.includes(a.personId))))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .map((w) => ({ id: w.id, label: `${w.year} · ${w.title.slice(0, 90)}` }))
}

const refsOf = (w: AdminWork): AuthorRef[][] =>
  [w.authors, ...(isStudent(w) ? [w.supervisors] : []), ...(w.type === 'talk' ? [w.presenters] : [])]

export function unmatched() {
  const m = new Map<string, Set<string>>()
  for (const w of db.works) for (const a of allAuthorRefs(w)) if (!a.personId) {
    if (!m.has(a.displayName)) m.set(a.displayName, new Set())
    m.get(a.displayName)!.add(w.id)
  }
  const items = [...m].map(([displayName, ids]) => ({
    displayName, count: ids.size, workIds: [...ids], suggestions: suggestions(displayName),
    works: [...ids].map((id) => ({ id, title: db.works.find((w) => w.id === id)?.title || 'Без названия' })),
  }))
  items.sort((a, b) => a.displayName.localeCompare(b.displayName, 'ru'))
  return { items, total: items.length }
}

// "Иванов И. И." finds "Иванов Иван Иванович": every word of the query starts a word of the name; one typo is forgiven.
function nameMatches(q: string, name: string) {
  const ws = wordsOf(name)
  return wordsOf(q).every((t) => ws.some((w) => wordMatch(t, w, true)))
}

export function adminPeople(sp: URLSearchParams) {
  const q = sp.get('q')?.toLowerCase().trim()
  const counts = new Map<string, number>()
  for (const w of db.works) for (const id of new Set(allAuthorRefs(w).map((a) => a.personId))) if (id) counts.set(id, (counts.get(id) ?? 0) + 1)
  const items = activePeople()
    .filter((p) => p.id !== sp.get('exclude') && (!sp.get('kind') || p.kind === sp.get('kind')) &&
      (!sp.get('department') || p.department === sp.get('department')) && (!q || [p.fullName, ...(p.aliases ?? [])].some((n) => nameMatches(q, n))))
    .sort((a, b) => a.fullName.localeCompare(b.fullName, 'ru'))
    .map((p) => ({ ...p, shortName: shortName(p.fullName), workCount: counts.get(p.id) ?? 0 }))
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const pageSize = Math.min(200, Number(sp.get('pageSize')) || 50)
  return { items: items.slice((page - 1) * pageSize, page * pageSize), total: items.length }
}

export function personCard(p: MockPerson) {
  const works = db.works.filter((w) => allAuthorRefs(w).some((a) => a.personId === p.id))
    .map((w) => ({ id: w.id, type: w.type, title: w.title, year: w.year, status: w.status }))
    .sort((a, b) => b.year - a.year)
  return { ...p, works }
}

function relink(person: MockPerson, test: (a: AuthorRef) => boolean) {
  for (const w of db.works) for (const refs of refsOf(w)) for (const a of refs) if (test(a)) {
    a.personId = person.id
    a.kind = person.kind
  }
}

export function match(displayName: string, personId: string | null, kind: PersonKind): MockPerson | undefined {
  let p: MockPerson | undefined
  if (personId) p = resolvePerson(personId)
  else {
    p = { id: `p-${++db.seq}`, kind, fullName: displayName, aliases: [] }
    db.people.push(p)
  }
  if (!p) return undefined
  if (!p.aliases?.includes(displayName)) p.aliases = [...(p.aliases ?? []), displayName]
  relink(p, (a) => !a.personId && a.displayName === displayName)
  for (const imp of db.imports) for (const it of imp.items) {
    for (const h of [it, it.result]) if (h?.unmatchedAuthors) h.unmatchedAuthors = h.unmatchedAuthors.filter((n) => n !== displayName)
    if (it.draft) relinkDraft(it.draft, p, displayName)
  }
  return p
}

function relinkDraft(w: AdminWork, p: MockPerson, name: string) {
  for (const refs of refsOf(w)) for (const a of refs) if (!a.personId && a.displayName === name) Object.assign(a, { personId: p.id, kind: p.kind })
}

export function merge(main: MockPerson, from: MockPerson) {
  relink(main, (a) => a.personId === from.id)
  main.aliases = [...new Set([...(main.aliases ?? []), from.fullName, ...(from.aliases ?? [])])].filter((n) => n !== main.fullName)
  for (const k of ['department', 'position', 'academicDegree', 'academicTitle', 'orcid', 'affiliation', 'photoUrl'] as const) main[k] ??= from[k]
  from.mergedInto = main.id
}

/** Keep publication ↔ talk links symmetric. */
export function syncRelated(w: AdminWork, before?: AdminWork) {
  const otherKey = w.type === 'publication' ? 'relatedTalkId' : w.type === 'talk' ? 'relatedPublicationId' : null
  if (!otherKey) return
  const backKey = otherKey === 'relatedTalkId' ? 'relatedPublicationId' : 'relatedTalkId'
  const rec = w as unknown as Record<string, string | undefined>
  if (!rec[otherKey]) delete rec[otherKey]
  const prev = (before as unknown as Record<string, string | undefined> | undefined)?.[otherKey]
  const get = (id?: string) => db.works.find((x) => x.id === id) as unknown as Record<string, string | undefined> | undefined
  if (prev && prev !== rec[otherKey]) {
    const old = get(prev)
    if (old?.[backKey] === w.id) delete old[backKey]
  }
  const target = get(rec[otherKey])
  if (target) {
    const stale = get(target[backKey])
    if (stale && stale.id !== w.id) delete stale[otherKey]
    target[backKey] = w.id
  }
}

// ---------- imports ----------
const normTitle = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
const findDuplicate = (type: WorkType, title: string, year?: number) =>
  db.works.find((w) => w.type === type && w.status !== 'rejected' && normTitle(w.title) === normTitle(title) && (!year || w.year === year))
const splitNames = (s: string) => s.split(/[;\n]+/).map((x) => x.trim()).filter(Boolean)
const parseDate = (s: string) => {
  const m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(s.trim())
  return m ? `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}` : s.trim()
}
const aiConf = () => Math.round((0.45 + Math.random() * 0.53) * 100) / 100
const titleKeywords = (title: string) =>
  [...new Set(title.toLowerCase().split(/[^\p{L}-]+/u).filter((x) => x.length > 7))].slice(0, 4)
const now = () => new Date().toISOString()

function draftMeta(importId: string, fieldMeta: AdminWork['fieldMeta']) {
  const t = now()
  return { status: 'draft' as const, importId, fieldMeta, createdAt: t, updatedAt: t, errors: [], issues: [], actions: [], materials: [] }
}

function studentFromRecord(type: 'vkr' | 'coursework', r: Record<string, string>, importId: string, d: number): { w: AdminWork; unmatched: string[] } {
  let author = matchExact(r['ФИО автора'])
  if (!author) {
    author = gen.newStudent(d, type === 'vkr' ? 'graduate' : 'student')
    Object.assign(author, { fullName: r['ФИО автора'], aliases: [shortName(r['ФИО автора'])], department: r['Кафедра'] })
  }
  const sup = refTo(r['ФИО руководителя'], 'staff')
  const consent = /^(да|yes|1|true|\+)$/i.test(r['Согласие на публикацию текста'] ?? '')
  const t = 'table' as const
  const w = {
    id: `${type === 'vkr' ? 'vkr' : 'cw'}-${++db.seq}`, type, title: r['Тема'],
    authors: [{ personId: author.id, displayName: shortName(author.fullName), kind: author.kind }],
    supervisors: [sup], year: Number(r['Год']), department: r['Кафедра'],
    level: fromLabel(levelLabels, r['Уровень']) ?? 'bachelor',
    direction: { code: r['Код направления'], name: r['Название направления'] ?? '' },
    profile: r['Профиль'] || undefined, discipline: r['Дисциплина'] || undefined,
    kind: type === 'coursework' ? (/проект/i.test(r['Работа или проект'] ?? '') ? 'project' : 'work') : undefined,
    annotation: `В работе рассматривается тема «${r['Тема']}». Описаны постановка задачи, обзор существующих решений, предложенный подход и результаты его экспериментальной проверки.`,
    keywords: titleKeywords(r['Тема']), language: 'ru', pages: 40 + Math.floor(Math.random() * 60),
    consent, accessLevel: consent && type === 'vkr' ? 'full' : 'card_only', hasFile: true, originalFileName: r['Имя файла'],
    ...draftMeta(importId, {
      title: { source: t }, authors: { source: t }, supervisors: { source: t }, year: { source: t }, level: { source: t },
      direction: { source: t }, department: { source: t }, profile: { source: t }, discipline: { source: t },
      annotation: { source: 'ai', confidence: aiConf() }, keywords: { source: 'ai', confidence: aiConf() },
      pages: { source: 'ai', confidence: 0.99 }, language: { source: 'ai', confidence: 0.99 },
    }),
  } as AdminWork
  return { w, unmatched: sup.personId ? [] : [sup.displayName] }
}

function tableRecordToWork(type: 'publication' | 'talk', r: Record<string, string>, importId: string): { w: AdminWork; unmatched: string[] } {
  const filled = (keys: Record<string, string>) =>
    Object.fromEntries(Object.entries(keys).filter(([, col]) => r[col]).map(([k]) => [k, { source: 'table' as const }]))
  let w: AdminWork
  if (type === 'publication') {
    const idn = r['ISSN/ISBN'] ?? ''
    const isbn = /^(isbn|97[89])/i.test(idn.trim())
    w = {
      id: `pub-${++db.seq}`, type, title: r['Название'], authors: splitNames(r['Авторы']).map((n) => refTo(n)),
      year: Number(r['Год']), department: r['Кафедра'] || undefined, keywords: [], language: /[а-яё]/i.test(r['Название']) ? 'ru' : 'en',
      accessLevel: 'card_only', hasFile: false, kind: fromLabel(pubKindLabels, r['Вид']) ?? 'other', venue: r['Журнал/сборник'],
      volume: r['Том'] || undefined, issue: r['Номер'] || undefined, pages: r['Страницы'] || undefined,
      publisher: r['Издательство'] || undefined, doi: r['DOI'] || undefined,
      isbn: isbn ? idn.replace(/^isbn\s*/i, '') : undefined, issn: !isbn && idn ? idn : undefined,
      indexing: (r['Индексация'] ?? '').split(/[,;]/).map((x) => fromLabel(indexingLabels, x)).filter(Boolean) as Indexing[],
      externalUrl: r['Ссылка'] || undefined,
      ...draftMeta(importId, filled({ title: 'Название', authors: 'Авторы', year: 'Год', department: 'Кафедра', kind: 'Вид', venue: 'Журнал/сборник', volume: 'Том', issue: 'Номер', pages: 'Страницы', publisher: 'Издательство', doi: 'DOI', issn: 'ISSN/ISBN', indexing: 'Индексация' })),
    } as Publication & AdminWork
  } else {
    const presenters = splitNames(r['Докладчики']).map((n) => refTo(n))
    const coauthors = splitNames(r['Соавторы'] ?? '').map((n) => refTo(n))
    const dateStart = parseDate(r['Дата начала'])
    w = {
      id: `talk-${++db.seq}`, type, title: r['Название доклада'], authors: [...presenters, ...coauthors], presenters,
      year: Number(dateStart.slice(0, 4)), department: r['Кафедра'] || undefined, keywords: [],
      language: /[а-яё]/i.test(r['Название доклада']) ? 'ru' : 'en', accessLevel: 'full', hasFile: false,
      event: {
        name: r['Конференция'], level: fromLabel(confLevelLabels, r['Уровень']) ?? 'national', city: r['Город'] || undefined,
        country: r['Страна'] || undefined, dateStart, dateEnd: r['Дата окончания'] ? parseDate(r['Дата окончания']) : undefined,
        url: r['Ссылка'] || undefined,
      },
      kind: fromLabel(talkKindLabels, r['Вид доклада'] ?? '') ?? 'section', format: fromLabel(formatLabels, r['Формат'] ?? '') ?? 'offline',
      award: r['Награда'] || undefined,
      ...draftMeta(importId, filled({ title: 'Название доклада', authors: 'Докладчики', presenters: 'Докладчики', year: 'Дата начала', department: 'Кафедра', 'event.name': 'Конференция', 'event.level': 'Уровень', 'event.dateStart': 'Дата начала', kind: 'Вид доклада', format: 'Формат' })),
    } as Talk & AdminWork
  }
  return { w, unmatched: [...new Set(allAuthorRefs(w).filter((a) => !a.personId).map((a) => a.displayName))] }
}

const FAKE_SURNAMES = ['Ковалёва', 'Романов', 'Лукина', 'Сорокин', 'Егорова', 'Титов', 'Белякова', 'Гусев', 'Щербакова', 'Медведев', 'Калинина', 'Антонов']

export class ImportError extends Error {}

export async function previewTable(file: File, type: WorkType): Promise<ImportPreview> {
  const rows = await readTable(file).catch(() => {
    throw new ImportError('Не удалось прочитать таблицу. Проверьте, что это .xlsx или .csv.')
  })
  const [header = [], ...body] = rows
  const missing = toRecords(rows, type).map((r) => missingInRow(r, type))
  return {
    header,
    rows: body.slice(0, 10).map((cells, i) => ({ line: i + 2, cells, missing: missing[i] })),
    total: body.length,
    bad: missing.filter((m) => m.length).length,
    missingColumns: missingColumns(header, type),
  }
}

/** The server's split: numbered «1. …» entries, else paragraphs, else lines. */
export function splitEntries(text: string): string[] {
  text = text.replace(/\r\n/g, '\n').trim()
  const numbered = /^\s*\d{1,4}\s*[.)]\s+/gm
  const parts = (text.match(numbered) ?? []).length >= 2 ? text.split(numbered) : /\n\s*\n/.test(text) ? text.split(/\n\s*\n/) : text.split('\n')
  return parts.map((p) => p.split(/\s+/).join(' ').trim()).filter((p) => /[\p{L}\p{N}]/u.test(p))
}

/** Offline stand-in for the LLM: «Иванов И. И., Петров П. П. Название // Журнал. 2024. № 2. С. 1–9.» */
function guessRecord(type: 'publication' | 'talk', entry: string): Record<string, string> {
  const names = entry.match(/[А-ЯЁA-Z][а-яёa-z-]+\s+[А-ЯЁA-Z]\.\s?(?:[А-ЯЁA-Z]\.)?/g) ?? []
  const rest = names.reduce((s, n) => s.replace(n, ''), entry).replace(/^[\s,]+/, '')
  const [title = '', where = ''] = rest.split('//').map((s) => s.trim())
  const year = entry.match(/\b(19|20)\d{2}\b/)?.[0] ?? ''
  const venue = where.split('.')[0]?.trim() ?? ''
  if (type === 'publication') return { 'Авторы': names.join('; '), 'Название': title.replace(/[.\s]+$/, ''), 'Вид': 'Статья в журнале', 'Журнал/сборник': venue, 'Год': year }
  return { 'Докладчики': names.join('; '), 'Название доклада': title.replace(/[.\s]+$/, ''), 'Конференция': venue, 'Уровень': 'национальный', 'Дата начала': year ? `01.01.${year}` : '' }
}

export async function createImport(form: FormData, user: string): Promise<MockImport> {
  const kind = form.get('kind') as 'archive' | 'table' | 'text'
  const workType = form.get('workType') as WorkType
  if (kind === 'text') return createTextImport(String(form.get('text') ?? ''), workType as 'publication' | 'talk', user)
  const uploads = form.getAll('file') as File[] // archive: any number of PDFs and ZIPs
  const file = uploads[0] ?? null
  const metaTable = form.get('metaTable') as File | null
  if (!file) throw new ImportError('Файл не передан')
  const id = `imp-${++db.seq}`
  const imp: MockImport = {
    id, kind, workType, fileName: uploads.length > 1 ? `${file.name} и ещё ${uploads.length - 1}` : file.name, metaTableName: metaTable?.name || undefined, status: 'processing',
    createdAt: now(), createdBy: user, items: [], stats: stats([]),
  }

  if (kind === 'archive') {
    const type = workType as 'vkr' | 'coursework'
    const records = metaTable && metaTable.size ? toRecords(await readTable(metaTable), type) : null
    const pdfs = uploads.filter((f) => /\.pdf$/i.test(f.name))
    const n = records?.length || pdfs.length || 12
    const start = Date.now()
    for (let i = 0; i < n; i++) {
      const rec = records?.[i]
      const d = Math.floor(Math.random() * 3)
      const ref = rec?.['Имя файла'] || pdfs[i]?.name || `${FAKE_SURNAMES[i % FAKE_SURNAMES.length]}_${type === 'vkr' ? 'ВКР' : 'Курсовая'}_${new Date().getFullYear()}.pdf`
      // the server reads the type off the title page; the mock goes by the file name
      const t: 'vkr' | 'coursework' = !rec && /курсов/i.test(ref) ? 'coursework' : type
      const item: MockItem = { ref, status: 'pending', readyAt: start + 3000 * (1 + Math.floor((i * 5) / n)) }
      if (i === 1) item.result = { status: 'error', error: 'Скан без текстового слоя' }
      else {
        let w: AdminWork
        let unmatchedNames: string[] = []
        if (rec && !missingInRow(rec, t).length) ({ w, unmatched: unmatchedNames } = studentFromRecord(t, rec, id, d))
        else {
          w = gen.studentWork(t, d, new Date().getFullYear(), ++db.seq)
          w.id = `${t === 'vkr' ? 'vkr' : 'cw'}-${db.seq}`
          Object.assign(w, { originalFileName: ref, publishedAt: undefined }, draftMeta(id, Object.fromEntries(
            ['title', 'authors', 'supervisors', 'year', 'level', 'direction', 'department', 'discipline', 'annotation', 'keywords', 'pages', 'language']
              .map((k) => [k, { source: 'ai' as const, confidence: aiConf() }]),
          )))
        }
        const dup = i === 3 ? db.works.find((x) => x.type === t && x.status === 'published') : findDuplicate(t, w.title, w.year)
        if (dup) Object.assign(w, { title: dup.title, duplicateOf: dup.id })
        item.draft = w
        item.result = { status: dup ? 'duplicate' : 'filled', title: w.title, duplicateOf: dup?.id, unmatchedAuthors: unmatchedNames.length ? unmatchedNames : undefined }
      }
      imp.items.push(item)
    }
  } else {
    const type = workType as 'publication' | 'talk'
    const rows = await readTable(file)
    const missing = missingColumns(rows[0] ?? [], type)
    if (missing.length) throw new ImportError(`В таблице нет обязательных колонок: ${missing.join(', ')}. Скачайте шаблон.`)
    toRecords(rows, type).forEach((rec, i) => addRow(imp, type, rec, `Строка ${i + 2}`))
    imp.status = 'done'
  }
  imp.stats = stats(imp.items)
  db.imports.unshift(imp)
  return imp
}

function addRow(imp: MockImport, type: 'publication' | 'talk', rec: Record<string, string>, ref: string, source?: FieldMeta) {
  const miss = missingInRow(rec, type)
  if (miss.length) {
    const error = miss.includes('Год') ? 'Не указан год' : miss.includes('Дата начала') ? 'Не указана дата начала' : `Не заполнено: ${miss.join(', ')}`
    imp.items.push({ ref, title: rec['Название'] || rec['Название доклада'] || undefined, status: 'error', error })
    return
  }
  const { w, unmatched: un } = tableRecordToWork(type, rec, imp.id)
  if (source) for (const k of Object.keys(w.fieldMeta)) w.fieldMeta[k] = { ...source }
  const dup = findDuplicate(type, w.title, w.year)
  if (dup) w.duplicateOf = dup.id
  db.works.push(w)
  imp.items.push({ ref, title: w.title, status: dup ? 'duplicate' : 'filled', workId: w.id, duplicateOf: dup?.id, unmatchedAuthors: un.length ? un : undefined })
}

function createTextImport(text: string, type: 'publication' | 'talk', user: string): MockImport {
  const entries = splitEntries(text)
  if (!entries.length) throw new ImportError('В тексте нет ни одной записи')
  const imp: MockImport = {
    id: `imp-${++db.seq}`, kind: 'text', workType: type, fileName: `Список текстом: ${entries.length}`, status: 'done',
    createdAt: now(), createdBy: user, items: [], stats: stats([]),
  }
  const ai = { source: 'ai' as const, confidence: 0.85 }
  entries.forEach((e) => addRow(imp, type, guessRecord(type, e), e.length > 120 ? e.slice(0, 119) + '…' : e, ai))
  imp.stats = stats(imp.items)
  db.imports.unshift(imp)
  return imp
}

/** Lazily move archive items along their timeline on every read. */
export function advance(imp: MockImport) {
  if (imp.status !== 'processing') return
  const t = Date.now()
  for (const it of imp.items) {
    if (it.status === 'pending' && it.readyAt! - 1500 <= t) it.status = 'extracted'
    if (it.status === 'extracted' && it.readyAt! <= t) {
      Object.assign(it, it.result)
      if (it.draft) {
        // a card without errors or issues is published right away (card only, text closed until consent)
        const checked = withChecks(it.draft)
        if (it.result?.status === 'filled' && !checked.errors.length && !checked.issues.length) {
          Object.assign(it.draft, { status: 'published', publishedAt: now() })
          it.status = 'published'
        }
        db.works.push(it.draft)
        it.workId = it.draft.id
        it.draft = undefined
      }
    }
  }
  imp.stats = stats(imp.items)
  if (imp.items.every((i) => i.status !== 'pending' && i.status !== 'extracted')) imp.status = 'done'
}

/** «Повторить»: failed files go back in the queue; a broken scan just fails again. */
export function retryImport(imp: MockImport): boolean {
  const failed = imp.kind === 'archive' ? imp.items.filter((i) => i.status === 'error') : []
  const t = Date.now()
  failed.forEach((it, n) => {
    it.result ??= { status: 'error', error: it.error }
    Object.assign(it, { status: 'pending', error: undefined, readyAt: t + 3000 * (n + 1) })
  })
  if (failed.length) imp.status = 'processing'
  return failed.length > 0
}

export const publicItem = ({ readyAt, result, draft, ...rest }: MockItem) => rest
export const publicImport = ({ items, ...rest }: MockImport) => rest
