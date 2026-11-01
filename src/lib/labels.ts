import type {
  AccessLevel, ConferenceLevel, CourseworkKind, DegreeLevel, FieldSource, ImportItemStatus, ImportStatus, Indexing,
  Language, MaterialKind, MaterialStatus, PersonKind, PublicationKind, SubjectScheme, TalkFormat, TalkKind, WorkStatus, WorkType,
} from '@/api/types'

export const workTypeLabels: Record<WorkType, string> = {
  vkr: 'ВКР',
  coursework: 'Курсовая',
  publication: 'Публикация',
  talk: 'Выступление',
}

export const workTypePlural: Record<WorkType, string> = {
  vkr: 'ВКР',
  coursework: 'Курсовые',
  publication: 'Публикации',
  talk: 'Выступления',
}

export const pubKindLabels: Record<PublicationKind, string> = {
  journal_article: 'Статья в журнале',
  proceedings_paper: 'Статья в сборнике',
  abstract: 'Тезисы',
  book_chapter: 'Глава в книге',
  monograph: 'Монография',
  textbook: 'Учебное пособие',
  other: 'Другое',
}

export const courseworkKindLabels: Record<CourseworkKind, string> = {
  work: 'Курсовая работа',
  project: 'Курсовой проект',
}

export const subjectSchemeLabels: Record<SubjectScheme, string> = {
  grnti: 'ГРНТИ',
  oecd: 'ОЕСР',
  wos: 'Web of Science',
  asjc: 'Scopus (ASJC)',
}

export const materialKindLabels: Record<MaterialKind, string> = {
  text: 'Текст',
  slides: 'Презентация',
  data: 'Данные, код',
  other: 'Другое',
}

export const materialStatusLabels: Record<MaterialStatus, string> = {
  pending: 'На проверке',
  approved: 'Опубликован',
  rejected: 'Отклонён',
}

export const indexingLabels: Record<Indexing, string> = {
  scopus: 'Scopus',
  wos: 'WoS',
  vak: 'ВАК',
  rinc: 'РИНЦ',
  core_rinc: 'Ядро РИНЦ',
}

export const confLevelLabels: Record<ConferenceLevel, string> = {
  international: 'Международная',
  national: 'Всероссийская',
  regional: 'Региональная',
  university: 'Вузовская',
}

export const talkKindLabels: Record<TalkKind, string> = {
  plenary: 'Пленарный',
  section: 'Секционный',
  poster: 'Стендовый',
  invited: 'Приглашённый',
  other: 'Другой',
}

export const formatLabels: Record<TalkFormat, string> = {
  offline: 'Очно',
  online: 'Онлайн',
  hybrid: 'Смешанный',
}

export const levelLabels: Record<DegreeLevel, string> = {
  bachelor: 'Бакалавриат',
  specialist: 'Специалитет',
  master: 'Магистратура',
}

export const accessLabels: Record<AccessLevel, string> = {
  full: 'Карточка и текст',
  card_only: 'Только карточка',
  hidden: 'Скрыта',
}

export const statusLabels: Record<WorkStatus, string> = {
  draft: 'Черновик',
  published: 'Опубликована',
  rejected: 'Отклонена',
}

export const personKindLabels: Record<PersonKind, string> = {
  staff: 'Преподаватель',
  student: 'Студент',
  graduate: 'Выпускник',
  external: 'Внешний автор',
}

export const sourceLabels: Record<FieldSource, string> = {
  table: 'таблица',
  ai: 'ИИ',
  doi: 'DOI',
  isbn: 'ISBN',
  bibliography: 'BibTeX/RIS',
  direction: 'направление подготовки',
  manual: 'вручную',
}

export const languageLabels: Record<Language, string> = {
  ru: 'Русский',
  en: 'Английский',
  other: 'Другой',
}

export const importStatusLabels: Record<ImportStatus, string> = {
  uploading: 'Загрузка',
  processing: 'Обработка',
  done: 'Готово',
  failed: 'Ошибка',
}

export const importItemStatusLabels: Record<ImportItemStatus, string> = {
  pending: 'В очереди',
  extracted: 'Текст извлечён',
  filled: 'На проверке',
  published: 'Опубликовано',
  duplicate: 'Дубль',
  error: 'Ошибка',
}


/** Work fields by form path: error summaries and DOI conflicts. */
export const fieldLabels: Record<string, string> = {
  title: 'Название', authors: 'Авторы', year: 'Год', department: 'Кафедра', annotation: 'Аннотация',
  keywords: 'Ключевые слова', language: 'Язык', supervisors: 'Руководитель', level: 'Уровень',
  direction: 'Направление', profile: 'Профиль', discipline: 'Дисциплина', pages: 'Страницы',
  kind: 'Вид', venue: 'Журнал / сборник', volume: 'Том', issue: 'Номер', publisher: 'Издательство',
  doi: 'DOI', issn: 'ISSN', isbn: 'ISBN', indexing: 'Индексация', 'event.name': 'Конференция',
  'event.level': 'Уровень конференции', 'event.dateStart': 'Дата начала', presenters: 'Докладчики', subjects: 'Классификаторы',
}
