import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getImports } from '@/api'
import type { Import, ImportStatus } from '@/api/types'
import { TypeIcon } from '@/components/WorkCard'
import { Badge, Card, EmptyState, ErrorState, Spinner, btn, type Tone } from '@/components/ui'
import { formatDateTime, plural } from '@/lib/format'
import { importStatusLabels, workTypePlural } from '@/lib/labels'
import { useTitle } from '@/components/Layout'

const tone: Record<ImportStatus, Tone> = { uploading: 'blue', processing: 'blue', done: 'green', failed: 'red' }
export const ImportStatusBadge = ({ status, errors = 0 }: { status: ImportStatus; errors?: number }) =>
  status === 'done' && errors > 0
    ? <Badge tone="amber">С ошибками</Badge>
    : <Badge tone={tone[status]}>{importStatusLabels[status]}</Badge>

/** "1 опубликовано · 1 ошибка из 2", only the non-zero parts. */
export function ImportResult({ stats: s }: { stats: Import['stats'] }) {
  const parts: [number, string, string?][] = [
    [s.published, 'опубликовано'],
    [s.filled, 'на проверке'],
    [s.duplicates, plural(s.duplicates, ['дубль', 'дубля', 'дублей'])],
    [s.errors, plural(s.errors, ['ошибка', 'ошибки', 'ошибок']), 'text-danger'],
  ]
  const shown = parts.filter(([n]) => n > 0)
  return (
    <>
      {shown.map(([n, label, cls], i) => <span key={label} className={cls}>{i > 0 && ' · '}{n} {label}</span>)}
      {shown.length > 0 ? ` из ${s.total}` : `${s.total} ${plural(s.total, ['файл', 'файла', 'файлов'])}`}
    </>
  )
}

/** Imports often share a file name ("vkr.zip"): the date tells them apart in filters. */
export const importOption = (i: Import) => `${i.fileName} · ${formatDateTime(i.createdAt)}`

export default function Imports() {
  const q = useQuery({
    queryKey: ['admin-imports'],
    queryFn: getImports,
    refetchInterval: (query) => (query.state.data?.some((i) => i.status === 'processing') ? 3000 : false),
  })
  useTitle('Импорты')
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="mr-auto text-4xl font-bold">Импорты</h1>
        <Link to="/admin/imports/new" className={btn('primary')}>Новый импорт</Link>
      </div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
        : q.data.length === 0 ? <EmptyState>Импортов пока нет</EmptyState>
        : (
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead className="border-b-2 border-brand text-left">
                <tr><th className="p-3">Файл и результат</th><th className="p-3">Тип</th><th className="p-3">Загружен</th><th className="p-3">Статус</th></tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {q.data.map((i) => (
                  <tr key={i.id} className="hover:bg-panel">
                    <td className="p-3">
                      <Link to={`/admin/imports/${i.id}`} className="font-medium text-ink hover:underline">{i.fileName}</Link>
                      {i.metaTableName && <p className="text-xs text-muted">+ {i.metaTableName}</p>}
                      <p className="mt-1 text-xs text-ink-2"><ImportResult stats={i.stats} /></p>
                    </td>
                    <td className="p-3"><span className="inline-flex items-center gap-1.5"><TypeIcon type={i.workType} />{workTypePlural[i.workType]}</span></td>
                    <td className="p-3 text-ink-2">{formatDateTime(i.createdAt)}<br /><span className="text-xs">{i.createdBy}</span></td>
                    <td className="p-3"><ImportStatusBadge status={i.status} errors={i.stats.errors} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
    </div>
  )
}
