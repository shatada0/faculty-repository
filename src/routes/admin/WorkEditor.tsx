import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useBlocker, useLocation, useNavigate, useParams, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Controller, FormProvider, useForm, useFormContext, type FieldErrors } from 'react-hook-form'
import {
  ApiError, createWork, fillFromDoi, fillFromIsbn, getAdminMaterialLink, getAdminWork, getAdminWorkFile, getEvents, getNextInQueue, getRelatedOptions,
  getUploadLimits, getWorkTemplate, patchWork, publishWork, refreshAll, rejectWork, reviewMaterial, unpublishWork, uploadWorkFile,
} from '@/api'
import type { AdminWork, AuthorRef, DoiConflict, FieldMeta, MaterialStatus, SubjectScheme, TalkEvent, WorkType } from '@/api/types'
import { AuthorsEditor } from '@/components/AuthorsEditor'
import { FileDrop } from '@/components/FileDrop'
import { NotFound, useTitle } from '@/components/Layout'
import { SubjectsEditor } from '@/components/SubjectsEditor'
import { FieldSourceBadge, MaterialStatusBadge, StatusBadge, TypeIcon } from '@/components/WorkCard'
import { Alert, Button, Card, Dialog, ErrorState, Field, Input, Select, Spinner, Tabs, Textarea, cn, useDebounced } from '@/components/ui'
import { formatSize } from '@/lib/format'
import {
  accessLabels, confLevelLabels, courseworkKindLabels, fieldLabels, formatLabels, indexingLabels, languageLabels, levelLabels, materialKindLabels,
  pubKindLabels, talkKindLabels, workTypeLabels,
} from '@/lib/labels'
import { openSigned } from '@/lib/open'
import { useDepartments } from '@/routes/People'

const TYPES: WorkType[] = ['vkr', 'coursework', 'publication', 'talk']
// Journal-level classifiers (WoS, Scopus) only make sense for publications.
const SCHEMES: SubjectScheme[] = ['grnti', 'oecd']
const PUBLICATION_SCHEMES: SubjectScheme[] = ['grnti', 'oecd', 'wos', 'asjc']
// ponytail: form values are typed loosely (Work is a union); the server validates and returns field errors.
type FormValues = Record<string, any>
/** Fill by an identifier: DOI from Crossref, ISBN from Crossref or Open Library — the same conflict flow. */
type By = 'doi' | 'isbn'
type DoiState = { id: string; by: By; value: string; conflicts: DoiConflict[] }
const fillBy = (by: By) => (by === 'doi' ? fillFromDoi : fillFromIsbn)

/** Server work → form values. The server sends every field (null when empty); inputs want ''. The student page count stays a number field. */
function toForm(w: AdminWork): FormValues {
  const v = JSON.parse(JSON.stringify(w), (_, x) => x ?? '')
  if (v.pages === '' && (w.type === 'vkr' || w.type === 'coursework')) delete v.pages
  return v
}

const sourceKey = (name: string) => (name.startsWith('event.') ? name.split('.').slice(0, 2).join('.') : name.split('.')[0])
const errAt = (errors: FieldErrors, path: string): string | undefined => {
  const e = path.split('.').reduce<any>((o, k) => o?.[k], errors)
  return e?.message ?? e?.root?.message
}

const SourcesCtx = createContext<Record<string, FieldMeta>>({})

/** Form field with label, source badge, low-confidence highlight and error. */
function F({ name, label, hint, className, children }: { name: string; label: string; hint?: ReactNode; className?: string; children: (id: string, describedBy?: string) => ReactNode }) {
  const { formState: { errors } } = useFormContext()
  const src = useContext(SourcesCtx)[sourceKey(name)]
  const low = !!src?.lowConfidence
  return (
    <Field label={label} error={errAt(errors, name)} hint={hint}
      extra={src && <FieldSourceBadge source={src.source} confidence={src.confidence} low={low} />}
      className={cn(low && '[&_input]:border-2 [&_input]:border-warn [&_textarea]:border-2 [&_textarea]:border-warn [&_select]:border-2 [&_select]:border-warn', className)}>
      {children}
    </Field>
  )
}

