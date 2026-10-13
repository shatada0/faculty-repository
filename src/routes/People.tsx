import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getDepartments, getStaff } from '@/api'
import { EmptyState, ErrorState, Input, Select, Skeleton } from '@/components/ui'
import { plural } from '@/lib/format'

/** Departments with staff and work counts, as the server counts them. */
export const useDepartments = () => useQuery({ queryKey: ['staff', 'departments'], queryFn: getDepartments, staleTime: 5 * 60_000 })

export default function People() {
  const [sp, setSp] = useSearchParams()
  const q = sp.get('q') ?? ''
  const department = sp.get('department') ?? ''
  const sort = sp.get('sort') ?? 'name'
  const set = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n }, { replace: true })
  const departments = useDepartments().data?.items ?? []
  const [text, setText] = useState(q)
  useEffect(() => {
    if (text.trim() === q) return
    const t = setTimeout(() => set('q', text.trim()), 300)
    return () => clearTimeout(t)
  }, [text])
  const res = useQuery({ queryKey: ['staff', { q, department, sort }], queryFn: () => getStaff({ q, department, sort }), placeholderData: keepPreviousData })

  return (
    <div className="space-y-5">
      <h1 className="text-4xl font-bold">Преподаватели</h1>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <label className="sr-only" htmlFor="people-q">Поиск по ФИО</label>
        <Input id="people-q" type="search" placeholder="Поиск по ФИО" value={text} onChange={(e) => setText(e.target.value)} className="max-w-xs border-brand" />
        <label className="flex items-center gap-2 text-ink-2">Кафедра
          <Select value={department} onChange={(e) => set('department', e.target.value)} className="w-auto">
            <option value="">все</option>
            {departments.map((d) => <option key={d.name}>{d.name}</option>)}
          </Select>
        </label>
        <label className="flex items-center gap-2 text-ink-2">Сортировка
          <Select value={sort} onChange={(e) => set('sort', e.target.value === 'name' ? '' : e.target.value)} className="w-auto">
            <option value="name">по ФИО</option>
            <option value="works">по числу работ</option>
          </Select>
        </label>
      </div>

      {res.isError ? <ErrorState error={res.error} onRetry={() => res.refetch()} />
        : res.isPending ? <Skeleton className="h-96" />
        : res.data.items.length === 0 ? <EmptyState>Никого не нашли</EmptyState>
        : (
          <ul className="border-t-2 border-brand">
            {res.data.items.map((p) => (
              <li key={p.id} className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-rule py-4">
                <div className="min-w-0">
                  <Link to={`/people/${p.id}`} className="text-lg font-bold no-underline hover:underline">{p.fullName}</Link>
                  <p className="text-ink-2">{[p.position, p.academicDegree].filter(Boolean).join(', ')}</p>
                </div>
                <p className="text-[15px] text-ink-2">
                  <b className="text-ink">{p.counts.publication}</b> {plural(p.counts.publication, ['публикация', 'публикации', 'публикаций'])} ·{' '}
                  <b className="text-ink">{p.counts.talk}</b> {plural(p.counts.talk, ['выступление', 'выступления', 'выступлений'])}
                </p>
              </li>
            ))}
          </ul>
        )}
    </div>
  )
}
