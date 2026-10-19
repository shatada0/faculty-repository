// Domain types — section 4 of the spec. API response shapes — section 7.

export type PersonKind = 'staff' | 'student' | 'graduate' | 'external'

export interface Person {
  id: string
  kind: PersonKind
  fullName: string
  department?: string
  position?: string
  academicDegree?: string
  academicTitle?: string
  orcid?: string
  affiliation?: string
  photoUrl?: string
  aliases?: string[] // name variants used for author matching (admin only)
  shortName?: string // "Иванов И. И.", from the server for author lists (admin only)
}

export interface AuthorRef {
  personId?: string
  displayName: string
  kind: PersonKind
}

export type WorkType = 'vkr' | 'coursework' | 'publication' | 'talk'
export type AccessLevel = 'full' | 'card_only' | 'hidden'
export type WorkStatus = 'draft' | 'published' | 'rejected'
export type FieldSource = 'table' | 'ai' | 'doi' | 'manual' | 'direction'
export type Language = 'ru' | 'en' | 'other'

// Classifiers as in eLIBRARY (ГРНТИ, ОЕСР), Web of Science categories, Scopus ASJC.
export type SubjectScheme = 'grnti' | 'oecd' | 'wos' | 'asjc'
export interface Subject { scheme: SubjectScheme; code: string; name: string }

export interface WorkBase {
  id: string
  type: WorkType
  title: string
  authors: AuthorRef[]
  year: number
  department?: string
  annotation?: string
  keywords: string[]
  language: Language
  accessLevel: AccessLevel
  hasFile: boolean
  externalUrl?: string
  thumbnailUrl?: string
  publishedAt?: string
  subjects?: Subject[]
}

export type DegreeLevel = 'bachelor' | 'specialist' | 'master'
export type CourseworkKind = 'work' | 'project'

export interface StudentWork extends WorkBase {
  type: 'vkr' | 'coursework'
  supervisors: AuthorRef[]
  level: DegreeLevel
  direction: { code: string; name: string }
  profile?: string
  discipline?: string
  pages?: number
  consent?: boolean // student's consent to open the full text
  kind?: CourseworkKind // coursework only: курсовая работа or курсовой проект
}

export type PublicationKind =
  | 'journal_article'
  | 'proceedings_paper'
  | 'abstract'
  | 'book_chapter'
  | 'monograph'
  | 'textbook'
  | 'other'

export type Indexing = 'scopus' | 'wos' | 'rinc' | 'vak' | 'core_rinc'

export interface Publication extends WorkBase {
  type: 'publication'
  kind: PublicationKind
  venue: string
  volume?: string
  issue?: string
  pages?: string
  publisher?: string
  doi?: string
  isbn?: string
  issn?: string
  indexing: Indexing[]
  relatedTalkId?: string
  // Read-only, from the server: citations (Crossref / Scopus / РИНЦ) and the journal's authority lists by ISSN.
  citations?: number
  journalLists?: string[] // "Белый список, уровень 2", "ВАК, К1", "Scopus Q2"
}

export type ConferenceLevel = 'international' | 'national' | 'regional' | 'university'
export type TalkKind = 'plenary' | 'section' | 'poster' | 'invited' | 'other'
export type TalkFormat = 'offline' | 'online' | 'hybrid'

export interface TalkEvent {
  name: string
  level: ConferenceLevel
  city?: string
  country?: string
  dateStart: string
  dateEnd?: string
  url?: string
}

export interface Talk extends WorkBase {
  type: 'talk'
  event: TalkEvent
  kind: TalkKind
  format: TalkFormat
  presenters: AuthorRef[]
  award?: string
  relatedPublicationId?: string
}

export type Work = StudentWork | Publication | Talk

// ---- display of a work, built by the server: the card and the work page only render it ----

export interface PersonLink { name: string; profileId?: string }
export interface ViewItem { text: string; href?: string; tone?: 'info' | 'warn' }
export interface DetailRow extends ViewItem { label: string; people?: PersonLink[] }

export interface WorkView {
  kind: string // "Статья в журнале", "Доклад (секционный)"
  authors: PersonLink[]
  byline: { label?: string; people: PersonLink[] }[] // card line: authors; "руководитель" …; "Докладчики:" …
  where: string // venue / event / direction line
  snippet?: string // annotation shown in lists
  meta: ViewItem[] // indexing, DOI, conference level, award, "только описание"
  details: DetailRow[] // work page "Сведения", empty rows omitted
  materials: { id: string; label: string }[] // approved materials, downloaded via /works/:id/materials/:mid
  fileButton?: string // "Открыть текст" / "Открыть материалы"; absent when the text is closed
  external?: { href: string; label: string }
  closedNote?: string
}

export type PublicWork = Work & { view: WorkView }

export interface FieldMeta {
  source: FieldSource
  confidence?: number
  lowConfidence?: boolean // set by the server: an AI value worth a second look
}

export type WorkAction = 'publish' | 'unpublish' | 'reject'

