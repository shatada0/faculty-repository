// Mock of the backend's publish-time validation (section 6.10; pydantic on the real server).
// Drafts may be saved incomplete; publishing requires these.
import { z } from 'zod'
import type { WorkType } from '@/api/types'

const maxYear = new Date().getFullYear() + 1
const opt = z.string().optional()

export const authorRefSchema = z.object({
  personId: z.string().optional(),
  displayName: z.string().trim().min(1, 'Укажите ФИО'),
  kind: z.enum(['staff', 'student', 'graduate', 'external']),
})

const base = z.looseObject({
  id: z.string().optional(),
  title: z.string().trim().min(1, 'Укажите название'),
  authors: z.array(authorRefSchema).min(1, 'Добавьте хотя бы одного автора'),
  year: z.number({ error: 'Укажите год' }).int('Год — целое число').min(1990, 'Год не раньше 1990').max(maxYear, `Год не позже ${maxYear}`),
  department: opt,
  annotation: opt,
  keywords: z.array(z.string()),
  language: z.enum(['ru', 'en', 'other']),
  accessLevel: z.enum(['full', 'card_only', 'hidden']),
  hasFile: z.boolean(),
  externalUrl: opt,
})

const student = base.extend({
  supervisors: z.array(authorRefSchema).min(1, 'Укажите научного руководителя'),
  level: z.enum(['bachelor', 'specialist', 'master'], { error: 'Выберите уровень' }),
  direction: z.object({
    code: z.string().trim().regex(/^\d{2}\.\d{2}\.\d{2}$/, 'Код направления в формате NN.NN.NN'),
    name: z.string(),
  }),
  department: z.string().trim().min(1, 'Укажите кафедру'),
  profile: opt,
  discipline: opt,
  pages: z.number().int().positive('Число страниц больше нуля').optional(),
  consent: z.boolean().optional(),
})

const noFullWithoutConsent = (w: { accessLevel: string; consent?: boolean }) => w.accessLevel !== 'full' || !!w.consent
const consentIssue = { path: ['accessLevel'], message: 'Нет согласия студента на открытый доступ к тексту' }

export const vkrSchema = student.extend({ type: z.literal('vkr') }).refine(noFullWithoutConsent, consentIssue)
export const courseworkSchema = student.extend({ type: z.literal('coursework') }).refine(noFullWithoutConsent, consentIssue)

export const publicationSchema = base.extend({
  type: z.literal('publication'),
  kind: z.enum(['journal_article', 'proceedings_paper', 'abstract', 'book_chapter', 'monograph', 'textbook', 'other'], { error: 'Выберите вид публикации' }),
  venue: z.string().trim(),
  volume: opt,
  issue: opt,
  pages: opt,
  publisher: opt,
  doi: z.union([z.literal(''), z.string().trim().regex(/^10\.\d{4,9}\/\S+$/, 'DOI в формате 10.xxxx/...')]).optional(),
  isbn: opt,
  issn: opt,
  indexing: z.array(z.enum(['scopus', 'wos', 'rinc', 'vak', 'core_rinc'])),
  relatedTalkId: opt,
}).superRefine((w, ctx) => {
  const book = w.kind === 'monograph' || w.kind === 'textbook'
  if (book && !w.publisher?.trim()) ctx.addIssue({ code: 'custom', path: ['publisher'], message: 'Укажите издательство' })
  if (!book && !w.venue) ctx.addIssue({ code: 'custom', path: ['venue'], message: 'Укажите журнал или сборник' })
})

export const talkSchema = base.extend({
  type: z.literal('talk'),
  event: z.object({
    name: z.string().trim().min(1, 'Укажите конференцию'),
    level: z.enum(['international', 'national', 'regional', 'university'], { error: 'Выберите уровень конференции' }),
    city: opt,
    country: opt,
    dateStart: z.string().min(1, 'Укажите дату начала'),
    dateEnd: opt,
    url: opt,
  }),
  kind: z.enum(['plenary', 'section', 'poster', 'invited', 'other']),
  format: z.enum(['offline', 'online', 'hybrid']),
  presenters: z.array(authorRefSchema).min(1, 'Отметьте хотя бы одного докладчика'),
  award: opt,
  relatedPublicationId: opt,
}).refine((w) => !w.event.dateEnd || w.event.dateEnd >= w.event.dateStart, {
  path: ['event', 'dateEnd'],
  message: 'Дата окончания не раньше даты начала',
})

export const workSchema = z.discriminatedUnion('type', [vkrSchema, courseworkSchema, publicationSchema, talkSchema])

export const schemaFor = (type: WorkType) =>
  ({ vkr: vkrSchema, coursework: courseworkSchema, publication: publicationSchema, talk: talkSchema })[type]
