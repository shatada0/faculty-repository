import { Link, useNavigate, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getAdminWorks, getImports } from '@/api'
import type { WorkType } from '@/api/types'
import { TypeIcon } from '@/components/WorkCard'
import { Badge, Card, Count, EmptyState, ErrorState, Select, Spinner } from '@/components/ui'
import { workTypePlural } from '@/lib/labels'

/** Query-string of the queue filters, carried into the editor for "Следующая работа". */
export const queueParams = (sp: URLSearchParams) => {
  const p = new URLSearchParams({ queue: '1' })
  for (const k of ['type', 'importId']) if (sp.get(k)) p.set(k, sp.get(k)!)
  return p.toString()
}

const reviewQuery = (sp: URLSearchParams) => ({
  status: 'draft', sort: 'problems', pageSize: 200, type: sp.get('type') ?? undefined, importId: sp.get('importId') ?? undefined,
})

export default function Review() {
  const [sp, setSp] = useSearchParams()
  const navigate = useNavigate()
  const query = reviewQuery(sp)
  const works = useQuery({ queryKey: ['admin-works', query], queryFn: () => getAdminWorks(query), placeholderData: keepPreviousData })
  const imports = useQuery({ queryKey: ['admin-imports'], queryFn: getImports })
  const set = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n }, { replace: true })

  return (
    <div className="space-y-4">
      <h1 className="text-4xl font-bold">Очередь проверки</h1>
      <div className="flex flex-wrap gap-2">
        <Select aria-label="Тип" value={sp.get('type') ?? ''} onChange={(e) => set('type', e.target.value)} className="w-auto">
          <option value="">Все типы</option>
          {(['vkr', 'coursework', 'publication', 'talk'] as WorkType[]).map((t) => <option key={t} value={t}>{workTypePlural[t]}</option>)}
        </Select>
        <Select aria-label="Импорт" value={sp.get('importId') ?? ''} onChange={(e) => set('importId', e.target.value)} className="w-auto max-w-xs">
          <option value="">Все импорты</option>
          {imports.data?.map((i) => <option key={i.id} value={i.id}>{i.fileName}</option>)}
        </Select>
        <span className="self-center text-sm text-muted">Сначала проблемные</span>
      </div>

      {works.isError ? <ErrorState error={works.error} onRetry={() => works.refetch()} />
        : works.isPending ? <Spinner />
        : works.data.items.length === 0 ? <EmptyState>Черновиков нет — всё проверено</EmptyState>
        : (
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-sm">
              <thead className="border-b-2 border-brand text-left">
                <tr><th className="p-3">Работа</th><th className="p-3">Авторы</th><th className="p-3">Год</th><th className="p-3">Импорт</th><th className="p-3">Внимание</th></tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {works.data.items.map((w) => {
                  const href = `/admin/works/${w.id}?${queueParams(sp)}`
                  return (
                    <tr key={w.id} className="cursor-pointer hover:bg-panel" onClick={() => navigate(href)}>
                      <td className="p-3">
                        <span className="flex items-start gap-2">
                          <TypeIcon type={w.type} className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
                          <Link to={href} className="font-medium text-ink hover:underline" onClick={(e) => e.stopPropagation()}>{w.title || w.originalFileName || 'Без названия'}</Link>
                        </span>
                        {w.duplicateOf && <Badge tone="amber" className="ml-6 mt-1">Возможный дубль</Badge>}
                      </td>
                      <td className="p-3 text-ink-2">{w.authors.map((a) => a.displayName).join(', ') || '—'}</td>
                      <td className="p-3">{w.year || '—'}</td>
                      <td className="max-w-[12rem] truncate p-3 text-xs text-muted">{w.importFileName ?? 'вручную'}</td>
                      <td className="p-3">
                        {w.issues.length
                          ? <Badge tone="amber" title={w.issues.join('\n')}><Count n={w.issues.length} forms={['поле', 'поля', 'полей']} /></Badge>
                          : <Badge tone="green">Готово</Badge>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        )}
    </div>
  )
}
