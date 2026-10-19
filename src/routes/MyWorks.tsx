// "Мои работы": an author adds materials (text, slides, data) to own works; a moderator checks them before they go public.
// The list stays compact (one row per work, a status line); everything about a work's materials opens in a side panel.
import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteMyMaterial, getMyWorks, getUploadLimits, uploadMaterial } from '@/api'
import type { Material, MaterialKind, MaterialStatus, MyWork } from '@/api/types'
import { FileDrop } from '@/components/FileDrop'
import { RequireAuth, useMe, useTitle } from '@/components/Layout'
import { Alert, Button, Dialog, EmptyState, ErrorState, Select, Spinner, cn } from '@/components/ui'
import { formatDateTime, formatSize } from '@/lib/format'
import { groupByYear } from '@/lib/group'
import { materialKindLabels } from '@/lib/labels'

const SUMMARY: { status: MaterialStatus; label: string; tone: string }[] = [
  { status: 'pending', label: 'на проверке', tone: 'text-warn' },
  { status: 'approved', label: 'опубликовано', tone: 'text-ok' },
  { status: 'rejected', label: 'отклонено', tone: 'text-muted' },
]
const of = (w: MyWork, status: MaterialStatus) => w.materials.filter((m) => m.status === status)

function MaterialRow({ m, onDelete }: { m: Material; onDelete?: () => void }) {
  return (
    <li className="flex items-start gap-3 border-b border-rule py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate font-bold" title={m.name}>{m.name}</p>
        <p className="text-sm text-ink-2">{materialKindLabels[m.kind]} · {formatSize(m.size)} · {formatDateTime(m.uploadedAt)}</p>
        {m.status === 'rejected' && m.rejectReason && <p className="text-sm text-danger">Причина: {m.rejectReason}</p>}
      </div>
      {onDelete && <Button variant="link" size="sm" onClick={onDelete}>Удалить</Button>}
    </li>
  )
}

/** Side panel of one work: upload first, then pending, published, and rejected (folded away). */
function MaterialsPanel({ w, onClose }: { w: MyWork; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: me } = useMe()
  const limits = useQuery({ queryKey: ['upload-limits'], queryFn: getUploadLimits, staleTime: Infinity })
  const [kind, setKind] = useState<MaterialKind>('text')
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()

  async function act(fn: () => Promise<unknown>, done: string) {
    setError(undefined)
    setNotice(undefined)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['my-works'] })
      setNotice(done)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }
  const upload = (f: File | null, err?: string) => {
    if (err) return setError(err)
    if (!f) return
    setProgress(0)
    act(() => uploadMaterial(w.id, f, kind, setProgress), 'Файл загружен и ждёт проверки модератором').finally(() => setProgress(null))
  }
  const pending = of(w, 'pending')
  const approved = of(w, 'approved')
  const rejected = of(w, 'rejected')

  return (
    <Dialog open onClose={onClose} title="Материалы" side className="w-[min(34rem,95vw)]">
      <div className="space-y-6">
        <div>
          <p className="text-sm text-ink-2">{w.kind}, {w.year}</p>
          <p className="font-bold leading-snug">{w.title}</p>
        </div>

        <section className="space-y-2 bg-panel p-3">
          <h3 className="font-bold">Добавить файл</h3>
          <label className="flex items-center gap-2 text-sm">Что это
            <Select value={kind} onChange={(e) => setKind(e.target.value as MaterialKind)} className="h-9 w-auto">
              {Object.entries(materialKindLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </label>
          {progress !== null
            ? <p className="text-sm text-ink-2" role="status">Загружено {Math.round(progress * 100)}%</p>
            : limits.data && <FileDrop label="Файл" {...limits.data.material} file={null} onFile={upload} />}
          {notice && <p className="text-sm text-ok" role="status">{notice}</p>}
          {error && <Alert tone="red">{error}</Alert>}
        </section>

        {pending.length > 0 && (
          <section>
            <h3 className="border-b-2 border-brand pb-1 font-bold">На проверке <span className="font-normal text-muted">{pending.length}</span></h3>
            <ul>{pending.map((m) => <MaterialRow key={m.id} m={m} onDelete={m.uploadedBy === me?.name ? () => window.confirm(`Удалить файл «${m.name}»?`) && act(() => deleteMyMaterial(w.id, m.id), 'Файл удалён') : undefined} />)}</ul>
          </section>
        )}
        {approved.length > 0 && (
          <section>
            <h3 className="border-b-2 border-brand pb-1 font-bold">Опубликованы <span className="font-normal text-muted">{approved.length}</span></h3>
            <ul>{approved.map((m) => <MaterialRow key={m.id} m={m} />)}</ul>
          </section>
        )}
        {rejected.length > 0 && (
          <details>
            <summary className="cursor-pointer font-bold">Отклонённые <span className="font-normal text-muted">{rejected.length}</span></summary>
            <ul className="mt-1">{rejected.map((m) => <MaterialRow key={m.id} m={m} />)}</ul>
          </details>
        )}
        {w.materials.length === 0 && <p className="text-sm text-muted">Материалов пока нет.</p>}
      </div>
    </Dialog>
  )
}

function WorkRow({ w, onOpen }: { w: MyWork; onOpen: () => void }) {
  const counts = SUMMARY.map((s) => ({ ...s, n: of(w, s.status).length })).filter((s) => s.n > 0)
  return (
    <li className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule py-3">
      <div className="min-w-0 flex-[1_1_20rem]">
        <p className="text-sm text-ink-2">{w.kind}</p>
        <p className="font-bold leading-snug">{w.public ? <Link to={`/works/${w.id}`}>{w.title}</Link> : w.title}</p>
        {counts.length > 0 && (
          <p className="text-sm">{counts.map((c, i) => <span key={c.status} className={c.tone}>{i > 0 && ' · '}{c.label}: {c.n}</span>)}</p>
        )}
      </div>
      {w.canUpload && <Button variant="outline" size="sm" onClick={onOpen}>{w.materials.length ? 'Материалы' : 'Добавить материал'}</Button>}
    </li>
  )
}

function MyWorks() {
  const q = useQuery({ queryKey: ['my-works'], queryFn: getMyWorks })
  const [openId, setOpenId] = useState<string>()
  useTitle('Мои работы')
  const open = q.data?.items.find((w) => w.id === openId)
  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-4xl font-bold">Мои работы</h1>
        <p className="text-ink-2">Добавляйте текст, презентации и данные к публикациям и выступлениям. Файл появится на странице работы после проверки модератором.</p>
      </div>
      {q.isPending ? <Spinner />
        : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
        : q.data.items.length === 0 ? <EmptyState>Работ с вашим авторством пока нет</EmptyState>
        : groupByYear(q.data.items).map(([year, items]) => (
          <section key={year} className="grid gap-x-6 border-t-2 border-brand sm:grid-cols-[5rem_minmax(0,1fr)]">
            <h2 className="pt-3 text-2xl font-bold">{year}</h2>
            <ul className={cn(q.isFetching && 'opacity-80')}>{items.map((w) => <WorkRow key={w.id} w={w} onOpen={() => setOpenId(w.id)} />)}</ul>
          </section>
        ))}
      {open && <MaterialsPanel key={open.id} w={open} onClose={() => setOpenId(undefined)} />}
    </div>
  )
}

export default function MyWorksPage() {
  return <RequireAuth author><MyWorks /></RequireAuth>
}
