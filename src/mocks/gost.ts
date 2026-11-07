// Bibliographic reference per ГОСТ Р 7.0.100–2018 (simplified to the fields we store).
import type { AuthorRef, Publication, StudentWork, Talk, Work } from '@/api/types'
import { formatDateRange, imprint } from './view'
import { initialsFirst, parseName } from './names'
import { levelLabels } from '@/lib/labels'

const D = '. – '

function heading(a: AuthorRef): string {
  const { surname, initials } = parseName(a.displayName)
  return initials.length ? `${surname}, ${initials.map((i) => i + '.').join(' ')}` : surname
}

/** Heading + statement of responsibility: 1–3 authors get a heading, 4 are listed, 5+ → first 3 [и др.]. */
function authorsPart(title: string, authors: AuthorRef[], suffix = '', etAl = '[и др.]'): string {
  const names = authors.map((a) => initialsFirst(a.displayName))
  const resp = authors.length >= 5 ? `${names.slice(0, 3).join(', ')} ${etAl}` : names.join(', ')
  const head = authors.length > 0 && authors.length <= 3 ? `${heading(authors[0])} ` : ''
  return `${head}${title}${suffix}${resp ? ` / ${resp}` : ''}`
}

const end = (s: string) => (/[.!?]$/.test(s) ? s : s + '.')

function publication(w: Publication): string {
  const t = imprint(w)
  if (w.kind === 'monograph' || w.kind === 'textbook') {
    let s = authorsPart(w.title, w.authors, ` : ${t[w.kind]}`, t.etAl)
    s += D + [w.publisher, w.year].filter(Boolean).join(', ')
    if (w.pages) s += D + `${w.pages} ${t.bookPages}`
    if (w.isbn) s += D + `ISBN ${w.isbn}`
    return tail(end(s), w)
  }
  let s = authorsPart(w.title, w.authors, '', t.etAl) + ` // ${w.venue}`
  if (w.publisher && w.kind !== 'journal_article') s += D + `${w.publisher}, ${w.year}`
  else s += D + w.year
  const vol = [w.volume && `${t.vol} ${w.volume}`, w.issue && `${t.no} ${w.issue}`].filter(Boolean).join(', ')
  if (vol) s += D + vol
  if (w.pages) s += D + `${t.pages} ${w.pages}`
  return tail(end(s), w)
}

function tail(s: string, w: Work): string {
  if (w.type === 'publication' && w.doi) return `${s} – DOI ${w.doi}.`
  if (w.externalUrl) return `${s} – URL: ${w.externalUrl}.`
  return s
}

function student(w: StudentWork): string {
  const what = w.type === 'vkr'
    ? `выпускная квалификационная работа (${levelLabels[w.level].toLowerCase()})`
    : `${w.kind === 'project' ? 'курсовой проект' : 'курсовая работа'}${w.discipline ? ` по дисциплине «${w.discipline}»` : ''}`
  let s = authorsPart(w.title, w.authors, ` : ${what} : ${w.direction.code} «${w.direction.name}»`)
  if (w.supervisors.length) s += ` ; научный руководитель ${w.supervisors.map((a) => initialsFirst(a.displayName)).join(', ')}`
  if (w.department) s += ` ; ${w.department}`
  s += D + w.year
  if (w.pages) s += D + `${w.pages} с.`
  return tail(end(s), w)
}

function talk(w: Talk): string {
  const e = w.event
  const where = [e.city, formatDateRange(e.dateStart, e.dateEnd)].filter(Boolean).join(', ')
  const s = authorsPart(w.title, w.authors, ' : доклад') + ` // ${e.name} (${where})`
  return tail(end(s), w)
}

export function gostCitation(w: Work): string {
  if (w.type === 'publication') return publication(w)
  if (w.type === 'talk') return talk(w)
  return student(w)
}
