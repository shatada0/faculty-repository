// Ordered author list with drag-and-drop, linking to people in the system, and inline person creation.
import { useEffect, useState } from 'react'
import { useFieldArray, useFormContext } from 'react-hook-form'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical, Link2, Link2Off, Trash2 } from 'lucide-react'
import { createPerson, getAdminPeople, refreshAll } from '@/api'
import type { AuthorRef, Person, PersonKind } from '@/api/types'
import { personKindLabels } from '@/lib/labels'
import { useDepartments } from '@/routes/People'
import { Button, Dialog, Field, Input, Select, cn, useDebounced } from './ui'

/** Search people in the system and pick one. */
export function PersonPicker({ initial = '', onPick, exclude, autoFocus }: { initial?: string; onPick: (p: Person) => void; exclude?: string; autoFocus?: boolean }) {
  const [q, setQ] = useState(initial)
  const debounced = useDebounced(q.trim())
  const query = { q: debounced, exclude, pageSize: 8 }
  const res = useQuery({ queryKey: ['admin-people', query], queryFn: () => getAdminPeople(query), enabled: debounced.length >= 2 })
  const items = res.data?.items ?? []
  return (
    <div className="space-y-1">
      <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Фамилия…" className="h-10" aria-label="Поиск человека" autoFocus={autoFocus} />
      {debounced.length >= 2 && (
        <ul className="max-h-56 overflow-y-auto border border-rule bg-white" role="listbox" aria-label="Найденные люди">
          {res.isFetching && !items.length && <li className="p-2 text-sm text-muted">Поиск…</li>}
          {!res.isFetching && !items.length && <li className="p-2 text-sm text-muted">Никого не нашли</li>}
          {items.map((p) => (
            <li key={p.id} role="option" aria-selected={false}>
              <button type="button" onClick={() => onPick(p)} className="flex w-full items-start gap-2 px-2 py-1.5 text-left text-sm hover:bg-panel">
                <span className="flex-1">{p.fullName}<span className="block text-xs text-muted">{[personKindLabels[p.kind], p.department ?? p.affiliation].filter(Boolean).join(' · ')}</span></span>
                <span className="text-xs text-muted">{p.workCount}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Create a person; used from the authors editor and the unmatched-authors list. */
export function NewPersonDialog({ open, displayName, defaultKind = 'staff', onClose, onCreated }: {
  open: boolean; displayName: string; defaultKind?: PersonKind; onClose: () => void; onCreated: (p: Person) => void
}) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ fullName: displayName, kind: defaultKind as PersonKind, department: '', affiliation: '' })
  const departments = useDepartments().data?.items ?? []
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  useEffect(() => { if (open) setForm({ fullName: displayName, kind: defaultKind, department: '', affiliation: '' }) }, [open, displayName, defaultKind])
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const p = await createPerson({
        fullName: form.fullName.trim(), kind: form.kind, aliases: displayName && displayName !== form.fullName ? [displayName] : [],
        ...(form.kind === 'external' ? { affiliation: form.affiliation || undefined } : { department: form.department || undefined }),
      })
      refreshAll(qc)
      onCreated(p)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось создать')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Новый человек">
      <form onSubmit={submit} className="space-y-3">
        <Field label="ФИО полностью" hint="Например: Иванов Иван Иванович">{(id) => <Input id={id} required value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} />}</Field>
        <Field label="Вид">{(id) => (
          <Select id={id} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as PersonKind })}>
            {(Object.keys(personKindLabels) as PersonKind[]).map((k) => <option key={k} value={k}>{personKindLabels[k]}</option>)}
          </Select>
        )}</Field>
        {form.kind === 'external'
          ? <Field label="Организация">{(id) => <Input id={id} value={form.affiliation} onChange={(e) => setForm({ ...form, affiliation: e.target.value })} />}</Field>
          : <Field label="Кафедра">{(id) => <Select id={id} value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}><option value="">—</option>{departments.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}</Select>}</Field>}
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button type="submit" loading={busy}>Создать и связать</Button>
        </div>
      </form>
    </Dialog>
  )
}

