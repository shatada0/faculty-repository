import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getLatestWorks, getStats } from '@/api'
import type { WorkType } from '@/api/types'
import { WorkCard, WorkCardSkeleton } from '@/components/WorkCard'
import { Button, ErrorState, Input, SectionTitle, Skeleton, Tiles, btn } from '@/components/ui'
import { formatShortDate, plural } from '@/lib/format'
import { useDepartments } from './People'

const TYPES: WorkType[] = ['vkr', 'coursework', 'publication', 'talk']
const TILE_LABEL: Record<WorkType, [string, string, string]> = {
  vkr: ['ВКР', 'ВКР', 'ВКР'],
  coursework: ['курсовая', 'курсовые', 'курсовых'],
  publication: ['публикация', 'публикации', 'публикаций'],
  talk: ['выступление', 'выступления', 'выступлений'],
}
const EXAMPLES = ['языковые модели', 'составление расписания', 'микросервисы', '09.03.04']

export default function Home() {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const stats = useQuery({ queryKey: ['stats'], queryFn: getStats })
  const latest = useQuery({ queryKey: ['works', 'latest'], queryFn: getLatestWorks })
  const departments = useDepartments()

  return (
    <div className="-mt-6 space-y-10">
      <section className="-mx-4 bg-panel px-4 py-9">
        <form role="search" className="flex max-w-3xl flex-wrap" onSubmit={(e) => { e.preventDefault(); navigate(q.trim() ? `/works?q=${encodeURIComponent(q.trim())}` : '/works') }}>
          <label htmlFor="home-q" className="mb-2 w-full text-2xl font-bold">Найти работу</label>
          <Input id="home-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="тема, автор, руководитель, журнал или конференция" className="h-13 flex-[1_1_18rem] border-brand text-lg" />
          <Button type="submit" className="h-13 px-8">Найти</Button>
        </form>
        <p className="mt-3 text-[15px] text-ink-2">
          Например: {EXAMPLES.map((x, i) => <span key={x}>{i > 0 && ', '}<Link to={`/works?q=${encodeURIComponent(x)}`}>{x}</Link></span>)}
        </p>
      </section>

      <section aria-label="Что есть в репозитории">
        {stats.isPending ? <Skeleton className="h-40" /> : stats.isError ? <ErrorState error={stats.error} onRetry={() => stats.refetch()} /> : (
          <Tiles items={TYPES.map((t) => ({ value: stats.data.byType[t], label: plural(stats.data.byType[t], TILE_LABEL[t]), to: `/works?type=${t}` }))} />
        )}
      </section>

      <div className="flex flex-wrap items-start gap-x-14 gap-y-10">
        <section className="min-w-0 flex-[999_1_34rem]">
          <SectionTitle action={<Link to="/works" className={btn('link', 'sm')}>Все работы</Link>}>Новые поступления</SectionTitle>
          {latest.isError ? <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
            : latest.isPending ? Array.from({ length: 6 }, (_, i) => <WorkCardSkeleton key={i} />)
            : latest.data.map((w) => <WorkCard key={w.id} work={w} lead={w.publishedAt ? formatShortDate(w.publishedAt) : ''} />)}
        </section>

        <aside className="min-w-0 flex-[1_1_18rem] space-y-8">
          <section>
            <SectionTitle>Кафедры</SectionTitle>
            {departments.isPending ? <Skeleton className="mt-3 h-40" /> : departments.data?.items.map((d) => (
              <div key={d.name} className="border-b border-rule py-3.5">
                <Link to={`/people?department=${encodeURIComponent(d.name)}`} className="font-bold">{d.name}</Link>
                <div className="text-sm text-muted">{d.staffCount} {plural(d.staffCount, ['преподаватель', 'преподавателя', 'преподавателей'])} · {d.workCount} {plural(d.workCount, ['работа', 'работы', 'работ'])}</div>
                <div className="mt-1 text-[15px]">
                  {d.people.map((p, i) => <span key={p.id}>{i > 0 && ', '}<Link to={`/people/${p.id}`}>{p.shortName}</Link></span>)}
                  {d.staffCount > d.people.length && ` и ещё ${d.staffCount - d.people.length}`}
                </div>
              </div>
            ))}
            <Link to="/people" className={btn('link', 'sm', 'mt-4')}>Все преподаватели</Link>
          </section>
          <section className="bg-panel px-5 py-4">
            <h2 className="mb-2 text-xl font-bold">О репозитории</h2>
            <p className="text-[15px] text-ink-2">ВКР и курсовые загружает кафедра, публикации и доклады — отдел науки. Текст работы открыт, если автор дал согласие; иначе доступна только карточка.</p>
          </section>
        </aside>
      </div>
    </div>
  )
}
