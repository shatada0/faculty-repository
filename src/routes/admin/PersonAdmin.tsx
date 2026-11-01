import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getAdminPerson, importOrcid, mergePerson, patchPerson } from '@/api'
import type { AdminPersonCard, OrcidImport, Person, PersonKind, PersonPatch } from '@/api/types'
import { PersonPicker } from '@/components/AuthorsEditor'
import { NotFound, useTitle } from '@/components/Layout'
import { StatusBadge, TypeIcon } from '@/components/WorkCard'
import { Alert, Button, Card, Dialog, ErrorState, Field, Input, Select, Spinner, Textarea } from '@/components/ui'
import { personKindLabels } from '@/lib/labels'
import { useDepartments } from '@/routes/People'
import { invalidatePeople } from './People'

function OrcidImportCard({ person }: { person: AdminPersonCard }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<OrcidImport>()
  const [error, setError] = useState<string>()
  async function run() {
    setBusy(true)
    setError(undefined)
    try {
      setResult(await importOrcid(person.id))
      await invalidatePeople(qc)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка импорта')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card className="space-y-3 p-4">
      <h2 className="text-xl font-bold">Импорт из ORCID</h2>
      <p className="text-sm text-ink-2">Работы с DOI из профиля {person.orcid} станут черновиками публикаций, заполненными по Crossref. Уже известные DOI пропускаются.</p>
      <Button variant="outline" size="sm" onClick={run} loading={busy}>Импортировать</Button>
      {result && (
        <Alert tone="blue">
          {result.message}
          {result.notFound.length > 0 && <span className="mt-1 block text-sm">Нет в Crossref: {result.notFound.join(', ')}</span>}
        </Alert>
      )}
      {error && <Alert tone="red">{error}</Alert>}
    </Card>
  )
}

function PersonForm({ person }: { person: AdminPersonCard }) {
  const qc = useQueryClient()
  const init = { ...person, aliasesText: (person.aliases ?? []).join('\n') }
  const [f, setF] = useState(init)
  const departments = useDepartments().data?.items ?? []
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string }>()
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value })
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      // null, not undefined: JSON drops undefined and the server would keep the old value
      const body: PersonPatch = {
        fullName: f.fullName.trim(), kind: f.kind, department: f.department || null, position: f.position || null,
        academicDegree: f.academicDegree || null, academicTitle: f.academicTitle || null, orcid: f.orcid || null,
        spin: f.spin || null, elibraryId: f.elibraryId || null,
        affiliation: f.affiliation || null, aliases: f.aliasesText.split('\n').map((s) => s.trim()).filter(Boolean),
      }
      const saved = await patchPerson(person.id, body)
      qc.setQueryData(['admin-person', person.id], saved)
      await invalidatePeople(qc)
      setMsg({ ok: true, text: 'Сохранено' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Ошибка' })
    } finally {
      setBusy(false)
    }
  }
  const staff = f.kind === 'staff'
  return (
    <form onSubmit={save} className="grid gap-4 sm:grid-cols-2">
      <Field label="ФИО" className="sm:col-span-2">{(id) => <Input id={id} value={f.fullName} onChange={set('fullName')} />}</Field>
      <Field label="Вид">{(id) => (
        <Select id={id} value={f.kind} onChange={set('kind')}>{(Object.keys(personKindLabels) as PersonKind[]).map((k) => <option key={k} value={k}>{personKindLabels[k]}</option>)}</Select>
      )}</Field>
      {f.kind === 'external'
        ? <Field label="Организация">{(id) => <Input id={id} value={f.affiliation ?? ''} onChange={set('affiliation')} />}</Field>
        : <Field label="Кафедра">{(id) => <Select id={id} value={f.department ?? ''} onChange={set('department')}><option value="">—</option>{departments.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}</Select>}</Field>}
      {staff && (
        <>
          <Field label="Должность">{(id) => <Input id={id} value={f.position ?? ''} onChange={set('position')} placeholder="доцент кафедры ИС" />}</Field>
          <Field label="Учёная степень">{(id) => <Input id={id} value={f.academicDegree ?? ''} onChange={set('academicDegree')} placeholder="кандидат технических наук" />}</Field>
          <Field label="Учёное звание">{(id) => <Input id={id} value={f.academicTitle ?? ''} onChange={set('academicTitle')} placeholder="доцент" />}</Field>
        </>
      )}
      <Field label="ORCID">{(id) => <Input id={id} value={f.orcid ?? ''} onChange={set('orcid')} placeholder="0000-0002-1825-0097" />}</Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="SPIN-код РИНЦ">{(id) => <Input id={id} value={f.spin ?? ''} onChange={set('spin')} placeholder="1234-5678" />}</Field>
        <Field label="AuthorID РИНЦ" hint="Из адреса страницы автора в eLIBRARY">{(id) => <Input id={id} value={f.elibraryId ?? ''} onChange={set('elibraryId')} inputMode="numeric" />}</Field>
      </div>
      <Field label="Варианты написания ФИО" className="sm:col-span-2" hint="По одному в строке, в том числе латиницей. По ним сопоставляются авторы при импорте.">
        {(id) => <Textarea id={id} rows={4} value={f.aliasesText} onChange={set('aliasesText')} />}
      </Field>
      <div className="flex items-center gap-3 sm:col-span-2">
        <Button type="submit" loading={busy}>Сохранить</Button>
        {msg && <span className={msg.ok ? 'text-sm text-ok' : 'text-sm text-danger'} role="status">{msg.text}</span>}
      </div>
    </form>
  )
}

