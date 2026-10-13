import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { exportPeople, getAdminPeople, getUnmatched, matchAuthor } from '@/api'
import type { PersonKind, UnmatchedAuthor } from '@/api/types'
import { NewPersonDialog, PersonPicker } from '@/components/AuthorsEditor'
import { Alert, Badge, Button, Card, Count, EmptyState, ErrorState, Input, Pagination, Select, Spinner, Tabs } from '@/components/ui'
import { personKindLabels } from '@/lib/labels'
import { useDepartments } from '@/routes/People'

export const invalidatePeople = (qc: ReturnType<typeof useQueryClient>) =>
  qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith('admin') || ['works', 'work', 'person', 'personWorks', 'staff', 'facets'].includes(String(x.queryKey[0])) })

function UnmatchedCard({ u }: { u: UnmatchedAuthor }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [picking, setPicking] = useState(false)
  const [creating, setCreating] = useState(false)
  async function link(personId: string | null, kind: PersonKind) {
    setBusy(true)
    try {
      await matchAuthor(u.displayName, personId, kind)
      await invalidatePeople(qc)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка')
      setBusy(false)
    }
  }
  return (
    <Card className="space-y-3 border-b border-rule py-5">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="font-semibold">{u.displayName}</h3>
        <span className="text-sm text-muted">
          в <Count n={u.count} forms={['работе', 'работах', 'работах']} />:{' '}
          {u.workIds.slice(0, 3).map((id, i) => <span key={id}>{i > 0 && ', '}<Link to={`/admin/works/${id}`} className="text-ink hover:underline">{id}</Link></span>)}
          {u.workIds.length > 3 && '…'}
        </span>
      </div>
      {u.suggestions.length > 0 ? (
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Похожие люди</p>
          {u.suggestions.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-2 text-sm">
              <Link to={`/admin/people/${p.id}`} className="text-ink hover:underline">{p.fullName}</Link>
              <Badge>{personKindLabels[p.kind]}</Badge>
              {p.department && <span className="text-xs text-muted">{p.department}</span>}
              <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => link(p.id, p.kind)}>Это он</Button>
            </div>
          ))}
        </div>
      ) : <p className="text-sm text-muted">Похожих людей не нашли</p>}
      {picking && <PersonPicker onPick={(p) => link(p.id, p.kind)} autoFocus />}
      <div className="flex flex-wrap gap-2 border-t border-rule pt-3">
        <Button size="sm" variant="ghost" onClick={() => setPicking(!picking)} disabled={busy}>Найти другого</Button>
        <Button size="sm" variant="ghost" onClick={() => setCreating(true)} disabled={busy}>Создать нового</Button>
        <Button size="sm" variant="ghost" onClick={() => link(null, 'external')} loading={busy}>Внешний автор</Button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      <NewPersonDialog open={creating} displayName={u.displayName} onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); link(p.id, p.kind) }} />
    </Card>
  )
}

function Unmatched() {
  const q = useQuery({ queryKey: ['admin-unmatched'], queryFn: getUnmatched })
  if (q.isPending) return <Spinner />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (!q.data.items.length) return <EmptyState>Все авторы сопоставлены с людьми системы</EmptyState>
  return (
    <div className="space-y-3">
      <Alert tone="blue">Имена из импортов, не связанные ни с кем. После сопоставления все работы с этим написанием во всех импортах привяжутся к выбранному человеку.</Alert>
      {q.data.items.map((u) => <UnmatchedCard key={u.displayName} u={u} />)}
    </div>
  )
}