function KeywordsInput({ value, onChange, id }: { value: string[]; onChange: (v: string[]) => void; id: string }) {
  const [text, setText] = useState(value.join(', '))
  useEffect(() => setText(value.join(', ')), [value])
  return <Input id={id} value={text} onChange={(e) => setText(e.target.value)} placeholder="через запятую"
    onBlur={() => onChange(text.split(',').map((s) => s.trim()).filter(Boolean))} />
}

function PdfPane({ id }: { id: string }) {
  const q = useQuery({ queryKey: ['admin-work-file', id], queryFn: () => getAdminWorkFile(id), staleTime: 5 * 60_000 })
  if (q.isPending) return <Spinner label="Загрузка файла…" />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  // The browser may refuse to show the file inline (blocked third-party frame, untrusted certificate): a link always works.
  return (
    <div className="space-y-1">
      <a href={q.data.url} target="_blank" rel="noreferrer" className="text-sm">Открыть файл в новой вкладке ↗</a>
      <iframe src={q.data.url} title="Текст работы" className="h-[75vh] w-full border border-rule bg-white" />
    </div>
  )
}

function TypeChooser() {
  const [, setSp] = useSearchParams()
  return (
    <div className="space-y-4">
      <h1 className="text-4xl font-bold">Новая работа</h1>
      <p className="text-ink-2">Выберите тип работы</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {TYPES.map((t) => (
          <button key={t} type="button" onClick={() => setSp({ type: t })} className="flex items-center gap-3 border border-rule bg-white p-4 text-left hover:border-brand">
            <TypeIcon type={t} className="h-6 w-6 text-ink" /><span className="text-lg font-bold">{workTypeLabels[t]}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function WorkEditor() {
  const { id } = useParams()
  const [sp] = useSearchParams()
  const type = sp.get('type') as WorkType | null
  const newType = !id && type && TYPES.includes(type) ? type : null
  const saved = useQuery({ queryKey: ['admin-work', id], queryFn: () => getAdminWork(id!), enabled: !!id })
  const template = useQuery({ queryKey: ['admin-work-template', newType], queryFn: () => getWorkTemplate(newType!), enabled: !!newType, staleTime: Infinity })
  useTitle(id ? saved.data?.title || saved.data?.originalFileName : 'Новая работа')
  if (!id && !newType) return <TypeChooser />
  const q = id ? saved : template
  if (q.isError) return q.error instanceof ApiError && q.error.status === 404 ? <NotFound text="Работа не найдена" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (q.isPending) return <Spinner />
  return <Editor key={id ?? `new-${newType}`} work={id ? q.data : undefined} initial={q.data} />
}

/** `work` is the saved work; a new one starts from the server's `initial` template. */
function Editor({ work, initial }: { work?: AdminWork; initial: AdminWork }) {
  const navigate = useNavigate()
  const [sp] = useSearchParams()
  const qc = useQueryClient()
  const departments = useDepartments().data?.items ?? []
  const limits = useQuery({ queryKey: ['upload-limits'], queryFn: getUploadLimits, staleTime: Infinity })
  const type = initial.type
  const form = useForm<FormValues>({ defaultValues: toForm(initial) })
  const { register, control, watch, setValue, getValues, formState, reset, resetField, setError, clearErrors } = form
  const [busy, setBusy] = useState<string>()
  const location = useLocation()
  const [message, setMessage] = useState<{ tone: 'blue' | 'red'; text: string } | undefined>(
    location.state?.message ? { tone: 'blue', text: location.state.message } : undefined,
  )
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [doiState, setDoiState] = useState<DoiState | null>(location.state?.doi ?? null)
  const [pane, setPane] = useState<'file' | 'card'>('card')
  const bypass = useRef(false)
  const createdId = useRef<string>(undefined) // a new work saved once is patched afterwards, never created twice
  const isStudent = type === 'vkr' || type === 'coursework'

  // Field errors come from the server: shown on load and after every save.
  const showErrors = (w: AdminWork) => w.errors.forEach((e) => setError(e.field, { message: e.message }))
  useEffect(() => { if (work) showErrors(work) }, [work])

  // Editing a field hides its error until the next save. Field sources ("вручную", "по DOI") are kept by the server.
  useEffect(() => {
    const sub = watch((_, { name, type: ev }) => { if (ev === 'change' && name) clearErrors(sourceKey(name)) })
    return () => sub.unsubscribe()
  }, [watch])

  // ---- leaving with unsaved changes ----
  const dirty = formState.isDirty
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && !bypass.current && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm('Есть несохранённые изменения. Уйти без сохранения?')) blocker.proceed()
    else blocker.reset()
  }, [blocker])
  useEffect(() => {
    if (!dirty) return
    const h = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  const refresh = (saved: AdminWork) => {
    qc.setQueryData(['admin-work', saved.id], saved)
    // publish, materials and file bump updatedAt too: without it the next save of the open form would be refused as stale
    resetField('updatedAt', { defaultValue: saved.updatedAt })
    refreshAll(qc)
  }

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label)
    setMessage(undefined)
    try {
      return await fn()
    } catch (e) {
      setMessage({ tone: 'red', text: e instanceof Error ? e.message : 'Ошибка' })
    } finally {
      setBusy(undefined)
    }
  }

  /** Take the server's version of the work: cache, form values, field errors. */
  function apply(w: AdminWork): AdminWork {
    refresh(w)
    reset(toForm(w))
    showErrors(w)
    return w
  }

  async function save(): Promise<AdminWork> {
    const id = work?.id ?? createdId.current
    const saved = apply(id ? await patchWork(id, getValues()) : await createWork(getValues()))
    createdId.current = saved.id
    return saved
  }

  /** A new work gets its own URL after the first save; the message (and DOI conflicts) travel with the navigation. */
  function done(saved: AdminWork, text: string, doi?: DoiState) {
    if (work) {
      setMessage({ tone: 'blue', text })
      if (doi) setDoiState(doi)
      return
    }
    bypass.current = true
    navigate(`/admin/works/${saved.id}`, { replace: true, state: { message: text, doi } })
  }

  const onSave = () => run('save', async () => done(await save(), 'Сохранено'))
  const onPublish = () => run('publish', async () => {
    const saved = await save()
    if (saved.errors.length) return done(saved, 'Сохранено как черновик. Исправьте ошибки, чтобы опубликовать')
    const pub = await publishWork(saved.id)
    refresh(pub)
    done(pub, 'Работа опубликована и появилась в каталоге')
  })
  const onUnpublish = () => run('unpublish', async () => { refresh(await unpublishWork(work!.id)); setMessage({ tone: 'blue', text: 'Снято с публикации' }) })
  const onReject = () => run('reject', async () => {
    refresh(await rejectWork(work!.id, reason.trim()))
    setRejecting(false)
    setMessage({ tone: 'blue', text: 'Работа отклонена' })
  })
  const onMaterial = (mid: string, st: MaterialStatus) => {
    // the author sees the reason in «Мои работы»; Cancel keeps the material as it is
    const reason = st === 'rejected' ? window.prompt('Причина отклонения (её увидит автор)', '') : ''
    if (reason === null) return
    run('material', async () => { refresh(await reviewMaterial(work!.id, mid, st, reason)) })
  }
  const onNext = () => run('next', async () => {
    const { id: nextId } = await getNextInQueue({ after: work?.id, type: sp.get('type'), importId: sp.get('importId') })
    if (!nextId) return setMessage({ tone: 'blue', text: 'В очереди больше нет черновиков' })
    navigate(`/admin/works/${nextId}?${sp}`)
  })

  // ---- DOI / ISBN: the server fills the saved work and reports fields it would overwrite ----
  const onLookup = (by: By) => run(by, async () => {
    const value = String(getValues(by) ?? '')
    const r = await fillBy(by)((await save()).id, value)
    apply(r.work)
    const conflicts = r.conflicts.length ? { id: r.work.id, by, value, conflicts: r.conflicts } : undefined
    const what = by === 'doi' ? 'DOI' : 'ISBN'
    done(r.work, conflicts ? `Пустые поля заполнены по ${what}` : `Поля заполнены по ${what}`, conflicts)
  })
  const onOverwrite = (d: DoiState) => run(d.by, async () => {
    apply((await fillBy(d.by)(d.id, d.value, d.conflicts.map((c) => c.field))).work)
    setDoiState(null)
    setMessage({ tone: 'blue', text: `Поля перезаписаны по ${d.by === 'doi' ? 'DOI' : 'ISBN'}` })
  })

  // ---- related work options ----
  const authors: AuthorRef[] = watch('authors') ?? []
  const relatedType = type === 'publication' ? 'talk' : type === 'talk' ? 'publication' : null
  const relatedKey = type === 'publication' ? 'relatedTalkId' : 'relatedPublicationId'
  const relatedQuery = { type: relatedType, author: authors.flatMap((a) => (a.personId ? [a.personId] : [])), include: watch(relatedKey) }
  const related = useQuery({
    queryKey: ['admin-works', 'related', relatedQuery],
    queryFn: () => getRelatedOptions(relatedQuery),
    enabled: !!relatedType,
    placeholderData: keepPreviousData,
  })

  // ---- conference autocomplete ----
  const eventName: string = watch('event.name') ?? ''
  const eventQ = useDebounced(eventName)
  const events = useQuery({ queryKey: ['admin-events', eventQ], queryFn: () => getEvents(eventQ), enabled: type === 'talk' && eventQ.length >= 3 })
  const pickEvent = (name: string) => {
    const ev = events.data?.find((e: TalkEvent) => e.name === name)
    if (!ev) return
    for (const k of ['level', 'city', 'country', 'url'] as const) if (ev[k] && !getValues(`event.${k}`)) setValue(`event.${k}`, ev[k], { shouldDirty: true, shouldValidate: true })
  }

  const hasFile = !!work?.hasFile
  const status = work?.status
  const actions = (work ?? initial).actions
  const inQueue = sp.get('queue') === '1'
  const errors = formState.errors

  const uploadFile = (f: File | null, err?: string) => {
    if (err) return setMessage({ tone: 'red', text: err })
    if (!f || !work) return
    run('upload', async () => {
      refresh(await uploadWorkFile(work.id, f))
      qc.invalidateQueries({ queryKey: ['admin-work-file', work.id] })
    })
  }

  const formEl = (
    <div className="space-y-6">
      {/* DOI first: one click fills the rest of a publication */}
      {type === 'publication' && (
        <F name="doi" label="DOI" hint={import.meta.env.VITE_USE_MOCKS === 'true' ? 'Для демо: 10.12345/kim.2026.0142, 10.12345/jads.2025.118, 10.12345/pi.2026.07' : 'Введите DOI — название, авторов, журнал и год заполнит Crossref'}>{(id, d) => (
          <div className="flex gap-2">
            <Input id={id} aria-describedby={d} placeholder="10.xxxx/..." {...register('doi')} aria-invalid={!!errors.doi} />
            <Button variant="outline" onClick={() => onLookup('doi')} loading={busy === 'doi'}>Заполнить по DOI</Button>
          </div>
        )}</F>
      )}
      <section className="space-y-4">
        <F name="title" label="Название">{(id, d) => <Textarea id={id} aria-describedby={d} rows={3} {...register('title')} aria-invalid={!!errors.title} />}</F>
        <F name="authors" label="Авторы (порядок как в работе)">{() => (
          <AuthorsEditor name="authors" label="Авторы" defaultKind={isStudent ? 'student' : 'staff'} onChanged={() => clearErrors('authors')} />
        )}</F>
        {isStudent && (
          <F name="supervisors" label="Научный руководитель">{() => (
            <AuthorsEditor name="supervisors" label="Руководители" defaultKind="staff" onChanged={() => clearErrors('supervisors')} />
          )}</F>
        )}
        <div className="grid gap-4 sm:grid-cols-3">
          <F name="year" label="Год">{(id) => <Input id={id} type="number" inputMode="numeric" {...register('year', { setValueAs: (v) => (v === '' || v == null ? undefined : Number(v)) })} aria-invalid={!!errors.year} />}</F>
          {/* only reference departments: the server rejects anything else */}
          {/* key: options arrive after mount, and a registered select keeps showing «—» until it is mounted again */}
          <F name="department" label="Кафедра" className="sm:col-span-2">{(id) => (
            <Select key={departments.length} id={id} {...register('department')} aria-invalid={!!errors.department}><option value="">—</option>{departments.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}</Select>
          )}</F>
        </div>
      </section>

      {isStudent && (
        <section className="grid gap-4 sm:grid-cols-2">
          <F name="level" label="Уровень">{(id) => (
            <Select id={id} {...register('level')}>{Object.entries(levelLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="direction.code" label="Код направления" hint="Формат NN.NN.NN">{(id) => <Input id={id} placeholder="09.03.01" {...register('direction.code')} aria-invalid={!!errAt(errors, 'direction.code')} />}</F>
          <F name="direction.name" label="Название направления" className="sm:col-span-2">{(id) => <Input id={id} {...register('direction.name')} />}</F>
          <F name="profile" label="Профиль">{(id) => <Input id={id} {...register('profile')} />}</F>
          {type === 'coursework' && <F name="discipline" label="Дисциплина">{(id) => <Input id={id} {...register('discipline')} />}</F>}
          {type === 'coursework' && <F name="kind" label="Курсовая работа или проект">{(id) => (
            <Select id={id} {...register('kind')}>{Object.entries(courseworkKindLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>}
          <F name="pages" label="Страниц">{(id) => <Input id={id} type="number" {...register('pages', { setValueAs: (v) => (v === '' || v == null ? undefined : Number(v)) })} />}</F>
        </section>
      )}

      {type === 'publication' && (
        <section className="grid gap-4 sm:grid-cols-2">
          <F name="kind" label="Вид публикации">{(id) => (
            <Select id={id} {...register('kind')}>{Object.entries(pubKindLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="venue" label="Журнал или сборник">{(id) => <Input id={id} {...register('venue')} aria-invalid={!!errors.venue} />}</F>
          <div className="grid grid-cols-3 gap-3 sm:col-span-2">
            <F name="volume" label="Том">{(id) => <Input id={id} {...register('volume')} />}</F>
            <F name="issue" label="Номер">{(id) => <Input id={id} {...register('issue')} />}</F>
            <F name="pages" label="Страницы">{(id) => <Input id={id} placeholder="15–24" {...register('pages')} />}</F>
          </div>
          <F name="publisher" label="Издательство">{(id) => <Input id={id} {...register('publisher')} aria-invalid={!!errors.publisher} />}</F>
          <div className="grid grid-cols-2 gap-3">
            <F name="issn" label="ISSN">{(id) => <Input id={id} {...register('issn')} />}</F>
            <F name="isbn" label="ISBN" hint="Книга: заполнить по Crossref или Open Library">{(id, d) => (
              <div className="flex gap-2">
                <Input id={id} aria-describedby={d} {...register('isbn')} aria-invalid={!!errors.isbn} />
                <Button variant="outline" onClick={() => onLookup('isbn')} loading={busy === 'isbn'}>По ISBN</Button>
              </div>
            )}</F>
          </div>
          <F name="indexing" label="Индексация" className="sm:col-span-2">{() => (
            <Controller control={control} name="indexing" render={({ field }) => (
              <div className="flex flex-wrap gap-3">
                {Object.entries(indexingLabels).map(([v, l]) => (
                  <label key={v} className="flex items-center gap-1.5 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-brand" checked={field.value?.includes(v)}
                      onChange={(e) => field.onChange(e.target.checked ? [...field.value, v] : field.value.filter((x: string) => x !== v))} />{l}
                  </label>
                ))}
              </div>
            )} />
          )}</F>
          {work?.type === 'publication' && (
            <p className="text-sm text-ink-2 sm:col-span-2">
              Журнал в списках: {work.journalLists?.length ? work.journalLists.join(', ') : 'нет данных'} · Цитирований: {work.citations ?? 0}
            </p>
          )}
        </section>
      )}

      {type === 'talk' && (
        <section className="grid gap-4 sm:grid-cols-2">
          <F name="event.name" label="Конференция" className="sm:col-span-2" hint="Начните вводить — подскажем ранее введённые, чтобы не плодить разные написания">{(id) => (
            <>
              <Input id={id} list="events" {...register('event.name', { onChange: (e) => pickEvent(e.target.value) })} aria-invalid={!!errAt(errors, 'event.name')} />
              <datalist id="events">{events.data?.map((e) => <option key={e.name} value={e.name} />)}</datalist>
            </>
          )}</F>
          <F name="event.level" label="Уровень конференции">{(id) => (
            <Select id={id} {...register('event.level')}>{Object.entries(confLevelLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="event.url" label="Сайт конференции">{(id) => <Input id={id} type="url" {...register('event.url')} />}</F>
          <F name="event.city" label="Город">{(id) => <Input id={id} {...register('event.city')} />}</F>
          <F name="event.country" label="Страна">{(id) => <Input id={id} {...register('event.country')} />}</F>
          <F name="event.dateStart" label="Дата начала">{(id) => <Input id={id} type="date" {...register('event.dateStart')} aria-invalid={!!errAt(errors, 'event.dateStart')} />}</F>
          <F name="event.dateEnd" label="Дата окончания">{(id) => <Input id={id} type="date" {...register('event.dateEnd')} aria-invalid={!!errAt(errors, 'event.dateEnd')} />}</F>
          <F name="kind" label="Вид доклада">{(id) => (
            <Select id={id} {...register('kind')}>{Object.entries(talkKindLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="format" label="Формат">{(id) => (
            <Select id={id} {...register('format')}>{Object.entries(formatLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="presenters" label="Докладчики (из списка авторов)" className="sm:col-span-2">{() => (
            <Controller control={control} name="presenters" render={({ field }) => {
              const same = (a: AuthorRef, b: AuthorRef) => (a.personId && a.personId === b.personId) || a.displayName === b.displayName
              return (
                <div className="flex flex-wrap gap-3">
                  {authors.filter((a) => a.displayName).map((a, i) => (
                    <label key={i} className="flex items-center gap-1.5 text-sm">
                      <input type="checkbox" className="h-4 w-4 accent-brand" checked={field.value.some((p: AuthorRef) => same(p, a))}
                        onChange={(e) => field.onChange(e.target.checked
                          ? authors.filter((x) => x === a || field.value.some((p: AuthorRef) => same(p, x)))
                          : field.value.filter((p: AuthorRef) => !same(p, a)))} />
                      {a.displayName}
                    </label>
                  ))}
                  {!authors.some((a) => a.displayName) && <span className="text-sm text-muted">Сначала добавьте авторов</span>}
                </div>
              )
            }} />
          )}</F>
          <F name="award" label="Награда">{(id) => <Input id={id} placeholder="Диплом I степени" {...register('award')} />}</F>
        </section>
      )}

      {relatedType && (
        <F name={relatedKey} label={type === 'publication' ? 'Связанный доклад' : 'Связанная публикация (тезисы, статья)'} hint="Показаны работы с общими авторами">{(id) => (
          <Select key={related.data?.length} id={id} {...register(relatedKey)}>
            <option value="">— нет —</option>
            {related.data?.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </Select>
        )}</F>
      )}

      <section className="space-y-4">
        <F name="annotation" label="Аннотация">{(id) => <Textarea id={id} rows={5} {...register('annotation')} />}</F>
        <F name="keywords" label="Ключевые слова">{(id) => (
          <Controller control={control} name="keywords" render={({ field }) => <KeywordsInput id={id} value={field.value ?? []} onChange={(v) => { field.onChange(v); clearErrors('keywords') }} />} />
        )}</F>
        <F name="subjects" label="Классификаторы">{() => (
          <Controller control={control} name="subjects" render={({ field }) => (
            <SubjectsEditor schemes={type === 'publication' ? PUBLICATION_SCHEMES : SCHEMES} value={field.value ?? []} onChange={(v) => { field.onChange(v); clearErrors('subjects') }} />
          )} />
        )}</F>
        <div className="grid gap-4 sm:grid-cols-2">
          <F name="language" label="Язык">{(id) => (
            <Select id={id} {...register('language')}>{Object.entries(languageLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          )}</F>
          <F name="externalUrl" label="Внешняя ссылка">{(id) => <Input id={id} type="url" placeholder="https://" {...register('externalUrl')} />}</F>
        </div>
      </section>

      <section className="space-y-3 bg-panel p-3">
        {isStudent && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-brand" {...register('consent')} />
            Есть согласие студента на открытый доступ к тексту
          </label>
        )}
        <F name="accessLevel" label="Уровень доступа"
          hint={type === 'publication' ? '«Карточка и текст» — только если текст в открытом доступе' : undefined}>
          {(id) => (
            <Select id={id} {...register('accessLevel')} aria-invalid={!!errors.accessLevel}>
              {Object.entries(accessLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          )}
        </F>
      </section>
    </div>
  )

  const errorCount = Object.keys(errors).length

  return (
    <FormProvider {...form}>
      <SourcesCtx.Provider value={work?.fieldMeta ?? {}}>
        <div className="space-y-4 pb-24">
          <nav aria-label="Навигационная цепочка" className="flex flex-wrap gap-1.5 text-sm text-ink-2">
            <Link to={inQueue ? `/admin/review?${new URLSearchParams([...sp].filter(([k]) => k !== 'queue'))}` : '/admin/works'}>{inQueue ? 'Очередь проверки' : 'Все работы'}</Link>
            {work?.importId && <><span aria-hidden>›</span><Link to={`/admin/imports/${work.importId}`}>импорт {work.importFileName ?? work.importId}</Link></>}
            {work?.originalFileName && <><span aria-hidden>›</span><span>{work.originalFileName}</span></>}
          </nav>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b-2 border-brand pb-2">
            <h1 className="text-3xl font-bold">{work ? workTypeLabels[type] : `Новая работа: ${workTypeLabels[type].toLowerCase()}`}</h1>
            {status && <StatusBadge status={status} />}
          </div>

          {work?.duplicateOf && <Alert title="Возможный дубль">Похожая работа уже есть: <Link to={`/admin/works/${work.duplicateOf}`} className="font-medium underline">открыть оригинал</Link>. Проверьте и отклоните дубль или исправьте карточку.</Alert>}
          {status === 'rejected' && work?.rejectReason && <Alert tone="red" title="Отклонена">{work.rejectReason}</Alert>}

          {hasFile && (
            <Tabs className="xl:hidden" value={pane} onChange={setPane} items={[{ value: 'card', label: 'Карточка' }, { value: 'file', label: 'Файл' }]} />
          )}
          <div className={cn(hasFile && 'xl:grid xl:grid-cols-2 xl:gap-10')}>
            {hasFile && <div className={cn('xl:sticky xl:top-20 xl:block xl:self-start', pane === 'file' ? 'block' : 'hidden')}><PdfPane id={work!.id} /></div>}
            <Card className={cn(hasFile && pane === 'file' && 'hidden xl:block')}>
              {/* first: «Материалы от авторов» on the dashboard leads here */}
              {work && work.materials.length > 0 && (
                <section className="mb-6 space-y-1 border-b border-rule pb-4">
                  <h2 className="font-bold">Материалы от авторов</h2>
                  <ul className="divide-y divide-rule">
                    {work.materials.map((m) => (
                      <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                        <button type="button" className="text-left underline underline-offset-4 hover:text-accent"
                          onClick={() => openSigned(() => getAdminMaterialLink(work.id, m.id)).catch((e) => setMessage({ tone: 'red', text: e.message }))}>
                          {materialKindLabels[m.kind]}: {m.name}
                        </button>
                        <span className="text-muted">{[formatSize(m.size), m.uploadedBy].filter(Boolean).join(' · ')}</span>
                        <MaterialStatusBadge status={m.status} />
                        <span className="ml-auto flex gap-4">
                          {m.status !== 'approved' && <Button variant="link" size="sm" onClick={() => onMaterial(m.id, 'approved')} loading={busy === 'material'}>Принять</Button>}
                          {m.status !== 'rejected' && <Button variant="link" size="sm" className="!text-danger" onClick={() => onMaterial(m.id, 'rejected')} loading={busy === 'material'}>Отклонить</Button>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {formEl}
              {!hasFile && (
                <div className="mt-6 border-t border-rule pt-4">
                  {work
                    ? limits.data && <FileDrop label="Файл работы (текст, презентация)" {...limits.data.workFile} file={null} onFile={uploadFile} />
                    : <p className="text-sm text-muted">Файл можно будет загрузить после первого сохранения.</p>}
                </div>
              )}
            </Card>
          </div>
        </div>

        <div className="fixed inset-x-0 bottom-0 z-20 border-t-2 border-brand bg-white">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
            {message && <span className={cn(message.tone === 'red' ? 'text-danger' : 'text-ok')} role="status">{message.text}</span>}
            {!message && errorCount > 0 && <span className="text-warn">Ошибок в карточке: {errorCount}. {Object.keys(errors).map((k) => fieldLabels[k] ?? k).join(', ')}</span>}
            <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-2">
              {actions.includes('reject') && <Button variant="link" size="sm" className="!text-danger" onClick={() => setRejecting(true)}>Отклонить</Button>}
              {actions.includes('unpublish') && <Button variant="link" size="sm" onClick={onUnpublish} loading={busy === 'unpublish'}>Снять с публикации</Button>}
              {inQueue && <Button variant="link" size="sm" onClick={onNext} loading={busy === 'next'}>Следующая работа</Button>}
              <Button variant="outline" size="sm" onClick={onSave} loading={busy === 'save'}>Сохранить</Button>
              {actions.includes('publish') && (
                <Button size="sm" onClick={onPublish} loading={busy === 'publish'}>
                  Опубликовать
                </Button>
              )}
            </div>
          </div>
        </div>

        <Dialog open={rejecting} onClose={() => setRejecting(false)} title="Отклонить работу">
          <div className="space-y-3">
            <Field label="Причина">{(id) => <Textarea id={id} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Например: дубль, загружен не тот файл" />}</Field>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRejecting(false)}>Отмена</Button>
              <Button variant="danger" disabled={!reason.trim()} loading={busy === 'reject'} onClick={onReject}>Отклонить</Button>
            </div>
          </div>
        </Dialog>

        <Dialog open={!!doiState} onClose={() => setDoiState(null)} title="Перезаписать заполненные поля?">
          {doiState && (
            <div className="space-y-3 text-sm">
              <p>Пустые поля заполнены по {doiState.by === 'doi' ? 'DOI' : 'ISBN'}. Эти поля уже заполнены и отличаются от найденного:</p>
              <ul className="space-y-2">
                {doiState.conflicts.map((c) => (
                  <li key={c.field} className="bg-panel p-2">
                    <p className="font-medium">{c.label}</p>
                    <p className="text-ink-2">сейчас: {c.current}</p>
                    <p className="text-ink">{doiState.by === 'doi' ? 'по DOI' : 'по ISBN'}: {c.incoming}</p>
                  </li>
                ))}
              </ul>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setDoiState(null)}>Оставить мои значения</Button>
                <Button onClick={() => onOverwrite(doiState)} loading={busy === doiState.by}>Перезаписать</Button>
              </div>
            </div>
          )}
        </Dialog>
      </SourcesCtx.Provider>
    </FormProvider>
  )
}
