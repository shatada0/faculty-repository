// Rubrics of a work: chosen ones as chips, new ones picked from the server's classifier (ГРНТИ, ОЕСР, WoS, ASJC).
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { searchClassifier } from '@/api'
import type { Subject, SubjectScheme } from '@/api/types'
import { subjectSchemeLabels } from '@/lib/labels'
import { Input, useDebounced } from './ui'

function SchemeRow({ scheme, value, onChange }: { scheme: SubjectScheme; value: Subject[]; onChange: (v: Subject[]) => void }) {
  const [q, setQ] = useState('')
  const debounced = useDebounced(q.trim())
  const [open, setOpen] = useState(false)
  const res = useQuery({ queryKey: ['admin-classifier', scheme, debounced], queryFn: () => searchClassifier(scheme, debounced), enabled: open, staleTime: 5 * 60_000 })
  const mine = value.filter((s) => s.scheme === scheme)
  const found = (res.data ?? []).filter((s) => !mine.some((x) => x.code === s.code))
  const label = subjectSchemeLabels[scheme]
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-bold">{label}</p>
      {mine.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {mine.map((s) => (
            <span key={s.code} className="inline-flex items-center gap-1 bg-panel px-2 py-0.5 text-sm">
              {s.code} {s.name}
              <button type="button" aria-label={`Убрать ${s.code}`} onClick={() => onChange(value.filter((x) => x !== s))}><X className="h-3.5 w-3.5" /></button>
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <Input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
          placeholder="Код или название" className="h-10" aria-label={`Добавить рубрику: ${label}`} />
        {open && found.length > 0 && (
          <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto border border-rule bg-white" role="listbox" aria-label={label}>
            {found.map((s) => (
              <li key={s.code} role="option" aria-selected={false}>
                <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { onChange([...value, s]); setQ(''); setOpen(false) }}
                  className="w-full px-2 py-1.5 text-left text-sm hover:bg-panel">{s.code} {s.name}</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function SubjectsEditor({ schemes, value, onChange }: { schemes: SubjectScheme[]; value: Subject[]; onChange: (v: Subject[]) => void }) {
  return <div className="grid gap-4 sm:grid-cols-2">{schemes.map((s) => <SchemeRow key={s} scheme={s} value={value} onChange={onChange} />)}</div>
}
