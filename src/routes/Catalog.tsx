import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getFacets, getWorks } from '@/api'
import { Filters } from '@/components/Filters'
import { WorkCard, WorkCardSkeleton } from '@/components/WorkCard'
import { Button, Dialog, EmptyState, ErrorState, Input, Pagination, Select, Tabs } from '@/components/ui'
import { ALL_FILTERS, TAB_FILTERS, TABS, type FilterKey, type Tab } from '@/lib/catalog'
import { workTypePlural } from '@/lib/labels'
import { plural } from '@/lib/format'

const PAGE_SIZE = 20

export default function Catalog() {
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.includes(sp.get('type') as Tab) ? sp.get('type') : 'all') as Tab
  const q = sp.get('q') ?? ''
  const keys = TAB_FILTERS[tab]
  const sort = sp.get('sort') ?? (q ? 'relevance' : 'new')
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const filters = Object.fromEntries(keys.map((k) => [k, sp.getAll(k)]))
  const activeCount = keys.reduce((n, k) => n + filters[k].length, 0)

  const base = { type: tab === 'all' ? undefined : tab, q, ...filters }
  const works = useQuery({
    queryKey: ['works', { ...base, sort, page }],
    queryFn: () => getWorks({ ...base, sort, page, pageSize: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })
  const facets = useQuery({ queryKey: ['facets', base], queryFn: () => getFacets(base), placeholderData: keepPreviousData })

  // ---- URL updates ----
  function update(mut: (p: URLSearchParams) => void, replace = false) {
    setSp((prev) => {
      const next = new URLSearchParams(prev)
      mut(next)
      next.delete('page')
      return next
    }, { replace })
  }
  const setTab = (t: Tab) => update((p) => {
    if (t === 'all') p.delete('type'); else p.set('type', t)
    for (const k of ALL_FILTERS) if (!TAB_FILTERS[t].includes(k)) p.delete(k)
  })
  const toggle = (k: FilterKey, v: string) => update((p) => {
    const cur = p.getAll(k)
    p.delete(k)
    for (const x of cur.includes(v) ? cur.filter((c) => c !== v) : [...cur, v]) p.append(k, x)
  })
  const reset = () => update((p) => { for (const k of ALL_FILTERS) p.delete(k); p.delete('q') })

  // ---- debounced search box (300 ms) ----
  const [text, setText] = useState(q)
  const pushed = useRef(q)
  useEffect(() => {
    if (q !== pushed.current) { pushed.current = q; setText(q) }
  }, [q])
  useEffect(() => {
    if (text.trim() === pushed.current) return
    const t = setTimeout(() => {
      pushed.current = text.trim()
      update((p) => (text.trim() ? p.set('q', text.trim()) : p.delete('q')), true)
    }, 300)
    return () => clearTimeout(t)
  }, [text])

  const [drawer, setDrawer] = useState(false)
  const byType = facets.data?.type
  const tabCount = (t: Tab) => byType && (t === 'all' ? byType.reduce((n, f) => n + f.count, 0) : byType.find((f) => f.value === t)?.count)

  const filtersEl = (
    <Filters keys={keys} facets={facets.data} loading={facets.isPending} selected={(k) => filters[k]} onToggle={toggle} onReset={activeCount > 0 ? reset : undefined} />
  )
  const offset = (page - 1) * PAGE_SIZE

  return (
    <div className="space-y-5">
      <h1 className="text-4xl font-bold">Работы</h1>
      <form role="search" className="flex max-w-3xl flex-wrap" onSubmit={(e) => {
        e.preventDefault()
        pushed.current = text.trim()
        update((p) => (text.trim() ? p.set('q', text.trim()) : p.delete('q')))
      }}>
        <label htmlFor="catalog-q" className="sr-only">Поиск по работам</label>
        <Input id="catalog-q" type="search" value={text} onChange={(e) => setText(e.target.value)} className="h-12 flex-[1_1_18rem] border-brand text-lg"
          placeholder="тема, автор, руководитель, журнал или конференция" />
        <Button type="submit" className="h-12 px-7">Найти</Button>
      </form>

      <Tabs
        value={tab}
        onChange={setTab}
        items={TABS.map((t) => ({
          value: t,
          label: <>{t === 'all' ? 'Все' : workTypePlural[t]}{tabCount(t) !== undefined && <span className="ml-1.5 font-normal text-muted">{tabCount(t)}</span>}</>,
        }))}
      />

      <div className="flex gap-12">
        <aside className="hidden w-60 shrink-0 lg:block" aria-label="Фильтры">{filtersEl}</aside>

        <section className="min-w-0 flex-1" aria-live="polite" aria-busy={works.isFetching}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b-2 border-brand pb-2">
            <p className="mr-auto">
              {works.data && <><b>{works.data.total}</b> {plural(works.data.total, ['работа', 'работы', 'работ'])}</>}
            </p>
            <Button variant="link" size="sm" className="lg:hidden" onClick={() => setDrawer(true)}>
              Фильтры{activeCount > 0 && ` (${activeCount})`}
            </Button>
            <label className="flex items-center gap-2 text-ink-2">
              <span className="hidden sm:inline">Сортировка</span>
              <Select value={sort} onChange={(e) => update((p) => p.set('sort', e.target.value))} className="h-9 w-auto" aria-label="Сортировка">
                {q && <option value="relevance">по релевантности</option>}
                <option value="new">сначала новые</option>
                <option value="title">по названию</option>
              </Select>
            </label>
          </div>

          {works.isError ? <div className="mt-4"><ErrorState error={works.error} onRetry={() => works.refetch()} /></div>
            : works.isPending ? Array.from({ length: 5 }, (_, i) => <WorkCardSkeleton key={i} />)
            : works.data.items.length === 0 ? (
              <div className="mt-4">
                <EmptyState action={<Button variant="outline" onClick={reset}>Сбросить фильтры</Button>}>
                  <p className="text-xl font-bold text-ink">Ничего не найдено</p>
                  <p>Попробуйте изменить запрос или убрать часть фильтров.</p>
                </EmptyState>
              </div>
            ) : (
              <ol className={works.isPlaceholderData ? 'opacity-60' : undefined}>
                {works.data.items.map((w, i) => <li key={w.id}><WorkCard work={w} lead={`${offset + i + 1}.`} narrowLead /></li>)}
              </ol>
            )}

          {works.data && (
            <div className="mt-5">
              <Pagination page={page} total={works.data.total} pageSize={PAGE_SIZE} onChange={(n) => {
                const p = new URLSearchParams(sp)
                if (n > 1) p.set('page', String(n)); else p.delete('page')
                setSp(p)
                window.scrollTo({ top: 0 })
              }} />
            </div>
          )}
        </section>
      </div>

      <Dialog open={drawer} onClose={() => setDrawer(false)} title="Фильтры" side>
        {filtersEl}
        <Button className="mt-6 w-full" onClick={() => setDrawer(false)}>Показать</Button>
      </Dialog>
    </div>
  )
}
