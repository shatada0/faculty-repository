import type { WorkType } from '@/api/types'

export type Tab = 'all' | WorkType
export const TABS: Tab[] = ['all', 'vkr', 'coursework', 'publication', 'talk']

export const FILTER_LABELS = {
  year: 'Год',
  level: 'Уровень',
  direction: 'Направление',
  discipline: 'Дисциплина',
  pubKind: 'Вид публикации',
  indexing: 'Индексация',
  venue: 'Журнал / сборник',
  confLevel: 'Уровень конференции',
  talkKind: 'Вид доклада',
  format: 'Формат',
  department: 'Кафедра',
  supervisor: 'Руководитель',
  author: 'Автор',
  courseKind: 'Курсовая работа или проект',
  journalList: 'Журнал в списках',
  grnti: 'Рубрика ГРНТИ',
  oecd: 'Область ОЕСР',
  wos: 'Категория Web of Science',
  asjc: 'Область Scopus (ASJC)',
} as const

export type FilterKey = keyof typeof FILTER_LABELS
export const ALL_FILTERS = Object.keys(FILTER_LABELS) as FilterKey[]

// Section 6.2: filters depend on the tab.
export const TAB_FILTERS: Record<Tab, FilterKey[]> = {
  all: ['year', 'department', 'author', 'grnti'],
  vkr: ['year', 'level', 'direction', 'department', 'supervisor', 'grnti'],
  coursework: ['year', 'courseKind', 'level', 'direction', 'discipline', 'department', 'supervisor', 'grnti'],
  publication: ['year', 'pubKind', 'indexing', 'journalList', 'venue', 'department', 'author', 'grnti', 'oecd', 'wos', 'asjc'],
  talk: ['year', 'confLevel', 'talkKind', 'format', 'department', 'author', 'grnti'],
}
