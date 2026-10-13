import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getAdminSummary, getImports, getUnmatched } from '@/api'
import type { WorkType } from '@/api/types'
import { ErrorState, SectionTitle, Skeleton, Tiles, btn } from '@/components/ui'
import { formatDateTime, plural } from '@/lib/format'
import { workTypePlural } from '@/lib/labels'
import { ImportStatusBadge } from './Imports'

const DRAFT_LABELS: Record<WorkType, string> = { vkr: 'ВКР', coursework: 'курсовые', publication: 'публикации', talk: 'выступления' }

export default function Dashboard() {
  const summary = useQuery({ queryKey: ['admin-works', 'summary'], queryFn: getAdminSummary })
  const imports = useQuery({ queryKey: ['admin-imports'], queryFn: getImports })
  const unmatched = useQuery({ queryKey: ['admin-unmatched'], queryFn: getUnmatched })

  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-4xl font-bold">Сводка</h1>
        <Link to="/admin/imports/new" className={btn('primary')}>Новый импорт</Link>
        <Link to="/admin/works/new" className={btn('outline')}>Добавить работу</Link>
      </div>

      <section className="space-y-4">
        <SectionTitle action={<Link to="/admin/review" className={btn('link', 'sm')}>Очередь проверки</Link>}>Черновики на проверке</SectionTitle>
        {summary.isError ? <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
          : summary.isPending ? <Skeleton className="h-36" />
          : (
            <>
              <p className="text-ink-2"><b className="text-ink">{summary.data.drafts.total}</b> {plural(summary.data.drafts.total, ['черновик', 'черновика', 'черновиков'])}{summary.data.drafts.withIssues > 0 && <>, у {summary.data.drafts.withIssues} есть замечания</>}</p>
              <Tiles items={(['vkr', 'coursework', 'publication', 'talk'] as WorkType[]).map((t) => ({ value: summary.data.drafts.byType[t], label: DRAFT_LABELS[t], to: `/admin/review?type=${t}` }))} />
            </>
          )}
      </section>

      <div className="grid gap-10 lg:grid-cols-2 [&>*]:min-w-0">
        <section>
          <SectionTitle action={<Link to="/admin/imports" className={btn('link', 'sm')}>Все импорты</Link>}>Последние импорты</SectionTitle>
          {imports.isPending ? <Skeleton className="mt-3 h-40" />
            : imports.isError ? <ErrorState error={imports.error} onRetry={() => imports.refetch()} />
            : imports.data.slice(0, 5).map((i) => (
              <div key={i.id} className="flex items-baseline gap-3 border-b border-rule py-3">
                <div className="min-w-0 flex-1">
                  <Link to={`/admin/imports/${i.id}`} className="block truncate font-bold">{i.fileName}</Link>
                  <p className="text-sm text-muted">{workTypePlural[i.workType]} · {formatDateTime(i.createdAt)} · ошибок {i.stats.errors} из {i.stats.total}</p>
                </div>
                <ImportStatusBadge status={i.status} />
              </div>
            ))}
        </section>

        {summary.data && summary.data.pendingMaterials.length > 0 && (
          <section>
            <SectionTitle>Материалы от авторов</SectionTitle>
            {summary.data.pendingMaterials.map((m) => (
              <div key={`${m.workId}:${m.name}`} className="border-b border-rule py-3">
                <Link to={`/admin/works/${m.workId}`} className="block font-bold">{m.title}</Link>
                <p className="text-sm text-muted">{m.name} · ждёт проверки</p>
              </div>
            ))}
          </section>
        )}

        <section>
          <SectionTitle action={<Link to="/admin/people?unmatched=1" className={btn('link', 'sm')}>Разобрать</Link>}>Нераспознанные авторы</SectionTitle>
          {unmatched.isPending ? <Skeleton className="mt-3 h-24" />
            : unmatched.isError ? <ErrorState error={unmatched.error} onRetry={() => unmatched.refetch()} />
            : unmatched.data.total === 0 ? <p className="py-3 text-ink-2">Все авторы сопоставлены</p>
            : (
              <div className="py-3">
                <p className="text-5xl font-bold leading-none">{unmatched.data.total}</p>
                <p className="mt-3 text-ink-2">{unmatched.data.items.slice(0, 10).map((u) => u.displayName).join(', ')}</p>
              </div>
            )}
        </section>
      </div>
    </div>
  )
}