export default function PersonAdmin() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['admin-person', id], queryFn: () => getAdminPerson(id) })
  useTitle(q.data?.fullName)
  const [dup, setDup] = useState<Person | null>(null)
  const [merging, setMerging] = useState(false)
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string>()

  // A merged-away id resolves to the surviving profile.
  useEffect(() => { if (q.data && q.data.id !== id) navigate(`/admin/people/${q.data.id}`, { replace: true }) }, [q.data, id, navigate])

  if (q.isError) return q.error instanceof ApiError && q.error.status === 404 ? <NotFound text="Человек не найден" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (q.isPending || q.data.id !== id) return <Spinner />
  const p = q.data

  async function merge() {
    if (!dup) return
    setMerging(true)
    try {
      const merged = await mergePerson(p.id, dup.id)
      qc.setQueryData(['admin-person', p.id], merged)
      await invalidatePeople(qc)
      setDup(null)
      setPicking(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка объединения')
    } finally {
      setMerging(false)
    }
  }

  return (
    <div className="space-y-5">
      <nav className="text-sm"><Link to="/admin/people" className="text-ink hover:underline">← Люди</Link></nav>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-4xl font-bold">{p.fullName}</h1>
        {p.kind === 'staff' && <Link to={`/people/${p.id}`} className="inline-flex items-center gap-1 text-sm text-ink hover:underline">Публичный профиль</Link>}
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_22rem]">
        <Card className="p-4"><PersonForm key={JSON.stringify(p)} person={p} /></Card>

        <div className="space-y-5">
          {p.orcid && <OrcidImportCard person={p} />}
          <Card className="space-y-3 p-4">
            <h2 className="flex items-center gap-2 text-xl font-bold">Объединить с дублем</h2>
            <p className="text-sm text-ink-2">Выберите профиль-дубль: все его работы перейдут к этому профилю, а старая ссылка будет вести сюда.</p>
            {picking ? <PersonPicker onPick={setDup} exclude={p.id} autoFocus /> : <Button variant="outline" size="sm" onClick={() => setPicking(true)}>Выбрать дубль</Button>}
            {error && <Alert tone="red">{error}</Alert>}
          </Card>

          <Card className="p-4">
            <h2 className="mb-2 text-xl font-bold">Работы ({p.works.length})</h2>
            {p.works.length === 0 ? <p className="text-sm text-muted">Нет работ</p> : (
              <ul className="space-y-2 text-sm">
                {p.works.map((w) => (
                  <li key={w.id} className="flex items-start gap-2">
                    <TypeIcon type={w.type} className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
                    <Link to={`/admin/works/${w.id}`} className="flex-1 text-ink hover:underline">{w.title}</Link>
                    <span className="text-xs text-muted">{w.year}</span>
                    {w.status !== 'published' && <StatusBadge status={w.status} />}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <Dialog open={!!dup} onClose={() => setDup(null)} title="Объединить профили?">
        {dup && (
          <div className="space-y-3 text-sm">
            <p>Все работы профиля <b>{dup.fullName}</b> перейдут к профилю <b>{p.fullName}</b>. Варианты написания ФИО объединятся. Отменить это действие нельзя.</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDup(null)}>Отмена</Button>
              <Button onClick={merge} loading={merging}>Объединить</Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
