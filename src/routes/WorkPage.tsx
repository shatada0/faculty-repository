import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError, getMaterialLink, getWork, getWorkFile, getWorks } from '@/api'
import type { DetailRow, FileLink, PublicWork, StudentWork } from '@/api/types'
import { Citation, useCopy } from '@/components/CitationButton'
import { NotFound } from '@/components/Layout'
import { Authors, ViewText, WorkCard } from '@/components/WorkCard'
import { Alert, Button, ErrorState, SectionTitle, Skeleton, btn } from '@/components/ui'
import { workTypePlural } from '@/lib/labels'
import { openSigned } from '@/lib/open'

const Row = ({ row }: { row: DetailRow }) => (
  <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b border-rule py-2.5">
    <dt className="text-muted">{row.label}</dt><dd>{row.people ? <Authors people={row.people} /> : <ViewText item={row} />}</dd>
  </div>
)

function Related({ id, title }: { id: string; title: string }) {
  const q = useQuery({ queryKey: ['work', id], queryFn: () => getWork(id) })
  if (q.isError) return null
  return (
    <section>
      <h2 className="mb-2 text-xl font-bold">{title}</h2>
      {q.isPending ? <Skeleton className="h-16" /> : <WorkCard work={q.data} className="border-t border-rule" />}
    </section>
  )
}

function SupervisorOther({ w }: { w: StudentWork & PublicWork }) {
  const sup = w.supervisors.find((s) => s.personId)
  const q = useQuery({
    queryKey: ['works', { type: w.type, supervisor: sup?.personId, exclude: w.id, pageSize: 4 }],
    queryFn: () => getWorks({ type: w.type, supervisor: sup?.personId, exclude: w.id, pageSize: 4 }),
    enabled: !!sup,
  })
  const items = q.data?.items ?? []
  if (!sup || !items.length) return null
  return (
    <section>
      <SectionTitle action={<Link to={`/people/${sup.personId}?tab=${w.type}`} className={btn('link', 'sm')}>Все работы</Link>}>
        Другие работы руководителя
      </SectionTitle>
      {items.map((x) => <WorkCard key={x.id} work={x} />)}
    </section>
  )
}

export default function WorkPage() {
  const { id = '' } = useParams()
  const q = useQuery({ queryKey: ['work', id], queryFn: () => getWork(id) })
  const { copied, copy } = useCopy()
  const [fileErr, setFileErr] = useState<string>()
  const [opening, setOpening] = useState(false)

  if (q.isError) return q.error instanceof ApiError && q.error.status === 404
    ? <NotFound text="Работа не найдена" />
    : <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (q.isPending) return <div className="space-y-3"><Skeleton className="h-4 w-40" /><Skeleton className="h-10 w-3/4" /><Skeleton className="h-40 w-full" /></div>

  const w = q.data
  const v = w.view

  async function open(getLink: () => Promise<FileLink>) {
    setFileErr(undefined)
    setOpening(true)
    try {
      await openSigned(getLink)
    } catch (e) {
      setFileErr(e instanceof Error ? e.message : 'Не удалось открыть файл')
    } finally {
      setOpening(false)
    }
  }

  return (
    <div className="space-y-6">
      <nav aria-label="Навигационная цепочка" className="flex flex-wrap gap-1.5 text-sm text-ink-2">
        <Link to="/works">Работы</Link><span aria-hidden>›</span><Link to={`/works?type=${w.type}`}>{workTypePlural[w.type]}</Link>
      </nav>

      <header className="max-w-4xl space-y-2.5">
        <p className="text-[15px] text-ink-2">{v.kind}, {w.year}</p>
        <h1 className="text-4xl font-bold leading-tight">{w.title}</h1>
        <p className="text-lg"><Authors people={v.authors} /></p>
        <div className="flex flex-wrap items-center gap-2.5 pt-2">
          {v.fileButton && <Button onClick={() => open(() => getWorkFile(w.id))} loading={opening}>{v.fileButton}</Button>}
          <a href="#cite" className={btn(v.fileButton ? 'outline' : 'primary')}>Цитировать</a>
          {v.external && <a href={v.external.href} target="_blank" rel="noreferrer" className={btn('outline')}>{v.external.label}</a>}
          <Button variant="outline" onClick={() => copy(window.location.href)}>{copied ? 'Ссылка скопирована' : 'Скопировать ссылку'}</Button>
          {v.closedNote && <span className="text-[15px] text-ink-2">{v.closedNote}</span>}
        </div>
        {fileErr && <Alert tone="red">{fileErr}</Alert>}
      </header>

      <div className="flex flex-wrap items-start gap-x-14 gap-y-8 border-t-2 border-brand pt-6">
        <div className="min-w-0 flex-[999_1_34rem] space-y-8">
          {w.annotation && (
            <section>
              <h2 className="mb-2 text-xl font-bold">Аннотация</h2>
              <p className="max-w-3xl text-lg leading-relaxed">{w.annotation}</p>
            </section>
          )}
          {w.keywords.length > 0 && (
            <section>
              <h2 className="mb-2 text-xl font-bold">Ключевые слова</h2>
              <p>{w.keywords.map((k, i) => <span key={k}>{i > 0 && ', '}<Link to={`/works?q=${encodeURIComponent(k)}`}>{k}</Link></span>)}</p>
            </section>
          )}
          {v.materials.length > 0 && (
            <section>
              <h2 className="mb-2 text-xl font-bold">Материалы</h2>
              <ul className="space-y-1">
                {v.materials.map((m) => (
                  <li key={m.id}><button type="button" className="text-left underline underline-offset-4 hover:text-accent" onClick={() => open(() => getMaterialLink(w.id, m.id))}>{m.label}</button></li>
                ))}
              </ul>
            </section>
          )}
          {w.type === 'publication' && w.relatedTalkId && <Related id={w.relatedTalkId} title="Доклад по этой работе" />}
          {w.type === 'talk' && w.relatedPublicationId && <Related id={w.relatedPublicationId} title="Публикация по докладу" />}
          <Citation workId={w.id} />
        </div>

        <aside className="min-w-0 flex-[1_1_18rem]" aria-label="Сведения о работе">
          <h2 className="mb-1 text-xl font-bold">Сведения</h2>
          <dl>{v.details.map((r) => <Row key={r.label} row={r} />)}</dl>
        </aside>
      </div>

      {(w.type === 'vkr' || w.type === 'coursework') && <SupervisorOther w={w} />}
    </div>
  )
}
