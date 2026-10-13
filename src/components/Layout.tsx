import { lazy, Suspense, useEffect } from 'react'
import { isRouteErrorResponse, Link, Navigate, NavLink, Outlet, useLocation, useNavigate, useRouteError } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getMe, logout, reportError } from '@/api'
import type { User } from '@/api/types'
import { btn, Button, cn, EmptyState, Spinner } from './ui'

const MockPanel = import.meta.env.VITE_USE_MOCKS === 'true' ? lazy(() => import('@/mocks/MockPanel')) : null
const ORG = import.meta.env.VITE_FACULTY_NAME || 'Лаборатория интеллектуальных систем'
/** Grafana on stats.<domain>: open only from the university network, so it is just a link out. */
const STATS_URL = import.meta.env.VITE_STATS_URL as string | undefined

/** Where a signed-in user works: authors upload materials, moderators and admins run the repository. */
export const homeFor = (user: User) => (user.role === 'author' ? '/my' : '/admin')

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => getMe().then((r) => r.user).catch((e) => {
      if (e instanceof ApiError && e.status === 401) return null
      throw e
    }),
    staleTime: 5 * 60_000,
    retry: false,
  })
}

function Header() {
  const { data: user } = useMe()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const link = ({ isActive }: { isActive: boolean }) =>
    cn('-mb-px border-b-[3px] pb-3 pt-2 no-underline hover:text-ink', isActive ? 'border-accent font-bold' : 'border-transparent')
  async function onLogout() {
    await logout()
    qc.setQueryData(['me'], null)
    qc.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('admin') || q.queryKey[0] === 'my-works' })
    navigate('/')
  }
  return (
    <>
      <div className="bg-ink text-sm text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-1.5">
          <span>{user && user.role !== 'author' ? 'Рабочее место модератора' : ORG}</span>
          <span className="flex items-center gap-3">
            {MockPanel && <Suspense><MockPanel /></Suspense>}
            {user
              ? <>{user.name} · <button type="button" onClick={onLogout} className="underline underline-offset-2 hover:text-white">Выйти</button></>
              : <Link to="/login" className="text-white hover:text-white">Вход</Link>}
          </span>
        </div>
      </div>
      <header className="border-b border-brand">
        <div className="mx-auto flex max-w-6xl flex-wrap items-end gap-x-10 gap-y-1 px-4 pt-4">
          <Link to="/" className="mr-auto flex items-center gap-3 pb-3 text-ink no-underline hover:text-ink">
            <img src="/logo.svg" alt="" className="h-12 w-12 shrink-0" />
            <span>
              <span className="block text-2xl font-bold leading-tight">Репозиторий работ</span>
              <span className="block text-sm text-ink-2">ВКР, курсовые, публикации и доклады</span>
            </span>
          </Link>
          <nav className="flex gap-7" aria-label="Основная навигация">
            <NavLink to="/works" className={link}>Работы</NavLink>
            <NavLink to="/people" className={link}>Преподаватели</NavLink>
            {user && <NavLink to={homeFor(user)} className={link}>{user.role === 'author' ? 'Мои работы' : 'Рабочее место'}</NavLink>}
          </nav>
        </div>
      </header>
    </>
  )
}

const Footer = () => (
  <footer className="border-t border-ink">
    <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-2 px-4 pb-7 pt-4 text-sm text-ink-2">
      <span>{ORG} · Репозиторий работ факультета</span>
      <span>{new Date().getFullYear()}</span>
    </div>
  </footer>
)

export function PublicLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-14 pt-6"><Outlet /></main>
      <Footer />
    </div>
  )
}

const adminNav = [
  { to: '/admin', label: 'Сводка', end: true },
  { to: '/admin/imports', label: 'Импорты' },
  { to: '/admin/review', label: 'Проверка' },
  { to: '/admin/works', label: 'Все работы' },
  { to: '/admin/people', label: 'Люди' },
]

export function AdminLayout() {
  const link = ({ isActive }: { isActive: boolean }) => cn(
    'flex min-h-11 items-center whitespace-nowrap border-b border-rule px-3 no-underline hover:text-ink md:px-2',
    isActive ? 'bg-panel font-bold' : '',
  )
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6 md:flex-row md:gap-10">
        <nav className="flex shrink-0 overflow-x-auto border-t-2 border-brand [scrollbar-width:none] md:w-48 md:flex-col" aria-label="Рабочее место">
          {adminNav.map(({ to, label, end }) => <NavLink key={to} to={to} end={end} className={link}>{label}</NavLink>)}
          {STATS_URL && <a href={STATS_URL} target="_blank" rel="noreferrer" className={link({ isActive: false })}>Статистика</a>}
        </nav>
        <main className="min-w-0 flex-1"><Suspense fallback={<Spinner />}><Outlet /></Suspense></main>
      </div>
      <Footer />
    </div>
  )
}

/** Signed-in pages: moderator's workplace by default, `author` for "Мои работы". A user of the other kind goes to their own home. */
export function RequireAuth({ children, author = false }: { children: React.ReactNode; author?: boolean }) {
  const { data: user, isPending, error } = useMe()
  const loc = useLocation()
  if (isPending) return <Spinner />
  if (error) return <div className="p-6 text-danger">Не удалось проверить вход: {error.message}</div>
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />
  if (author !== (user.role === 'author')) return <Navigate to={homeFor(user)} replace />
  return <>{children}</>
}

/** Instead of React Router's developer screen: most often a page's code could not be loaded (server down, stale tab after a deploy). */
export function RouteError() {
  const error = useRouteError()
  useEffect(() => { if (!isRouteErrorResponse(error)) reportError(error) }, [error])
  const detail = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : error instanceof Error ? error.message : ''
  return (
    <div className="mx-auto max-w-2xl px-4 py-16">
      <EmptyState action={<span className="flex flex-wrap justify-center gap-3"><Button onClick={() => window.location.reload()}>Обновить страницу</Button><a href="/" className={btn('outline')}>На главную</a></span>}>
        <p className="text-2xl font-bold text-ink">Не удалось открыть страницу</p>
        <p>Проверьте соединение и обновите страницу. Если не поможет — сообщите администратору.</p>
        {detail && <p className="text-sm text-muted">{detail}</p>}
      </EmptyState>
    </div>
  )
}

export const NotFound = ({ text = 'Страница не найдена' }: { text?: string }) => (
  <EmptyState action={<Link to="/works" className={btn('outline')}>Перейти в каталог</Link>}>
    <p className="text-2xl font-bold text-ink">{text}</p>
  </EmptyState>
)
