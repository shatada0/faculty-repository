import { useEffect } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ApiError, getPerson, getPersonMetrics, getPersonWorks } from '@/api'
import type { PublicWork } from '@/api/types'
import { NotFound } from '@/components/Layout'
import { WorkCard, WorkCardSkeleton } from '@/components/WorkCard'
import { EmptyState, ErrorState, SectionTitle, Select, Skeleton, Tabs, Tiles } from '@/components/ui'

function groupByYear(items: PublicWork[]) {
  const m = new Map<number, PublicWork[]>()
  for (const w of items) m.set(w.year, [...(m.get(w.year) ?? []), w])
  return [...m].sort((a, b) => b[0] - a[0])
}

/** Page of a teacher, student or graduate. Tiles, tabs and their filters come from the server. */
export default function Person() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const set = (patch: Record<string, string>, reset = false) => setSp((p) => {
    const n = reset ? new URLSearchParams() : new URLSearchParams(p)
    for (const [k, v] of Object.entries(patch)) if (v) n.set(k, v); else n.delete(k)
    return n
  }, { replace: true })

  const person = useQuery({ queryKey: ['person', id], queryFn: () => getPerson(id) })
  // Merged profiles answer with the surviving profile: move the URL there.
  useEffect(() => {
    if (person.data && person.data.id !== id) navigate(`/people/${person.data.id}${window.location.search}`, { replace: true })
  }, [person.data, id, navigate])

  const ready = !!person.data && person.data.id === id
  const tabs = person.data?.tabs ?? []
  const t = tabs.find((x) => x.key === sp.get('tab')) ?? tabs[0]
  const filters = Object.fromEntries((t?.filters ?? []).map((f) => [f.key, sp.get(f.key) ?? '']))
  const query = { ...t?.query, ...filters }
  const works = useQuery({
    queryKey: ['personWorks', id, query],
    queryFn: () => getPersonWorks(id, query),
    placeholderData: keepPreviousData,
    enabled: ready && !!t,
  })
  const year = sp.get('year') ?? ''
  const metrics = useQuery({ queryKey: ['person', id, 'metrics', year], queryFn: () => getPersonMetrics(id, { year }), placeholderData: keepPreviousData, enabled: ready })

  if (person.isError) return person.error instanceof ApiError && person.error.status === 404
    ? <NotFound text="Страница не найдена" />
    : <ErrorState error={person.error} onRetry={() => person.refetch()} />
  if (person.isPending) return <div className="space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>

  const p = person.data
  const openYear = (y: number, key = 'publications') => set({ tab: key, year: String(y) }, true)

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <nav aria-label="Навигационная цепочка" className="flex flex-wrap gap-1.5 text-sm text-ink-2">
          {p.kind === 'staff'
            ? <><Link to="/people">Преподаватели</Link><span aria-hidden>›</span>{p.department && <Link to={`/people?department=${encodeURIComponent(p.department)}`}>{p.department}</Link>}</>
            : <Link to="/works">Работы</Link>}
        </nav>
        <header className="space-y-1">
          <h1 className="text-4xl font-bold leading-tight">{p.fullName}</h1>
          {p.headline && <p className="text-lg first-letter:uppercase">{p.headline}</p>}
          <p className="text-ink-2">
            {[p.academicDegree, p.academicTitle].filter(Boolean).join(', ')}
            {p.orcid && <>{(p.academicDegree || p.academicTitle) && ' · '}<a href={`https://orcid.org/${p.orcid}`} target="_blank" rel="noreferrer">ORCID {p.orcid}</a></>}
          </p>
        </header>
      </div>

      {p.tiles.length > 0 && <Tiles items={p.tiles} />}

      {metrics.data && metrics.data.items.length > 0 && (
        <section className="space-y-3">
          <SectionTitle action={
            <label className="flex items-center gap-2 text-ink-2">Работы
              <Select value={year} onChange={(e) => set({ year: e.target.value })} className="h-9 w-auto" aria-label="Год работ для цитирования">
                <option value="">все годы</option>
                {metrics.data.years.map((y) => <option key={y} value={y}>{y}</option>)}
              </Select>
            </label>
          }>Цитирование</SectionTitle>
          <Tiles items={metrics.data.items} className={metrics.isPlaceholderData ? 'opacity-60' : undefined} />
        </section>
      )}

      {p.byYear.length > 0 && (
        <section>
          <SectionTitle action={
            <span className="flex gap-4 text-sm">
              <span className="flex items-center gap-1.5"><span aria-hidden className="h-3 w-3 bg-brand" />публикации</span>
              <span className="flex items-center gap-1.5"><span aria-hidden className="h-3 w-3 bg-accent" />выступления</span>
            </span>
          }>Публикации и выступления по годам</SectionTitle>
          <div className="mt-3 h-52" aria-hidden>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={p.byYear} margin={{ left: -24, right: 0, top: 8 }} barCategoryGap="30%">
                <XAxis dataKey="year" tick={{ fontSize: 13, fill: '#4d4d4d' }} tickLine={false} axisLine={{ stroke: '#1f3a5f' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#666666' }} tickLine={false} axisLine={false} />
                <Tooltip cursor={{ fill: '#eef2f7' }} contentStyle={{ borderRadius: 0, border: '1px solid #1f3a5f' }} />
                <Bar dataKey="publication" name="Публикации" stackId="a" fill="#1f3a5f" cursor="pointer" isAnimationActive={false} onClick={(d: { payload?: { year: number } }) => d.payload && openYear(d.payload.year)} />
                <Bar dataKey="talk" name="Выступления" stackId="a" fill="#d42a3d" cursor="pointer" isAnimationActive={false} onClick={(d: { payload?: { year: number } }) => d.payload && openYear(d.payload.year, 'talks')} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-sm text-ink-2">
            <span>Работы за год:</span>
            {p.byYear.map(({ year: y }) => <button key={y} type="button" onClick={() => openYear(y)} className="underline underline-offset-4 hover:text-accent">{y}</button>)}
          </p>
        </section>
      )}

      <div className="flex flex-wrap items-start gap-x-14 gap-y-10">
        <section className="min-w-0 flex-[999_1_34rem]">
          {!t ? <EmptyState>Пока нет опубликованных работ</EmptyState> : (
            <>
              <Tabs value={t.key} onChange={(v) => set({ tab: v === tabs[0].key ? '' : v }, true)}
                items={tabs.map((x) => ({ value: x.key, label: <>{x.label}<span className="ml-1.5 font-normal text-muted">{x.count}</span></> }))} />

              <div className="flex flex-wrap gap-x-6 gap-y-2 py-4">
                {t.filters.map((f) => (
                  <label key={f.key} className="flex items-center gap-2 text-ink-2">{f.label}
                    <Select value={filters[f.key]} onChange={(e) => set({ [f.key]: e.target.value })} className="h-9 w-auto">
                      <option value="">{f.all}</option>
                      {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </Select>
                  </label>
                ))}
              </div>

              {works.isError ? <ErrorState error={works.error} onRetry={() => works.refetch()} />
                : !works.data ? Array.from({ length: 3 }, (_, i) => <WorkCardSkeleton key={i} />)
                : works.data.items.length === 0 ? <EmptyState>Нет работ{Object.values(filters).some(Boolean) && ' с такими фильтрами'}</EmptyState>
                : (
                  <div className={works.isPlaceholderData ? 'opacity-60' : undefined}>
                    {groupByYear(works.data.items).map(([y, items]) => (
                      <section key={y} className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-4 border-t-2 border-brand">
                        <h3 className="pt-3 text-2xl font-bold">{y}</h3>
                        <div>{items.map((w) => <WorkCard key={w.id} work={w} />)}</div>
                      </section>
                    ))}
                  </div>
                )}
            </>
          )}
        </section>

        {p.coauthors.length > 0 && (
          <aside className="min-w-0 flex-[1_1_16rem]">
            <SectionTitle>Чаще всего пишет с</SectionTitle>
            <ul>
              {p.coauthors.map((c) => (
                <li key={c.id} className="flex min-h-11 items-center justify-between gap-3 border-b border-rule">
                  <Link to={`/people/${c.id}`}>{c.fullName}</Link>
                  <span className="text-ink-2">{c.count}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-sm text-muted">Число общих публикаций и докладов</p>
          </aside>
        )}
      </div>
    </div>
  )
}
