// Endpoint functions — one per row of section 7.
import { api, download, qs, upload, type Query } from './client'
import type {
  AccessLevel, AdminPersonCard, AdminPersonListItem, AdminSummary, AdminWork, Department, DoiConflict, Facets, FileLink, Import, ImportItem,
  ImportPreview, Material, OrcidImport, MaterialKind, MaterialStatus, MyWork, Page, Person, PersonMetrics, PersonPatch, PersonProfile, PublicWork, StaffListItem, Stats,
  Subject, SubjectScheme, Suggestions, TalkEvent, UnmatchedAuthor, UploadLimits, User, WorkType,
} from './types'

export { ApiError, reportError } from './client'

// public
export const getStats = () => api<Stats>('/stats')
export const getWorks = (q: Query) => api<Page<PublicWork>>(`/works${qs(q)}`)
export const getLatestWorks = () => api<PublicWork[]>('/works/latest')
export const getFacets = (q: Query) => api<Facets>(`/works/facets${qs(q)}`)
export const getSuggestions = (q: string) => api<Suggestions>(`/works/suggest${qs({ q })}`)
export const getWork = (id: string) => api<PublicWork>(`/works/${id}`)
export const getWorkFile = (id: string) => api<FileLink>(`/works/${id}/file`)
export const getCitation = (id: string) => api<{ gost: string }>(`/works/${id}/citation`)
export const getStaff = (q: Query) => api<{ items: StaffListItem[] }>(`/people${qs(q)}`)
export const getPerson = (id: string) => api<PersonProfile>(`/people/${id}`)
export const getPersonMetrics = (id: string, q: Query) => api<PersonMetrics>(`/people/${id}/metrics${qs(q)}`)
export const getMaterialLink = (workId: string, id: string) => api<FileLink>(`/works/${workId}/materials/${id}`)
export const getPersonWorks = (id: string, q: Query) => api<{ items: PublicWork[]; total: number }>(`/people/${id}/works${qs(q)}`)
export const getDepartments = () => api<{ items: Department[] }>('/departments')

// auth
export const login = (login: string, password: string) =>
  api<{ user: User }>('/auth/login', { method: 'POST', json: { login, password } })
export const logout = () => api<void>('/auth/logout', { method: 'POST' })
export const getMe = () => api<{ user: User }>('/auth/me')

// author
export const getMyWorks = () => api<{ items: MyWork[] }>('/my/works')
export const uploadMaterial = (workId: string, file: File, kind: MaterialKind, onProgress?: (f: number) => void) => {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  return upload<Material>(`/my/works/${workId}/materials`, form, onProgress)
}
export const deleteMyMaterial = (workId: string, id: string) => api<void>(`/my/works/${workId}/materials/${id}`, { method: 'DELETE' })

// moderator
export const createImport = (form: FormData, onProgress?: (f: number) => void, signal?: AbortSignal) =>
  upload<Import>('/admin/imports', form, onProgress, signal)
export const getImports = () => api<Import[]>('/admin/imports')
export const getImport = (id: string, q: Query = {}) => api<{ import: Import; items: ImportItem[] }>(`/admin/imports/${id}${qs(q)}`)
/** Files that failed (the AI was down, the network dropped) go back to the worker. */
export const retryImport = (id: string) => api<{ import: Import; items: ImportItem[] }>(`/admin/imports/${id}/retry`, { method: 'POST' })
export const previewImportTable = (workType: WorkType, file: File) => {
  const form = new FormData()
  form.append('workType', workType)
  form.append('file', file)
  return api<ImportPreview>('/admin/imports/preview', { method: 'POST', body: form })
}
export const downloadTemplate = (t: WorkType) => download(`/admin/templates/${t}`, `template-${t}.csv`)
export const getTemplateColumns = (t: WorkType) => api<{ name: string; required: boolean }[]>(`/admin/templates/${t}/columns`)

export const getAdminSummary = () => api<AdminSummary>('/admin/summary')
export const getAdminWorks = (q: Query) => api<{ items: AdminWork[]; total: number }>(`/admin/works${qs(q)}`)
// Exports with the list's filters. Tables go through download() so errors are shown; the zip is a plain link because
// it can be gigabytes and a blob would hold all of it in memory.
export const exportWorks = (q: Query, format: 'xlsx' | 'csv') => download(`/admin/exports/works${qs({ ...q, format })}`, `works.${format}`)
export const worksZipUrl = (q: Query) => `/api/admin/exports/works.zip${qs(q)}`
// GOST lists of works for reports: public docx, a plain link like the zip
export const personWorksDocxUrl = (id: string, year?: string) => `/api/people/${id}/works.docx${qs({ year })}`
export const departmentWorksDocxUrl = (department: string) => `/api/departments/works.docx${qs({ department })}`
export const exportPeople = () => download('/admin/exports/people', 'people.xlsx')
export const bulkWorks = (ids: string[], action: 'publish' | 'access', accessLevel?: AccessLevel) =>
  api<{ done: number; skipped: string[] }>('/admin/works/bulk', { method: 'POST', json: { ids, action, accessLevel } })