function AuthorRow({ id, index, name, onChanged, onRemove, defaultKind }: {
  id: string; index: number; name: string; onChanged: () => void; onRemove: () => void; defaultKind: PersonKind
}) {
  const { register, watch, setValue } = useFormContext()
  const ref: AuthorRef = watch(`${name}.${index}`)
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  const [picking, setPicking] = useState(false)
  const [creating, setCreating] = useState(false)
  const link = (p: Person) => {
    setValue(`${name}.${index}`, { personId: p.id, displayName: ref.displayName || (p.shortName ?? p.fullName), kind: p.kind }, { shouldDirty: true, shouldValidate: true })
    onChanged()
    setPicking(false)
    setCreating(false)
  }
  const unmatched = !ref?.personId
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn('border-b border-rule bg-white py-2', isDragging && 'relative z-10 outline outline-brand')}>
      <div className="flex items-center gap-2">
        <button type="button" className="cursor-grab touch-none p-1 text-muted hover:bg-panel active:cursor-grabbing" aria-label={`Перетащить автора ${index + 1}`} {...attributes} {...listeners}>
          <GripVertical className="h-4 w-4" />
        </button>
        <span className="w-5 text-xs tabular-nums text-muted">{index + 1}.</span>
        <div className="min-w-0 flex-1">
          <Input {...register(`${name}.${index}.displayName`)} className={cn('h-10', unmatched && 'border-2 border-warn')} placeholder="Как в работе: Иванов И. И." aria-label={`ФИО автора ${index + 1}`} />
          <p className={cn('mt-0.5 text-sm italic', unmatched ? 'text-warn' : 'text-ok')}>
            {unmatched ? 'Не связан с человеком в системе' : `Связан · ${personKindLabels[ref.kind].toLowerCase()}`}
          </p>
        </div>
        {unmatched
          ? <Button variant="ghost" size="icon" onClick={() => setPicking(!picking)} aria-label="Связать с человеком" title="Связать с человеком" aria-expanded={picking}><Link2 className="h-4 w-4" /></Button>
          : <Button variant="ghost" size="icon" onClick={() => { setValue(`${name}.${index}`, { displayName: ref.displayName, kind: ref.kind }, { shouldDirty: true }); onChanged() }} aria-label="Отвязать" title="Отвязать"><Link2Off className="h-4 w-4" /></Button>}
        <Button variant="ghost" size="icon" onClick={onRemove} aria-label={`Удалить автора ${index + 1}`} title="Удалить"><Trash2 className="h-4 w-4" /></Button>
      </div>
      {picking && (
        <div className="mt-2 space-y-2 bg-panel p-3 sm:ml-9">
          <PersonPicker initial={ref.displayName ?? ''} onPick={link} autoFocus />
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>Создать нового</Button>
            <Button variant="ghost" size="sm" onClick={() => setPicking(false)}>Оставить как текст</Button>
          </div>
        </div>
      )}
      <NewPersonDialog open={creating} displayName={ref?.displayName ?? ''} defaultKind={defaultKind} onClose={() => setCreating(false)} onCreated={link} />
    </li>
  )
}

export function AuthorsEditor({ name, label, defaultKind, onChanged, error }: {
  name: 'authors' | 'supervisors'; label: string; defaultKind: PersonKind; onChanged: () => void; error?: string
}) {
  const { control } = useFormContext()
  const { fields, append, remove, move } = useFieldArray({ control, name })
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))
  function onDragEnd(e: DragEndEvent) {
    if (!e.over || e.active.id === e.over.id) return
    move(fields.findIndex((f) => f.id === e.active.id), fields.findIndex((f) => f.id === e.over!.id))
    onChanged()
  }
  return (
    <div className="space-y-2">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={fields.map((f) => f.id)} strategy={verticalListSortingStrategy}>
          <ol className="border-t border-rule" aria-label={label}>
            {fields.map((f, i) => (
              <AuthorRow key={f.id} id={f.id} index={i} name={name} defaultKind={defaultKind} onChanged={onChanged} onRemove={() => { remove(i); onChanged() }} />
            ))}
          </ol>
        </SortableContext>
      </DndContext>
      {error && <p className="text-xs text-danger">{error}</p>}
      <Button variant="link" size="sm" onClick={() => { append({ displayName: '', kind: defaultKind }); onChanged() }}>
        Добавить
      </Button>
    </div>
  )
}
