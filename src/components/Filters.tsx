import { useState } from 'react'
import type { Facet, Facets } from '@/api/types'
import { FILTER_LABELS, type FilterKey } from '@/lib/catalog'
import { Skeleton } from './ui'

const TOP = 8

function FacetGroup({ title, facets, selected, onToggle }: { title: string; facets: Facet[]; selected: string[]; onToggle: (v: string) => void }) {
  const [all, setAll] = useState(false)
  const shown = all ? facets : facets.filter((f, i) => i < TOP || selected.includes(f.value))
  return (
    <fieldset className="border-b border-rule py-3.5">
      <legend className="sr-only">{title}</legend>
      <p aria-hidden className="mb-1.5 font-bold">{title}</p>
      {shown.map((f) => (
        <label key={f.value} className="flex min-h-8 cursor-pointer items-start gap-2.5 py-0.5 text-[15px]">
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand" checked={selected.includes(f.value)} onChange={() => onToggle(f.value)} />
          <span className="leading-snug">{f.label} <span className="text-muted">({f.count})</span></span>
        </label>
      ))}
      {facets.length > TOP && (
        <button type="button" className="mt-1 text-sm underline underline-offset-4 hover:text-accent" onClick={() => setAll(!all)}>
          {all ? 'свернуть' : `показать все (${facets.length})`}
        </button>
      )}
    </fieldset>
  )
}

export function Filters({ keys, facets, selected, onToggle, onReset, loading }: {
  keys: FilterKey[]; facets?: Facets; selected: (k: FilterKey) => string[]; onToggle: (k: FilterKey, v: string) => void
  onReset?: () => void; loading?: boolean
}) {
  if (loading && !facets) return <div className="space-y-3">{keys.map((k) => <Skeleton key={k} className="h-24 w-full" />)}</div>
  const chosen = keys.flatMap((k) => selected(k).map((v) => ({ k, v, label: facets?.[k]?.find((f) => f.value === v)?.label ?? v })))
  return (
    <div>
      <h2 className="border-b-2 border-brand pb-1.5 text-xl font-bold">Уточнить</h2>
      {chosen.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-rule py-3 text-[15px]">
          <span className="text-ink-2">Выбрано:</span>
          {chosen.map((c) => (
            <button key={`${c.k}:${c.v}`} type="button" onClick={() => onToggle(c.k, c.v)} aria-label={`Убрать фильтр «${c.label}»`}
              className="bg-brand px-2 py-0.5 text-sm text-white hover:bg-accent">{c.label} ×</button>
          ))}
          {onReset && <button type="button" onClick={onReset} className="text-sm underline underline-offset-4 hover:text-accent">сбросить всё</button>}
        </div>
      )}
      {keys.map((k) => facets?.[k]?.length
        ? <FacetGroup key={k} title={FILTER_LABELS[k]} facets={facets[k]} selected={selected(k)} onToggle={(v) => onToggle(k, v)} />
        : null)}
    </div>
  )
}
