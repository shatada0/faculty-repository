// "Мои работы": an author adds materials (text, slides, data) to own works; a moderator checks them before they go public.
import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteMyMaterial, getMyWorks, getUploadLimits, uploadMaterial } from '@/api'
import type { MaterialKind, MyWork } from '@/api/types'
import { FileDrop } from '@/components/FileDrop'
import { RequireAuth } from '@/components/Layout'
import { MaterialStatusBadge } from '@/components/WorkCard'
import { Alert, Button, EmptyState, ErrorState, Select, Spinner } from '@/components/ui'
import { formatSize } from '@/lib/format'
import { materialKindLabels } from '@/lib/labels'

function MyWorkCard({ w }: { w: MyWork }) {
  const qc = useQueryClient()
  const limits = useQuery({ queryKey: ['upload-limits'], queryFn: getUploadLimits, staleTime: Infinity })
  const [kind, setKind] = useState<MaterialKind>('text')
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<string>()

  async function act(fn: () => Promise<unknown>) {
    setError(undefined)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['my-works'] })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    }
  }
  const upload = (f: File | null, err?: string) => {
    if (err) return setError(err)
    if (!f) return
    setProgress(0)
    act(() => uploadMaterial(w.id, f, kind, setProgress)).finally(() => setProgress(null))
  }

  return (
    <article className="space-y-3 border-b border-rule py-5">
      <div className="space-y-0.5">
        <p className="text-sm text-ink-2">{w.kind}, {w.year}</p>
        <h2 className="text-lg font-bold leading-snug">{w.public ? <Link to={`/works/${w.id}`}>{w.title}</Link> : w.title}</h2>
      </div>
      {w.materials.length > 0 && (
        <ul className="space-y-1 text-sm">
          {w.materials.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>{materialKindLabels[m.kind]}: {m.name}</span>
              <span className="text-muted">{formatSize(m.size)}</span>
              <MaterialStatusBadge status={m.status} />
              {m.status === 'pending' && <Button variant="link" size="sm" onClick={() => act(() => deleteMyMaterial(w.id, m.id))}>Удалить</Button>}
            </li>
          ))}
        </ul>
      )}
      {w.canUpload && limits.data && (
        <div className="space-y-2 bg-panel p-3">
          <label className="flex items-center gap-2 text-sm">Что загружаете
            <Select value={kind} onChange={(e) => setKind(e.target.value as MaterialKind)} className="h-9 w-auto">
              {Object.entries(materialKindLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </label>
          {progress !== null
            ? <p className="text-sm text-ink-2" role="status">Загружено {Math.round(progress * 100)}%</p>
            : <FileDrop label="Файл" {...limits.data.material} file={null} onFile={upload} />}
        </div>
      )}
      {error && <Alert tone="red">{error}</Alert>}
    </article>
  )
}

function MyWorks() {
  const q = useQuery({ queryKey: ['my-works'], queryFn: getMyWorks })
  return (
    <div className="space-y-5">
      <h1 className="text-4xl font-bold">Мои работы</h1>
      <Alert tone="blue">Добавьте текст, презентацию или данные к своим публикациям и выступлениям. Материал появится на странице работы после проверки модератором.</Alert>
      {q.isPending ? <Spinner />
        : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
        : q.data.items.length === 0 ? <EmptyState>Работ с вашим авторством пока нет</EmptyState>
        : <div className="border-t-2 border-brand">{q.data.items.map((w) => <MyWorkCard key={w.id} w={w} />)}</div>}
    </div>
  )
}

export default function MyWorksPage() {
  return <RequireAuth author><MyWorks /></RequireAuth>
}