export type MaterialKind = 'text' | 'slides' | 'data' | 'other'
export type MaterialStatus = 'pending' | 'approved' | 'rejected'
/** A file an author (or moderator) attached to a work; authors' uploads wait for a moderator. */
export interface Material { id: string; name: string; size: number; kind: MaterialKind; status: MaterialStatus; uploadedBy: string; uploadedAt: string; rejectReason?: string | null }

// Admin responses carry every field (null when empty, as pydantic serializes); the editor sends null for cleared fields.
export interface WorkAdminMeta {
  status: WorkStatus
  importId?: string
  originalFileName?: string
  fieldMeta: Record<string, FieldMeta> // kept by the server: changed fields become "manual", DOI fills become "doi"
  rejectReason?: string
  duplicateOf?: string
  createdAt: string
  updatedAt: string
  // Computed by the server on every admin response; ignored on write.
  importFileName?: string
  errors: { field: string; message: string }[] // what blocks publishing; field is a dotted path: "event.dateEnd", "authors.0.displayName"
  issues: string[] // everything to look at before publishing: errors, unsure AI fields, unmatched authors
  actions: WorkAction[] // status transitions allowed now
  materials: Material[]
}

export type AdminWork = Work & WorkAdminMeta

export interface DoiConflict { field: string; label: string; current: string; incoming: string }

export interface UploadRule { accept: string[]; maxBytes: number }
export interface UploadLimits { archive: UploadRule; table: UploadRule; workFile: UploadRule; material: UploadRule }

/** A work in the author's "Мои работы". */
export interface MyWork { id: string; title: string; kind: string; year: number; public: boolean; canUpload: boolean; materials: Material[] }

export type ImportKind = 'archive' | 'table'
export type ImportStatus = 'uploading' | 'processing' | 'done' | 'failed'
export type ImportItemStatus = 'pending' | 'extracted' | 'filled' | 'published' | 'duplicate' | 'error'

export interface Import {
  id: string
  kind: ImportKind
  workType: WorkType
  fileName: string
  metaTableName?: string
  status: ImportStatus
  createdAt: string
  createdBy: string
  stats: { total: number; processed: number; filled: number; published: number; duplicates: number; errors: number; unmatched: number }
}

export interface ImportItem {
  ref: string
  title?: string
  status: ImportItemStatus
  error?: string
  workId?: string
  duplicateOf?: string
  unmatchedAuthors?: string[]
}

// ---- API shapes ----

export interface User {
  id: string
  name: string
  role: 'moderator' | 'admin' | 'author' // author = a person in the repository who uploads materials to own works
  personId?: string
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface Facet {
  value: string
  label: string
  count: number
}

export type Facets = Record<string, Facet[]>

export interface Department {
  name: string
  staffCount: number
  workCount: number
  people: { id: string; shortName: string }[] // first three by name
}

export interface AdminSummary {
  drafts: { total: number; withIssues: number; byType: Record<WorkType, number> }
  pendingMaterials: { workId: string; title: string; name: string }[]
}

export interface StatTile { value: number | string; label: string; note?: string }

/** Import table checked by the server before upload. */
export interface ImportPreview {
  header: string[]
  rows: { line: number; cells: string[]; missing: string[] }[] // first 10 data rows
  total: number
  bad: number // rows with missing required fields
  missingColumns: string[]
}

export interface Stats {
  byType: Record<WorkType, number>
  staff: number
}

export interface PersonCounts {
  publication: number
  talk: number
  vkr: number
  coursework: number
}

export type StaffListItem = Person & { counts: PersonCounts }

/** A tab of a person's page: the server says what it lists and which filters it has. */
export interface ProfileTab {
  key: string
  label: string
  count: number
  query: Record<string, string> // passed to /people/:id/works
  filters: { key: string; label: string; all: string; options: { value: string; label: string }[] }[]
}

/** Public page of a teacher, student or graduate. */
export interface PersonProfile extends Person {
  headline?: string // "доцент кафедры ИС", "Студент · Кафедра ИС"
  tiles: StatTile[]
  tabs: ProfileTab[]
  byYear: { year: number; publication: number; talk: number }[]
  coauthors: { id: string; fullName: string; count: number }[]
}

/** h-index and citations over the person's publications, all years or one year of works. */
export interface PersonMetrics { items: StatTile[]; years: number[] }

export type AdminPersonListItem = Person & { workCount: number }

export interface UnmatchedAuthor {
  displayName: string
  count: number
  workIds: string[]
  works: { id: string; title: string }[]
  suggestions: Person[]
}

export interface AdminPersonCard extends Person {
  works: { id: string; type: WorkType; title: string; year: number; status: WorkStatus }[]
}

export interface FileLink {
  url: string
  expiresAt: string
}

/** GET /works/suggest — search box suggestions; `parts` is the title split by the server, `hit` marks matched words. */
export interface Suggestions {
  works: { id: string; type: WorkType; title: string; year?: number; parts: { text: string; hit: boolean }[] }[]
  people: { id: string; fullName: string }[]
}
