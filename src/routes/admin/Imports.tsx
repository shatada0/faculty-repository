import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getImports } from '@/api'
import type { ImportStatus } from '@/api/types'
import { TypeIcon } from '@/components/WorkCard'
import { Badge, Card, EmptyState, ErrorState, Spinner, btn, type Tone } from '@/components/ui'
import { formatDateTime } from '@/lib/format'
import { importStatusLabels, workTypePlural } from '@/lib/labels'

const tone: Record<ImportStatus, Tone> = { uploading: 'blue', processing: 'blue', done: 'green', failed: 'red' }
export const ImportStatusBadge = ({ status }: { status: ImportStatus }) => <Badge tone={tone[status]}>{importStatusLabels[status]}</Badge>

export default function Imports() {
  const q = useQuery({
    queryKey: ['admin-imports'],
    queryFn: getImports,
    refetchInterval: (query) => (query.state.data?.some((i) => i.status === 'processing') ? 3000 : false),
  })
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
                <tr><th className="p-3">Файл</th><th className="p-3">Тип</th><th className="p-3">Загружен</th><th className="p-3">Статус</th><th className="p-3 text-right">Заполнено / дубли / ошибки</th></tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {q.data.map((i) => (
                  <tr key={i.id} className="hover:bg-panel">
                    <td className="p-3">
                      <Link to={`/admin/imports/${i.id}`} className="font-medium text-ink hover:underline">{i.fileName}</Link>
                      {i.metaTableName && <p className="text-xs text-muted">+ {i.metaTableName}</p>}
                    </td>
                    <td className="p-3"><span className="inline-flex items-center gap-1.5"><TypeIcon type={i.workType} />{workTypePlural[i.workType]}</span></td>
                    <td className="p-3 text-ink-2">{formatDateTime(i.createdAt)}<br /><span className="text-xs">{i.createdBy}</span></td>
                    <td className="p-3"><ImportStatusBadge status={i.status} /></td>
                    <td className="p-3 text-right tabular-nums">{i.stats.filled} / {i.stats.duplicates} / <span className={i.stats.errors ? 'text-danger' : ''}>{i.stats.errors}</span> из {i.stats.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
    </div>
  )
}
