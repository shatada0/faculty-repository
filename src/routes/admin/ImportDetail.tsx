import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getImport, retryImport } from '@/api'
import type { ImportItemStatus } from '@/api/types'
import { NotFound, useTitle } from '@/components/Layout'
import { Alert, Badge, Button, Card, ErrorState, Select, Spinner, Tiles, btn, type Tone } from '@/components/ui'
import { formatDateTime, plural } from '@/lib/format'
import { importItemStatusLabels, workTypePlural } from '@/lib/labels'
import { ImportStatusBadge } from './Imports'

const itemTone: Record<ImportItemStatus, Tone> = { pending: 'slate', extracted: 'blue', filled: 'blue', published: 'green', duplicate: 'amber', error: 'red' }

export default function ImportDetail() {
  const { id = '' } = useParams()
  const [sp, setSp] = useSearchParams()
  const status = sp.get('status') ?? ''
  const qc = useQueryClient()
  const [retrying, setRetrying] = useState(false)
  const [retryErr, setRetryErr] = useState<string>()
  const q = useQuery({
    queryKey: ['admin-import', id, status],
    queryFn: () => getImport(id, { status }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const s = query.state.data?.import.status
      return s === 'processing' || s === 'uploading' ? 3000 : false
    },
    refetchIntervalInBackground: true,
  })
  const done = q.data?.import.status === 'done'
  useTitle(q.data && `Импорт ${q.data.import.fileName}`)
  useEffect(() => {
    if (done) qc.invalidateQueries({ predicate: (x) => ['admin-works', 'admin-unmatched', 'admin-imports'].includes(String(x.queryKey[0])) })
  }, [done, qc])

  if (q.isError) return q.error instanceof ApiError && q.error.status === 404 ? <NotFound text="Импорт не найден" /> : <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (q.isPending) return <Spinner />

  const { import: imp, items } = q.data

  return (
    <div className="space-y-5">
      <nav aria-label="Навигационная цепочка" className="text-sm text-ink-2"><Link to="/admin/imports">Импорты</Link></nav>
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="break-all text-3xl font-bold">{imp.fileName}</h1>
          <ImportStatusBadge status={imp.status} errors={imp.stats.errors} />
        </div>
        <p className="text-sm text-ink-2">
          {workTypePlural[imp.workType]} · {imp.kind === 'archive' ? 'архив' : 'таблица'}
          {imp.metaTableName && <> · таблица сведений: {imp.metaTableName}</>} · {imp.createdBy}, {formatDateTime(imp.createdAt)}
        </p>
      </header>

      <Tiles items={[
        { value: imp.stats.total, label: 'всего' },
        { value: imp.stats.published, label: 'опубликовано' },
        { value: imp.stats.filled, label: 'на проверке' },
        { value: imp.stats.duplicates, label: plural(imp.stats.duplicates, ['дубль', 'дубля', 'дублей']) },
        { value: imp.stats.errors, label: plural(imp.stats.errors, ['ошибка', 'ошибки', 'ошибок']) },
      ]} />

      {imp.status === 'processing' && (
        <div className="space-y-1" aria-live="polite">
          <div className="h-1.5 overflow-hidden bg-panel-2"><div className="h-full bg-brand transition-all" style={{ width: `${(imp.stats.processed / Math.max(1, imp.stats.total)) * 100}%` }} /></div>
          <p className="text-sm text-ink-2">ИИ разбирает файлы: {imp.stats.processed} из {imp.stats.total}. Страница обновляется автоматически.</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {imp.stats.filled > 0 && <Link to={`/admin/review?importId=${imp.id}`} className={btn('primary')}>Проверить черновики ({imp.stats.filled})</Link>}
        {imp.stats.unmatched > 0 && <Link to="/admin/people?unmatched=1" className={btn('outline')}>Разобрать авторов</Link>}
        {imp.kind === 'archive' && imp.status === 'done' && imp.stats.errors > 0 && (
          <Button variant="outline" loading={retrying} onClick={async () => {
            setRetrying(true)
            setRetryErr(undefined)
            try {
              qc.setQueryData(['admin-import', id, status], await retryImport(id))
              await qc.invalidateQueries({ queryKey: ['admin-import', id] })
            } catch (e) {
              setRetryErr(e instanceof Error ? e.message : 'Ошибка')
            } finally {
              setRetrying(false)
            }
          }}>Повторить файлы с ошибкой ({imp.stats.errors})</Button>
        )}
        <Select aria-label="Статус элемента" value={status} onChange={(e) => setSp(e.target.value ? { status: e.target.value } : {}, { replace: true, preventScrollReset: true })} className="ml-auto h-10 w-auto">
          <option value="">Все статусы</option>
          {(Object.keys(importItemStatusLabels) as ImportItemStatus[]).map((s) => <option key={s} value={s}>{importItemStatusLabels[s]}</option>)}
        </Select>
      </div>

      {retryErr && <Alert tone="red">{retryErr}</Alert>}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[44rem] text-sm">
          <thead className="border-b-2 border-brand text-left">
            <tr><th className="p-3">{imp.kind === 'archive' ? 'Файл' : 'Строка'}</th><th className="p-3">Название</th><th className="p-3">Статус</th><th className="p-3">Подробности</th></tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {items.map((it, i) => (
              <tr key={`${i}:${it.ref}`} className={it.status === 'error' ? 'bg-danger-bg' : it.status === 'duplicate' ? 'bg-warn-bg' : undefined}>
                <td className="max-w-[14rem] break-all p-3 font-mono text-xs">{it.ref}</td>
                <td className="p-3">
                  {it.workId ? <Link to={`/admin/works/${it.workId}`} className="text-ink hover:underline">{it.title ?? 'Черновик'}</Link> : <span className="text-ink-2">{it.title ?? '—'}</span>}
                </td>
                <td className="p-3"><Badge tone={itemTone[it.status]}>{importItemStatusLabels[it.status]}</Badge></td>
                <td className="space-y-1 p-3 text-xs">
                  {it.error && <p className="text-danger">{it.error}</p>}
                  {it.duplicateOf && <p>Похоже на <Link to={`/admin/works/${it.duplicateOf}`} className="text-ink hover:underline">существующую работу</Link></p>}
                  {it.unmatchedAuthors?.length ? (
                    <p className="flex flex-wrap items-center gap-1">Не распознаны: {it.unmatchedAuthors.map((n) => <Badge key={n} tone="amber">{n}</Badge>)}</p>
                  ) : null}
                </td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={4} className="p-6 text-center text-muted">Нет элементов с таким статусом</td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
