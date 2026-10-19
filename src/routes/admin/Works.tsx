import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { bulkWorks, exportWorks, getAdminWorks, getImports, worksZipUrl } from '@/api'
import type { AccessLevel, WorkStatus, WorkType } from '@/api/types'
import { StatusBadge, TypeIcon } from '@/components/WorkCard'
import { Alert, Button, Card, EmptyState, ErrorState, Input, Pagination, Select, Spinner, btn, cn } from '@/components/ui'
import { formatDateTime } from '@/lib/format'
import { accessLabels, statusLabels, workTypePlural } from '@/lib/labels'
import { importOption } from './Imports'
import { useTitle } from '@/components/Layout'

const PAGE = 25
const YEARS = Array.from({ length: new Date().getFullYear() - 2017 }, (_, i) => new Date().getFullYear() + 1 - i)

export default function Works() {
  const [sp, setSp] = useSearchParams()
  const qc = useQueryClient()
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const query = { type: sp.get('type') ?? '', status: sp.get('status') ?? '', year: sp.get('year') ?? '', importId: sp.get('importId') ?? '', q: sp.get('q') ?? '' }
  const set = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); n.delete('page'); return n }, { replace: true, preventScrollReset: true })
  const works = useQuery({ queryKey: ['admin-works', { ...query, page, pageSize: PAGE }], queryFn: () => getAdminWorks({ ...query, page, pageSize: PAGE }), placeholderData: keepPreviousData })
  const imports = useQuery({ queryKey: ['admin-imports'], queryFn: getImports })
  useTitle('Все работы')

  const [text, setText] = useState(query.q)
  useEffect(() => {
    if (text.trim() === query.q) return
    const t = setTimeout(() => set('q', text.trim()), 300)
    return () => clearTimeout(t)
  }, [text])

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [access, setAccess] = useState<AccessLevel>('card_only')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<string>()
  const items = works.data?.items ?? []
  const selectable = items.filter((w) => !w.errors.length)
  const chosen = items.filter((w) => selected.has(w.id))
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const exportTable = (format: 'xlsx' | 'csv') => exportWorks(query, format).catch((e) => setReport(e instanceof Error ? e.message : 'Ошибка'))

  async function bulk(action: 'publish' | 'access') {
    setBusy(true)
    setReport(undefined)
    try {
      const { done, skipped } = await bulkWorks(chosen.map((w) => w.id), action, access)
      setSelected(new Set())
      setReport(`Готово: ${done}.${skipped.length ? ` Пропущено: ${skipped.length} (${skipped.slice(0, 3).join('; ')}${skipped.length > 3 ? '…' : ''})` : ''}`)
    } catch (e) {
      setReport(e instanceof Error ? e.message : 'Ошибка')
    } finally {
      setBusy(false)
    }
    qc.invalidateQueries({ predicate: (x) => ['admin-works', 'works', 'facets', 'stats', 'person', 'personWorks', 'work', 'staff'].includes(String(x.queryKey[0])) })
  }

  return (
    <div className={cn('space-y-4', chosen.length > 0 && 'pb-20')}>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-4xl font-bold">Все работы</h1>
        <Button variant="outline" onClick={() => exportTable('xlsx')}>Выгрузить xlsx</Button>
        <Button variant="outline" onClick={() => exportTable('csv')}>csv</Button>
        {import.meta.env.VITE_USE_MOCKS !== 'true' && <a href={worksZipUrl(query)} className={btn('outline')}>С файлами (zip)</a>}
        <Link to="/admin/works/new" className={btn('primary')}>Добавить работу</Link>
      </div>

      <div className="flex flex-wrap gap-2">
        <Input type="search" aria-label="Поиск" placeholder="Поиск по названию, автору, файлу" value={text} onChange={(e) => setText(e.target.value)} className="w-full sm:w-72" />
        <Select aria-label="Тип" value={query.type} onChange={(e) => set('type', e.target.value)} className="w-auto">
          <option value="">Все типы</option>
          {(['vkr', 'coursework', 'publication', 'talk'] as WorkType[]).map((t) => <option key={t} value={t}>{workTypePlural[t]}</option>)}
        </Select>
        <Select aria-label="Статус" value={query.status} onChange={(e) => set('status', e.target.value)} className="w-auto">
          <option value="">Все статусы</option>
          {(Object.keys(statusLabels) as WorkStatus[]).map((s) => <option key={s} value={s}>{statusLabels[s]}</option>)}
        </Select>
        <Select aria-label="Год" value={query.year} onChange={(e) => set('year', e.target.value)} className="w-auto">
          <option value="">Все годы</option>
          {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
        </Select>
        <Select aria-label="Импорт" value={query.importId} onChange={(e) => set('importId', e.target.value)} className="w-auto max-w-[14rem]">
          <option value="">Все импорты</option>
          {imports.data?.map((i) => <option key={i.id} value={i.id}>{importOption(i)}</option>)}
        </Select>
      </div>

      {chosen.length > 0 && (
        // Floats at the bottom, so selecting a row does not push the table down.
        <div className="fixed inset-x-0 bottom-0 z-20 border-t-2 border-brand bg-panel">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2 px-4 py-3 text-sm">
            <span className="mr-2 font-medium">Выбрано: {chosen.length}</span>
            {chosen.some((w) => w.status === 'draft') && <Button size="sm" onClick={() => bulk('publish')} loading={busy}>Опубликовать черновики</Button>}
            <Select aria-label="Новый уровень доступа" value={access} onChange={(e) => setAccess(e.target.value as AccessLevel)} className="h-9 w-auto">
              {Object.entries(accessLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
            <Button size="sm" variant="outline" onClick={() => bulk('access')} loading={busy}>Изменить доступ</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Снять выбор</Button>
          </div>
        </div>
      )}
      {report && <Alert tone="blue">{report}</Alert>}

      {works.isError ? <ErrorState error={works.error} onRetry={() => works.refetch()} />
        : works.isPending ? <Spinner />
        : items.length === 0 ? <EmptyState>Работ не найдено</EmptyState>
        : (
          <Card className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead className="border-b-2 border-brand text-left">
                <tr>
                  <th className="w-10 p-3">
                    <input type="checkbox" className="h-4 w-4 accent-brand" aria-label="Выбрать все без ошибок"
                      checked={selectable.length > 0 && selectable.every((w) => selected.has(w.id))}
                      onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((w) => w.id)) : new Set())} />
                  </th>
                  <th className="p-3">Работа</th><th className="p-3">Авторы</th><th className="p-3">Год</th><th className="p-3">Статус</th><th className="p-3">Доступ</th><th className="hidden p-3 xl:table-cell">Изменена</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {items.map((w) => {
                  const errs = w.errors
                  return (
                    <tr key={w.id} className="hover:bg-panel">
                      <td className="p-3">
                        <input type="checkbox" className="h-4 w-4 accent-brand" aria-label={`Выбрать «${w.title}»`} disabled={errs.length > 0}
                          title={errs.length ? `Есть ошибки: ${errs.map((e) => e.message).join('; ')}` : undefined} checked={selected.has(w.id)} onChange={() => toggle(w.id)} />
                      </td>
                      <td className="min-w-[14rem] p-3">
                        <span className="flex items-start gap-2">
                          <TypeIcon type={w.type} className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
                          <Link to={`/admin/works/${w.id}`} className="font-medium text-ink hover:underline">{w.title || w.originalFileName || 'Без названия'}</Link>
                        </span>
                      </td>
                      <td className="max-w-[10rem] truncate p-3 text-ink-2">{w.authors.map((a) => a.displayName).join(', ')}</td>
                      <td className="p-3">{w.year}</td>
                      <td className="p-3"><StatusBadge status={w.status} /></td>
                      <td className="p-3 text-xs text-ink-2">{accessLabels[w.accessLevel]}</td>
                      <td className="hidden whitespace-nowrap p-3 text-xs text-muted xl:table-cell">{formatDateTime(w.updatedAt)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        )}
      {works.data && <Pagination page={page} total={works.data.total} pageSize={PAGE} onChange={(n) => setSp((p) => { const x = new URLSearchParams(p); x.set('page', String(n)); return x })} />}
    </div>
  )
}
