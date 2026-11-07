// Display of a work for the public card and page: the frontend only renders what this builds.
import type { AuthorRef, DetailRow, Material, PersonLink, SubjectScheme, ViewItem, Work, WorkView } from '@/api/types'
import { formatSize } from '@/lib/format'
import {
  confLevelLabels, courseworkKindLabels, formatLabels, indexingLabels, languageLabels, levelLabels, materialKindLabels, pubKindLabels,
  subjectSchemeLabels, talkKindLabels,
} from '@/lib/labels'

const toDate = (iso: string) => new Date(iso.length === 10 ? iso + 'T00:00:00' : iso)
const dayMonth = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'long' })
const full = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'long', year: 'numeric' })

/** "12–14 мая 2024 г.", "30 мая – 2 июня 2024 г." */
/** Abbreviations in the language of the work (ГОСТ Р 7.0.100–2018): an English article gets Vol., No., P. and [et al.]. */
export const imprint = (w: { language: string }) => (w.language === 'en'
  ? { vol: 'Vol.', no: 'No.', pages: 'P.', bookPages: 'p.', etAl: '[et al.]', monograph: 'monograph', textbook: 'textbook' }
  : { vol: 'Т.', no: '№', pages: 'С.', bookPages: 'с.', etAl: '[и др.]', monograph: 'монография', textbook: 'учебное пособие' })

export function formatDateRange(start: string, end?: string): string {
  const a = toDate(start)
  if (!end || end === start) return full.format(a)
  const b = toDate(end)
  if (a.getFullYear() !== b.getFullYear()) return `${full.format(a)} – ${full.format(b)}`
  if (a.getMonth() === b.getMonth()) return `${a.getDate()}–${full.format(b)}`
  return `${dayMonth.format(a)} – ${full.format(b)}`
}

const doiUrl = (doi: string) => `https://doi.org/${doi}`
/** Teachers, students and graduates have public pages; external authors do not. */
const links = (refs: AuthorRef[]): PersonLink[] =>
  refs.map((a) => ({ name: a.displayName, profileId: a.personId && a.kind !== 'external' ? a.personId : undefined }))
/** A detail row, or nothing when there is nothing to show. */
const row = (label: string, text?: string | number, extra: Omit<DetailRow, 'label' | 'text'> = {}): DetailRow[] =>
  text || extra.people?.length ? [{ label, text: text ? String(text) : '', ...extra }] : []

/** `materials` = the approved ones, listed for download. */
export function workView(w: Work, materials: Material[] = []): WorkView {
  const meta: ViewItem[] = []
  let kind: string
  let byline: WorkView['byline']
  let where: string
  let details: DetailRow[]
  if (w.type === 'publication') {
    const book = w.kind === 'monograph' || w.kind === 'textbook'
    const t = imprint(w)
    const imprintText = [w.volume && `${t.vol} ${w.volume}`, w.issue && `${t.no} ${w.issue}`, w.pages && (book ? `${w.pages} ${t.bookPages}` : `${t.pages} ${w.pages}`)].filter(Boolean).join(', ')
    const indexing = w.indexing.map((i) => indexingLabels[i]).join(', ')
    kind = pubKindLabels[w.kind]
    byline = [{ people: links(w.authors) }]
    where = [w.venue || w.publisher, w.year, imprintText].filter(Boolean).join('. ')
    const lists = (w.journalLists ?? []).join(', ')
    if (indexing) meta.push({ text: indexing, tone: 'info' })
    if (lists) meta.push({ text: lists })
    if (w.citations) meta.push({ text: `цитирований: ${w.citations}` })
    if (w.doi) meta.push({ text: `DOI ${w.doi}`, href: doiUrl(w.doi) })
    details = [
      ...row('Вид', kind), ...row(w.kind === 'journal_article' ? 'Журнал' : 'Издание', w.venue), ...row('Выходные данные', imprintText),
      ...row('Издательство', w.publisher), ...row('DOI', w.doi, { href: w.doi && doiUrl(w.doi) }), ...row('ISSN', w.issn), ...row('ISBN', w.isbn),
      ...row('Индексация', indexing, { tone: 'info' }), ...row('Журнал в списках', lists), ...row('Цитирований', w.citations),
    ]
  } else if (w.type === 'talk') {
    const e = w.event
    const dates = formatDateRange(e.dateStart, e.dateEnd)
    kind = `Доклад (${talkKindLabels[w.kind].toLowerCase()})`
    byline = [{ label: 'Докладчики:', people: links(w.presenters) }]
    where = [e.name, e.city, dates].filter(Boolean).join(', ')
    meta.push({ text: `${confLevelLabels[e.level].toLowerCase()} конференция` })
    if (w.award) meta.push({ text: w.award, tone: 'warn' })
    details = [
      ...row('Конференция', e.name, { href: e.url }), ...row('Уровень', confLevelLabels[e.level]),
      ...row('Место и даты', [e.city, e.country, dates].filter(Boolean).join(', ')), ...row('Вид доклада', talkKindLabels[w.kind]),
      ...row('Формат', formatLabels[w.format]), ...row('Докладчики', '', { people: links(w.presenters) }), ...row('Награда', w.award, { tone: 'warn' }),
    ]
  } else {
    kind = w.type === 'vkr' ? 'Выпускная квалификационная работа' : courseworkKindLabels[w.kind ?? 'work']
    byline = [{ people: links(w.authors) }, { label: 'руководитель', people: links(w.supervisors) }]
    where = `${w.direction.code} ${w.direction.name}${w.discipline ? `, ${w.discipline}` : ''}`
    details = [
      ...row(w.supervisors.length > 1 ? 'Руководители' : 'Руководитель', '', { people: links(w.supervisors) }),
      ...row('Уровень', levelLabels[w.level]), ...row('Направление', `${w.direction.code} ${w.direction.name}`),
      ...row('Профиль', w.profile), ...row('Дисциплина', w.discipline), ...row('Страниц', w.pages),
    ]
  }
  if (w.accessLevel === 'card_only') meta.push({ text: 'только описание' })
  const open = w.accessLevel === 'full' && w.hasFile
  return {
    kind,
    authors: links(w.authors),
    byline,
    where,
    snippet: w.type === 'vkr' || w.type === 'coursework' ? w.annotation : undefined,
    meta,
    details: [
      ...details, ...row('Год', w.year), ...row('Кафедра', w.department), ...row('Язык', languageLabels[w.language]),
      ...(Object.keys(subjectSchemeLabels) as SubjectScheme[]).flatMap((scheme) =>
        row(subjectSchemeLabels[scheme], (w.subjects ?? []).filter((x) => x.scheme === scheme).map((x) => `${x.code} ${x.name}`).join('; '))),
    ],
    materials: materials.map((m) => ({ id: m.id, label: `${materialKindLabels[m.kind]}: ${m.name}, ${formatSize(m.size)}` })),
    fileButton: open ? (w.type === 'talk' ? 'Открыть материалы' : 'Открыть текст') : undefined,
    external: w.type === 'publication' && w.doi ? { href: doiUrl(w.doi), label: 'Открыть по DOI' }
      : w.externalUrl ? { href: w.externalUrl, label: 'Страница издания' } : undefined,
    closedNote: w.accessLevel === 'card_only' ? 'Полный текст закрыт — доступна только карточка' : undefined,
  }
}
