// Search input with suggestions: ARIA combobox + listbox. The server ranks and marks matches; this only shows them.
import { useEffect, useId, useState, type ComponentProps, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getSuggestions } from '@/api'
import type { Suggestions } from '@/api/types'
import { workTypeLabels } from '@/lib/labels'
import { Input, cn } from './ui'

type Option = { id: string; to: string; label: ReactNode; note: string }

const marked = (parts: Suggestions['works'][number]['parts']) =>
  parts.map((p, i) => (p.hit ? <mark key={i} className="bg-transparent font-bold text-ink">{p.text}</mark> : p.text))

/** Enter on a highlighted suggestion opens it; Enter without one submits the surrounding form (full results). */
export function SearchBox({ value, onChange, className, inputClassName, ...input }: Omit<ComponentProps<'input'>, 'value' | 'onChange'> & {
  value: string; onChange: (v: string) => void; inputClassName?: string
}) {
  const navigate = useNavigate()
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [debounced, setDebounced] = useState(value.trim())
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value.trim()), 180)
    return () => clearTimeout(t)
  }, [value])
  const res = useQuery({
    queryKey: ['suggest', debounced],
    queryFn: () => getSuggestions(debounced),
    enabled: open && debounced.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
  const works: Option[] = (res.data?.works ?? []).map((w) => ({
    id: `${listId}-w-${w.id}`, to: `/works/${w.id}`, label: marked(w.parts), note: [workTypeLabels[w.type], w.year].filter(Boolean).join(', '),
  }))
  const people: Option[] = (res.data?.people ?? []).map((p) => ({ id: `${listId}-p-${p.id}`, to: `/people/${p.id}`, label: p.fullName, note: 'преподаватель' }))
  const options = [...works, ...people]
  const shown = open && debounced.length > 0 && options.length > 0

  function choose(o: Option) {
    setOpen(false)
    navigate(o.to)
  }
  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    const n = options.length
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      if (n) setActive((a) => (e.key === 'ArrowDown' ? (a + 1) % n : (a <= 0 ? n : a) - 1))
    } else if (e.key === 'Enter') {
      if (shown && options[active]) { e.preventDefault(); choose(options[active]) } else setOpen(false)
    } else if (e.key === 'Escape' && shown) {
      e.preventDefault()
      setOpen(false)
    }
  }
  const group = (label: string, items: Option[], offset: number) => items.length > 0 && (
    <div role="group" aria-label={label}>
      <div role="presentation" className="px-3 pt-2 pb-1 text-xs font-bold uppercase tracking-[0.08em] text-muted">{label}</div>
      {items.map((o, i) => (
        <div key={o.id} id={o.id} role="option" aria-selected={active === offset + i}
          onMouseDown={(e) => e.preventDefault()} onClick={() => choose(o)} onMouseMove={() => setActive(offset + i)}
          className={cn('cursor-pointer px-3 py-1.5', active === offset + i && 'bg-panel-2')}>
          <span className="block">{o.label}</span>
          <span className="block text-sm text-muted">{o.note}</span>
        </div>
      ))}
    </div>
  )

  return (
    <div className={cn('relative', className)}>
      <Input {...input} type="search" role="combobox" autoComplete="off" value={value} className={inputClassName}
        aria-autocomplete="list" aria-expanded={shown} aria-controls={listId}
        aria-activedescendant={shown && options[active] ? options[active].id : undefined}
        onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(-1) }}
        onKeyDown={onKeyDown} onBlur={() => setOpen(false)} onFocus={() => value.trim() && setOpen(true)} />
      <div id={listId} role="listbox" aria-label="Подсказки" hidden={!shown}
        className="absolute inset-x-0 top-full z-20 max-h-[70vh] overflow-y-auto border border-t-0 border-brand bg-white pb-1 text-left text-base">
        {group('Работы', works, 0)}
        {group('Люди', people, works.length)}
      </div>
    </div>
  )
}