export default function AdminPeople() {
  const [sp, setSp] = useSearchParams()
  const navigate = useNavigate()
  const unmatched = sp.get('unmatched') === '1'
  const page = Math.max(1, Number(sp.get('page')) || 1)
  const query = { q: sp.get('q') ?? '', kind: sp.get('kind') ?? '', department: sp.get('department') ?? '' }
  const set = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); n.delete('page'); return n }, { replace: true })
  const departments = useDepartments().data?.items ?? []
  const people = useQuery({ queryKey: ['admin-people', { ...query, page }], queryFn: () => getAdminPeople({ ...query, page, pageSize: 50 }), enabled: !unmatched, placeholderData: keepPreviousData })
  const unmatchedCount = useQuery({ queryKey: ['admin-unmatched'], queryFn: getUnmatched })
  const [text, setText] = useState(query.q)
  const [creating, setCreating] = useState(false)
  const [exportErr, setExportErr] = useState<string>()
  useEffect(() => {
    if (text.trim() === query.q) return
    const t = setTimeout(() => set('q', text.trim()), 300)
    return () => clearTimeout(t)
  }, [text])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-4xl font-bold">Люди</h1>
        <Button variant="outline" onClick={() => exportPeople().catch((e) => setExportErr(e instanceof Error ? e.message : 'Ошибка'))}>Выгрузить</Button>
        <Button onClick={() => setCreating(true)}>Добавить человека</Button>
      </div>
      {exportErr && <Alert tone="red">{exportErr}</Alert>}
      <Tabs value={unmatched ? 'u' : 'all'} onChange={(v) => setSp(v === 'u' ? { unmatched: '1' } : {})}
        items={[{ value: 'all', label: 'Все люди' }, { value: 'u', label: <>Нераспознанные авторы{unmatchedCount.data ? <span className="ml-1.5 bg-warn-bg px-1.5 text-xs text-warn">{unmatchedCount.data.total}</span> : null}</> }]} />

      {unmatched ? <Unmatched /> : (
        <>
          <div className="flex flex-wrap gap-2">
            <Input type="search" aria-label="Поиск по ФИО" placeholder="ФИО или вариант написания" value={text} onChange={(e) => setText(e.target.value)} className="w-full sm:w-72" />
            <Select aria-label="Вид" value={query.kind} onChange={(e) => set('kind', e.target.value)} className="w-auto">
              <option value="">Все виды</option>
              {(Object.keys(personKindLabels) as PersonKind[]).map((k) => <option key={k} value={k}>{personKindLabels[k]}</option>)}
            </Select>
            <Select aria-label="Кафедра" value={query.department} onChange={(e) => set('department', e.target.value)} className="w-auto">
              <option value="">Все кафедры</option>
              {departments.map((d) => <option key={d.name}>{d.name}</option>)}
            </Select>
          </div>
          {people.isError ? <ErrorState error={people.error} onRetry={() => people.refetch()} />
            : people.isPending ? <Spinner />
            : people.data.items.length === 0 ? <EmptyState>Никого не нашли</EmptyState>
            : (
              <Card className="overflow-x-auto">
                <table className="w-full min-w-[36rem] text-sm">
                  <thead className="border-b-2 border-brand text-left">
                    <tr><th className="p-3">ФИО</th><th className="p-3">Вид</th><th className="p-3">Кафедра / организация</th><th className="p-3 text-right">Работ</th></tr>
                  </thead>
                  <tbody className="divide-y divide-rule">
                    {people.data.items.map((p) => (
                      <tr key={p.id} className="hover:bg-panel">
                        <td className="p-3"><Link to={`/admin/people/${p.id}`} className="font-medium text-ink hover:underline">{p.fullName}</Link></td>
                        <td className="p-3"><Badge tone={p.kind === 'staff' ? 'blue' : 'slate'}>{personKindLabels[p.kind]}</Badge></td>
                        <td className="p-3 text-ink-2">{p.department ?? p.affiliation}</td>
                        <td className="p-3 text-right tabular-nums">{p.workCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          {people.data && <Pagination page={page} total={people.data.total} pageSize={50} onChange={(n) => setSp((p) => { const x = new URLSearchParams(p); x.set('page', String(n)); return x })} />}
        </>
      )}
      <NewPersonDialog open={creating} displayName="" onClose={() => setCreating(false)} onCreated={(p) => navigate(`/admin/people/${p.id}`)} />
    </div>
  )
}