/** Next draft of the review queue after `after` (same filters as the queue). */
export const getNextInQueue = (q: Query) => api<{ id: string | null }>(`/admin/works/next${qs(q)}`)
/** Works of the other type sharing an author, for the "related talk / publication" select. */
export const getRelatedOptions = (q: Query) => api<{ id: string; label: string }[]>(`/admin/works/related-options${qs(q)}`)
/** A new work of this type with the server's defaults (not saved). */
export const getWorkTemplate = (type: WorkType) => api<AdminWork>(`/admin/works/template${qs({ type })}`)
export const createWork = (w: Record<string, unknown>) => api<AdminWork>('/admin/works', { method: 'POST', json: w })
export const getAdminWork = (id: string) => api<AdminWork>(`/admin/works/${id}`)
export const patchWork = (id: string, w: Record<string, unknown>) => api<AdminWork>(`/admin/works/${id}`, { method: 'PATCH', json: w })
export const uploadWorkFile = (id: string, file: File, onProgress?: (f: number) => void) => {
  const form = new FormData()
  form.append('file', file)
  return upload<AdminWork>(`/admin/works/${id}/file`, form, onProgress)
}
export const publishWork = (id: string) => api<AdminWork>(`/admin/works/${id}/publish`, { method: 'POST' })
export const rejectWork = (id: string, reason: string) => api<AdminWork>(`/admin/works/${id}/reject`, { method: 'POST', json: { reason } })
export const unpublishWork = (id: string) => api<AdminWork>(`/admin/works/${id}/unpublish`, { method: 'POST' })
export const getAdminWorkFile = (id: string) => api<FileLink>(`/admin/works/${id}/file`)
/** Fill a saved work from DOI: empty fields are filled; filled ones that differ come back as conflicts unless listed in `overwrite`. */
export const fillFromDoi = (id: string, doi: string, overwrite: string[] = []) =>
  api<{ work: AdminWork; conflicts: DoiConflict[] }>(`/admin/works/${id}/doi`, { method: 'POST', json: { doi, overwrite } })
export const getUploadLimits = () => api<UploadLimits>('/upload-limits')
export const getAdminMaterialLink = (workId: string, id: string) => api<FileLink>(`/admin/works/${workId}/materials/${id}`)
export const reviewMaterial = (workId: string, id: string, status: MaterialStatus, reason = '') =>
  api<AdminWork>(`/admin/works/${workId}/materials/${id}`, { method: 'POST', json: { status, reason } })
/** Search a classifier (ГРНТИ, ОЕСР, WoS, ASJC) by code or name. */
export const searchClassifier = (scheme: SubjectScheme, q: string) => api<Subject[]>(`/admin/classifiers/${scheme}${qs({ q })}`)
export const getEvents = (q: string) => api<TalkEvent[]>(`/admin/events${qs({ q })}`)

export const getAdminPeople = (q: Query) => api<{ items: AdminPersonListItem[]; total: number }>(`/admin/people${qs(q)}`)
export const getUnmatched = () => api<{ items: UnmatchedAuthor[]; total: number }>(`/admin/people${qs({ unmatched: true })}`)
export const createPerson = (p: Partial<Person>) => api<Person>('/admin/people', { method: 'POST', json: p })
export const getAdminPerson = (id: string) => api<AdminPersonCard>(`/admin/people/${id}`)
export const patchPerson = (id: string, p: PersonPatch) => api<AdminPersonCard>(`/admin/people/${id}`, { method: 'PATCH', json: p })
export const importOrcid = (id: string) => api<OrcidImport>(`/admin/people/${id}/orcid-import`, { method: 'POST' })
export const mergePerson = (id: string, fromId: string) => api<AdminPersonCard>(`/admin/people/${id}/merge`, { method: 'POST', json: { fromId } })
export const matchAuthor = (displayName: string, personId: string | null, kind: Person['kind']) =>
  api<Person>('/admin/people/match', { method: 'POST', json: { displayName, personId, kind } })
