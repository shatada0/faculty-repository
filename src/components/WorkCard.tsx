import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { BookOpen, FileText, GraduationCap, Mic } from 'lucide-react'
import type { FieldSource, MaterialStatus, PersonLink, PublicWork, ViewItem, WorkStatus, WorkType } from '@/api/types'
import { materialStatusLabels, statusLabels } from '@/lib/labels'
import { Badge, cn, type Tone } from './ui'

export const TypeIcon = ({ type, className = 'h-4 w-4' }: { type: WorkType; className?: string }) => {
  const I = { vkr: GraduationCap, coursework: BookOpen, publication: FileText, talk: Mic }[type]
  return <I className={className} aria-hidden />
}

/** People of a work; the server gives `profileId` to those who have a public profile. */
export function Authors({ people, className }: { people: PersonLink[]; className?: string }) {
  return (
    <span className={className}>
      {people.map((p, i) => (
        <span key={i}>
          {i > 0 && ', '}
          {p.profileId ? <Link to={`/people/${p.profileId}`}>{p.name}</Link> : p.name}
        </span>
      ))}
    </span>
  )
}

/** A text from the server's view, maybe a link, maybe emphasized. */
export const ViewText = ({ item }: { item: ViewItem }) => {
  const text = item.href ? <a href={item.href} target="_blank" rel="noreferrer">{item.text}</a> : item.text
  return item.tone ? <b className={item.tone === 'info' ? 'text-info' : 'text-warn'}>{text}</b> : <>{text}</>
}

const statusTone: Record<WorkStatus, Tone> = { draft: 'amber', published: 'green', rejected: 'red' }
export const StatusBadge = ({ status }: { status: WorkStatus }) => <Badge tone={statusTone[status]}>{statusLabels[status].toLowerCase()}</Badge>

const materialTone: Record<MaterialStatus, Tone> = { pending: 'amber', approved: 'green', rejected: 'red' }
export const MaterialStatusBadge = ({ status }: { status: MaterialStatus }) => <Badge tone={materialTone[status]}>{materialStatusLabels[status].toLowerCase()}</Badge>

/** Where a field value came from, shown next to its label. */
export const FieldSourceBadge = ({ source, confidence, low }: { source: FieldSource; confidence?: number; low?: boolean }) => {
  const text = source === 'ai'
    ? `ИИ${confidence !== undefined ? `, уверенность ${Math.round(confidence * 100)}%` : ''}${low ? ' — проверьте' : ''}`
    : { table: 'из таблицы', doi: 'по DOI', manual: 'вручную', direction: 'по направлению подготовки' }[source]
  return <span className={cn('text-sm italic', low ? 'text-warn' : 'text-muted')}>{text}</span>
}

/** A work as a bibliographic list entry, as the server describes it. `lead` is an optional left column (number or date). */
export function WorkCard({ work: w, lead, narrowLead, className }: { work: PublicWork; lead?: ReactNode; narrowLead?: boolean; className?: string }) {
  const v = w.view
  return (
    <article className={cn('grid gap-x-4 border-b border-rule py-4', lead === undefined ? 'grid-cols-1' : narrowLead ? 'grid-cols-[2.5rem_minmax(0,1fr)]' : 'grid-cols-[4.5rem_minmax(0,1fr)]', className)}>
      {lead !== undefined && <span className="pt-0.5 text-sm text-muted">{lead}</span>}
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-sm text-ink-2">{v.kind}, {w.year}</span>
        <h3 className="text-lg font-bold leading-snug">
          <Link to={`/works/${w.id}`} className="no-underline hover:underline">{w.title}</Link>
        </h3>
        <span>{v.byline.map((g, i) => <span key={i}>{i > 0 && '; '}{g.label && `${g.label} `}<Authors people={g.people} /></span>)}</span>
        <span className="text-[15px] italic text-ink-2">{v.where}</span>
        {v.snippet && <p className="line-clamp-2 text-[15px] text-ink-2">{v.snippet}</p>}
        {v.meta.length > 0 && (
          <span className="text-sm text-ink-2">
            {v.meta.map((m, i) => <span key={i}>{i > 0 && ' · '}<ViewText item={m} /></span>)}
          </span>
        )}
      </div>
    </article>
  )
}

export function WorkCardSkeleton() {
  return (
    <div className="space-y-2 border-b border-rule py-4" aria-hidden>
      <div className="h-3 w-40 animate-pulse bg-panel" />
      <div className="h-5 w-3/4 animate-pulse bg-panel" />
      <div className="h-3 w-1/2 animate-pulse bg-panel" />
    </div>
  )
}
