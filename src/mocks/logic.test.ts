import { describe, expect, it } from 'vitest'
import type { AdminWork, Publication, Talk } from '@/api/types'
import { talkSchema } from './schemas'
import { gostCitation } from './gost'
import { confLevelLabels, pubKindLabels } from '@/lib/labels'
import { nameKey, parseName, shortName } from './names'
import { fromLabel, missingColumns, parseCsv, toRecords } from './table'
import { adminPeople, applyEdit, catalog, facets, fillFromDoi, latest, LOW_CONFIDENCE, metrics, nextInQueue, profile, suggest, template, withChecks } from './logic'
import { db } from './seed'

describe('csv', () => {
  it('parses ; delimiter, quotes, BOM and CRLF', () => {
    const rows = parseCsv('﻿Авторы;Название\r\n"Иванов И. И.; Петров П. П.";"Статья ""в кавычках"""\r\n')
    expect(rows).toEqual([['Авторы', 'Название'], ['Иванов И. И.; Петров П. П.', 'Статья "в кавычках"']])
  })
  it('detects missing template columns', () => {
    expect(missingColumns(['Авторы', 'Название'], 'publication')).toEqual(['Вид', 'Журнал/сборник', 'Год'])
    expect(toRecords([['Год', 'Название'], ['2024', 'X']], 'publication')[0]).toMatchObject({ Год: '2024', Название: 'X', Авторы: '' })
  })
})

describe('names', () => {
  it('reads every common spelling', () => {
    expect(parseName('Иванов Иван Иванович')).toEqual({ surname: 'Иванов', initials: ['И', 'И'] })
    expect(parseName('И.И. Иванов')).toEqual({ surname: 'Иванов', initials: ['И', 'И'] })
    expect(shortName('Белова Наталья Викторовна')).toBe('Белова Н. В.')
    expect(nameKey('Иванов И.И.')).toBe(nameKey('I. Ivanov'))
    expect(nameKey('Захаров И.')).toBe(nameKey('Zakharov I.'))
    expect(nameKey('Филиппович Андрей Юрьевич')).toBe(nameKey('Philippovich A.'))
    expect(nameKey('Архангельский А. И.')).toBe(nameKey('Arkhangelsky A. I.'))
  })
  it('maps table labels with endings', () => {
    expect(fromLabel(confLevelLabels, 'международный')).toBe('international')
    expect(fromLabel(pubKindLabels, 'Статья в сборнике')).toBe('proceedings_paper')
    expect(fromLabel(pubKindLabels, 'Статья')).toBeUndefined() // ambiguous stem
  })
})

describe('gost', () => {
  const base: Omit<Publication, 'kind' | 'title' | 'venue' | 'authors'> = { id: 'x', type: 'publication', year: 2024, keywords: [], language: 'ru', accessLevel: 'card_only', hasFile: false, indexing: [] }
  it('journal article with heading author', () => {
    const w = { ...base, kind: 'journal_article', title: 'Название статьи', venue: 'Журнал', volume: '12', issue: '3', pages: '15–24', doi: '10.1/x',
      authors: [{ displayName: 'Иванов И. И.', kind: 'staff' }, { displayName: 'Петров П. П.', kind: 'staff' }] } as Publication
    expect(gostCitation(w)).toBe('Иванов, И. И. Название статьи / И. И. Иванов, П. П. Петров // Журнал. – 2024. – Т. 12, № 3. – С. 15–24. – DOI 10.1/x.')
  })
  it('five authors: no heading, first three [и др.]', () => {
    const authors = ['А. А. Аа', 'Б. Б. Бб', 'В. В. Вв', 'Г. Г. Гг', 'Д. Д. Дд'].map((displayName) => ({ displayName, kind: 'staff' as const }))
    const s = gostCitation({ ...base, kind: 'journal_article', title: 'T', venue: 'J', authors } as Publication)
    expect(s.startsWith('T / А. А. Аа, Б. Б. Бб, В. В. Вв [и др.] // J')).toBe(true)
  })
})

describe('validation', () => {
  it('talk end date cannot precede start', () => {
    const talk = {
      type: 'talk', title: 'Доклад', authors: [{ displayName: 'Иванов И. И.', kind: 'staff' }], presenters: [{ displayName: 'Иванов И. И.', kind: 'staff' }],
      year: 2024, keywords: [], language: 'ru', accessLevel: 'full', hasFile: false, kind: 'section', format: 'offline',
      event: { name: 'Конф', level: 'national', dateStart: '2024-05-10', dateEnd: '2024-05-09' },
    } as unknown as Talk
    const r = talkSchema.safeParse(talk)
    expect(r.success).toBe(false)
    expect(r.error?.issues[0].path).toEqual(['event', 'dateEnd'])
  })
})

describe('mock catalog', () => {
  it('filters and counts only published, visible works', () => {
    const scopus = catalog(new URLSearchParams('type=publication&indexing=scopus&pageSize=100'))
    expect(scopus.total).toBeGreaterThan(0)
    expect(scopus.items.every((w) => w.type === 'publication' && w.indexing.includes('scopus'))).toBe(true)
    const all = catalog(new URLSearchParams('pageSize=1')).total
    const f = facets(new URLSearchParams())
    expect(f.type.reduce((n, x) => n + x.count, 0)).toBe(all)
    const ids = new Set<string>()
    for (let p = 1; p <= 5; p++) catalog(new URLSearchParams(`pageSize=100&page=${p}`)).items.forEach((w) => ids.add(w.id))
    expect(ids.size).toBe(all)
    expect(ids.has('vkr-14')).toBe(false) // hidden
    expect(ids.has('cw-7')).toBe(false) // rejected
  })
  it('search tolerates Russian endings', () => {
    const r = catalog(new URLSearchParams('q=нейронные сети'))
    expect(r.total).toBeGreaterThan(0)
  })
})

