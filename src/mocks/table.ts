// Import tables: templates (section 6.7), CSV/XLSX reading, header and row checks.
import type { WorkType } from '@/api/types'

export interface Column { name: string; required?: boolean }

const studentCols = (coursework: boolean): Column[] => [
  { name: 'Имя файла', required: true },
  { name: 'ФИО автора', required: true },
  { name: 'Тема', required: true },
  { name: 'ФИО руководителя', required: true },
  { name: 'Степень руководителя' },
  { name: 'Год', required: true },
  { name: 'Уровень', required: true },
  { name: 'Код направления', required: true },
  { name: 'Название направления' },
  { name: 'Профиль' },
  ...(coursework ? [{ name: 'Дисциплина' }, { name: 'Работа или проект' }] : []),
  { name: 'Кафедра', required: true },
  { name: 'Согласие на публикацию текста' },
]

export const templates: Record<WorkType, Column[]> = {
  vkr: studentCols(false),
  coursework: studentCols(true),
  publication: [
    { name: 'Авторы', required: true },
    { name: 'Название', required: true },
    { name: 'Вид', required: true },
    { name: 'Журнал/сборник', required: true },
    { name: 'Год', required: true },
    { name: 'Том' }, { name: 'Номер' }, { name: 'Страницы' }, { name: 'Издательство' },
    { name: 'DOI' }, { name: 'ISSN/ISBN' }, { name: 'Индексация' }, { name: 'Ссылка' }, { name: 'Кафедра' },
  ],
  talk: [
    { name: 'Докладчики', required: true },
    { name: 'Соавторы' },
    { name: 'Название доклада', required: true },
    { name: 'Конференция', required: true },
    { name: 'Уровень', required: true },
    { name: 'Город' }, { name: 'Страна' },
    { name: 'Дата начала', required: true },
    { name: 'Дата окончания' }, { name: 'Вид доклада' }, { name: 'Формат' }, { name: 'Награда' },
    { name: 'Ссылка' }, { name: 'Кафедра' },
  ],
}

const norm = (s: string) => s.replace(/\*/g, '').trim().replace(/\s+/g, ' ').toLowerCase()

/** RFC 4180-ish CSV; delimiter guessed from the first line (Excel in ru locale writes ";"). */
export function parseCsv(text: string): string[][] {
  text = text.replace(/^﻿/, '')
  const first = text.split('\n', 1)[0]
  const delim = [';', '\t', ','].reduce((best, d) => (first.split(d).length > first.split(best).length ? d : best), ',')
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++ }
      else if (ch === '"') quoted = false
      else cell += ch
    } else if (ch === '"') quoted = true
    else if (ch === delim) { row.push(cell); cell = '' }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell); rows.push(row); row = []; cell = ''
    } else cell += ch
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  return rows.filter((r) => r.some((c) => c.trim()))
}

export function toCsv(rows: string[][]): string {
  const esc = (c: string) => (/[;"\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)
  return '﻿' + rows.map((r) => r.map(esc).join(';')).join('\r\n') + '\r\n'
}

export async function readTable(file: File): Promise<string[][]> {
  if (/\.csv$/i.test(file.name)) return parseCsv(await file.text())
  const { default: readXlsxFile } = await import('read-excel-file')
  const rows = await readXlsxFile(file)
  return rows
    .map((r) => r.map((c) => (c instanceof Date ? c.toISOString().slice(0, 10) : c == null ? '' : String(c))))
    .filter((r) => r.some((c) => c.trim()))
}

export function missingColumns(header: string[], type: WorkType): string[] {
  const have = new Set(header.map(norm))
  return templates[type].filter((c) => c.required && !have.has(norm(c.name))).map((c) => c.name)
}

/** Rows → objects keyed by template column name. */
export function toRecords(rows: string[][], type: WorkType): Record<string, string>[] {
  const [header = [], ...body] = rows
  const idx = templates[type].map((c) => [c.name, header.findIndex((h) => norm(h) === norm(c.name))] as const)
  return body.map((r) => Object.fromEntries(idx.map(([name, i]) => [name, i >= 0 ? (r[i] ?? '').trim() : ''])))
}

export const missingInRow = (rec: Record<string, string>, type: WorkType) =>
  templates[type].filter((c) => c.required && !rec[c.name]).map((c) => c.name)

export const templateCsv = (type: WorkType) => toCsv([templates[type].map((c) => c.name)])

/** Reverse lookup for table import: "Статья в журнале" → "journal_article"; "международный" → "international". */
export function fromLabel<K extends string>(labels: Record<K, string>, text: string): K | undefined {
  const t = text.trim().toLowerCase()
  if (!t) return undefined
  const keys = Object.keys(labels) as K[]
  const exact = keys.find((k) => labels[k].toLowerCase() === t || k === t)
  if (exact) return exact
  // ponytail: 5-letter stem match covers gender/case endings; ambiguous stems are rejected
  const stem = keys.filter((k) => labels[k].toLowerCase().slice(0, 5) === t.slice(0, 5))
  return stem.length === 1 ? stem[0] : undefined
}