describe('server-side checks', () => {
  it('home list shows every type', () => {
    expect(new Set(latest().map((w) => w.type)).size).toBe(4)
  })
  it('people search accepts "Фамилия И. О."', () => {
    const p = db.people.find((x) => x.kind === 'staff')!
    expect(adminPeople(new URLSearchParams({ q: shortName(p.fullName) })).items.map((x) => x.id)).toContain(p.id)
  })
  it('flags unsure AI fields and walks the review queue', () => {
    const w = db.works.find((x) => Object.values(x.fieldMeta ?? {}).some((m) => m.source === 'ai' && (m.confidence ?? 1) < LOW_CONFIDENCE))!
    expect(withChecks(w).issues.some((i) => i.startsWith('ИИ не уверен'))).toBe(true)
    const first = nextInQueue(new URLSearchParams())!
    expect(nextInQueue(new URLSearchParams({ after: first }))).not.toBe(first)
  })
})

describe('editor saves and DOI on the server', () => {
  it('marks edited fields manual, fills untouched ones from DOI and reports conflicts', () => {
    const w = { type: 'publication', id: 't-1', status: 'draft', fieldMeta: {}, createdAt: '', updatedAt: '' } as unknown as AdminWork
    applyEdit(w, { ...template('publication'), title: 'Моё название' }, template('publication'))
    expect(Object.keys(w.fieldMeta)).toEqual(['title'])
    const conflicts = fillFromDoi(w, '10.12345/kim.2026.0142', [])
    expect(conflicts.map((c) => c.field)).toEqual(['title'])
    expect(w.fieldMeta.venue).toEqual({ source: 'doi' })
    expect(fillFromDoi(w, '10.12345/kim.2026.0142', ['title'])).toEqual([])
    expect(w.fieldMeta.title).toEqual({ source: 'doi' })
  })
  it('builds the public card on the server', () => {
    const v = catalog(new URLSearchParams('type=talk&pageSize=1')).items[0].view
    expect(v.kind.startsWith('Доклад (')).toBe(true)
    expect(v.byline[0].label).toBe('Докладчики:')
  })
})

describe('people pages and journal lists', () => {
  it('h-index counts works cited at least h times, per year of works too', () => {
    const all = metrics('p-s1', new URLSearchParams())
    const [h, cites] = all.items.map((t) => Number(t.value))
    expect(h).toBeGreaterThan(0)
    expect(h).toBeLessThanOrEqual(cites)
    const one = metrics('p-s1', new URLSearchParams({ year: String(all.years[0]) }))
    expect(Number(one.items[1].value)).toBeLessThanOrEqual(cites)
  })
  it('students get a page with their own works as tabs', () => {
    const student = db.people.find((p) => p.kind === 'graduate' && db.works.some((w) => w.type === 'vkr' && w.status === 'published' && w.authors[0].personId === p.id))!
    const p = profile(student)
    expect(p.tabs[0]).toMatchObject({ key: 'vkr', query: { type: 'vkr', role: 'author' } })
    expect(p.headline).toContain('Выпускник')
  })
  it('filters publications by the journal list', () => {
    const r = catalog(new URLSearchParams('type=publication&journalList=white1&pageSize=100'))
    expect(r.total).toBeGreaterThan(0)
    expect(r.items.every((w) => w.type === 'publication' && w.journalLists?.includes('Белый список, уровень 1'))).toBe(true)
  })
})

describe('live search', () => {
  const titles = (q: string) => catalog(new URLSearchParams({ q, pageSize: '100' })).items.map((w) => w.title.toLowerCase())
  it('finds by the start of a word and forgives typos', () => {
    const exact = titles('нейронные')
    expect(exact.length).toBeGreaterThan(0)
    for (const q of ['нейр', 'нейроные', 'нецронных']) expect(titles(q).some((t) => t.includes('нейрон'))).toBe(true)
    expect(titles('ронные')).not.toContain(exact[0]) // only word starts, not the middle
  })
  it('suggests works with marked words and staff', () => {
    expect(suggest('  ')).toEqual({ works: [], people: [] })
    const res = suggest('нейр')
    expect(res.works.length).toBeGreaterThan(0)
    expect(res.works.length).toBeLessThanOrEqual(6)
    for (const w of res.works) expect(w.parts.map((p) => p.text).join('')).toBe(w.title)
    expect(res.works.flatMap((w) => w.parts.filter((p) => p.hit).map((p) => p.text.toLowerCase())).every((t) => t.startsWith('ней'))).toBe(true)
    const staff = db.people.find((p) => p.kind === 'staff' && !p.mergedInto)!
    const surname = staff.fullName.split(' ')[0]
    expect(suggest(surname).people.map((p) => p.id)).toContain(staff.id)
    const draft = db.works.find((w) => w.status === 'draft')!
    expect(suggest(draft.title).works.map((w) => w.id)).not.toContain(draft.id)
  })
})
