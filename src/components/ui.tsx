// Small kit on native elements (button, input, select, dialog). Square shapes, hairlines, no shadows.
import { useEffect, useId, useRef, type ComponentProps, type ReactNode } from 'react'
import { Link } from 'react-router'
import { AlertTriangle, Info, Loader2, X } from 'lucide-react'
import { twMerge } from 'tailwind-merge'
import { plural } from '@/lib/format'

/** Joins classes; later ones win over earlier conflicting ones (e.g. a passed `w-auto` over the default `w-full`). */
export const cn = (...c: (string | false | null | undefined)[]) => twMerge(c.filter(Boolean).join(' '))

const caps = 'font-bold uppercase tracking-[0.08em]'
const variants = {
  primary: `bg-accent text-white hover:bg-accent-dark hover:text-white ${caps}`,
  secondary: `bg-panel text-ink hover:bg-panel-2 ${caps}`,
  outline: `border border-brand bg-white text-ink hover:bg-panel ${caps}`,
  ghost: 'text-ink hover:bg-panel',
  danger: `bg-danger text-white hover:bg-accent-dark hover:text-white ${caps}`,
  /** Uppercase text with a thick underline bar. */
  link: `border-b-2 border-current h-auto px-0 py-1 text-ink hover:text-accent ${caps}`,
}
const sizes = { sm: 'h-9 px-4 text-[13px]', md: 'h-11 px-5 text-[14px]', icon: 'h-10 w-10' }

export const btn = (variant: keyof typeof variants = 'primary', size: keyof typeof sizes = 'md', extra?: string) =>
  cn('inline-flex shrink-0 items-center justify-center gap-2 no-underline transition-colors disabled:pointer-events-none disabled:opacity-50', sizes[size], variants[variant], extra)

export function Button({ variant, size, className, loading, children, ...p }: ComponentProps<'button'> & { variant?: keyof typeof variants; size?: keyof typeof sizes; loading?: boolean }) {
  return (
    <button type="button" className={btn(variant, size, className)} disabled={loading || p.disabled} {...p}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  )
}

const control = 'w-full border border-field bg-white px-3 text-base text-ink focus:border-brand disabled:bg-panel aria-[invalid=true]:border-2 aria-[invalid=true]:border-danger'
export const Input = ({ className, ...p }: ComponentProps<'input'>) => <input className={cn(control, 'h-11', className)} {...p} />
export const Textarea = ({ className, ...p }: ComponentProps<'textarea'>) => <textarea className={cn(control, 'py-2', className)} {...p} />
export const Select = ({ className, ...p }: ComponentProps<'select'>) => <select className={cn(control, 'h-11 pr-8', className)} {...p} />

/** Label + control + error. Pass a render function to receive the generated id. */
export function Field({ label, error, hint, className, children, extra }: {
  label: ReactNode; error?: string; hint?: ReactNode; className?: string; extra?: ReactNode
  children: (id: string, describedBy?: string) => ReactNode
}) {
  const id = useId()
  const errId = error ? `${id}-err` : undefined
  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="font-bold text-ink">{label}</label>
        {extra}
      </div>
      {children(id, errId)}
      {hint && !error && <p className="text-sm text-muted">{hint}</p>}
      {error && <p id={errId} className="text-sm text-danger">{error}</p>}
    </div>
  )
}

/** Status text with a small square marker; no pills. */
const tones = {
  slate: 'text-ink-2',
  blue: 'text-info',
  green: 'text-ok',
  amber: 'text-warn',
  red: 'text-danger',
}
export type Tone = keyof typeof tones
export const Badge = ({ tone = 'slate', className, children, ...p }: ComponentProps<'span'> & { tone?: Tone }) => (
  <span className={cn('inline-flex items-center gap-1.5 whitespace-nowrap text-sm font-bold', tones[tone], className)} {...p}>
    {tone !== 'slate' && <span aria-hidden className="h-2 w-2 shrink-0 bg-current" />}
    {children}
  </span>
)

export const Card = ({ className, ...p }: ComponentProps<'div'>) => <div className={cn('bg-white', className)} {...p} />
export const Skeleton = ({ className }: { className?: string }) => <div className={cn('animate-pulse bg-panel', className)} aria-hidden />
export const Spinner = ({ label = 'Загрузка…' }: { label?: string }) => (
  <div className="flex items-center gap-2 py-8 text-muted" role="status"><Loader2 className="h-4 w-4 animate-spin" aria-hidden />{label}</div>
)

/** Serif section title over a 2px rule, with an optional action on the right. */
export const SectionTitle = ({ children, action, as: H = 'h2', className }: { children: ReactNode; action?: ReactNode; as?: 'h1' | 'h2'; className?: string }) => (
  <div className={cn('flex flex-wrap items-baseline justify-between gap-3 border-b-2 border-brand pb-1.5', className)}>
    <H className="text-2xl font-bold">{children}</H>
    {action}
  </div>
)

/** Flush number tiles: the first one dark, the rest light. */
export function Tiles({ items, className }: { items: { value: ReactNode; label: ReactNode; note?: ReactNode; to?: string }[]; className?: string }) {
  const bg = (i: number) => (i === 0 ? 'bg-brand text-white' : i % 2 ? 'bg-panel' : 'bg-panel-2')
  return (
    <div className={cn('grid grid-cols-[repeat(auto-fit,minmax(min(100%,9rem),1fr))]', className)}>
      {items.map((t, i) => (
        <div key={i} className={cn('flex flex-col gap-1 p-5 sm:p-6', bg(i))}>
          <span className="text-4xl font-bold leading-none sm:text-5xl">{t.value}</span>
          <span className="font-bold">{t.label}</span>
          {t.note && <span className={cn('text-sm', i ? 'text-ink-2' : 'text-brand-light')}>{t.note}</span>}
          {t.to && <Link to={t.to} className={btn('link', 'sm', cn('mt-2 self-start', i ? '' : '!text-white hover:!text-white'))}>Смотреть</Link>}
        </div>
      ))}
    </div>
  )
}

export function Alert({ tone = 'amber', title, children, className }: { tone?: 'amber' | 'red' | 'blue'; title?: ReactNode; children?: ReactNode; className?: string }) {
  const c = { amber: 'bg-warn-bg text-[#5c3c00]', red: 'bg-danger-bg text-[#7e1622]', blue: 'bg-panel text-ink' }[tone]
  const Icon = tone === 'blue' ? Info : AlertTriangle
  return (
    <div className={cn('flex gap-2 px-4 py-3', c, className)} role={tone === 'red' ? 'alert' : undefined}>
      <Icon className="mt-1 h-4 w-4 shrink-0" aria-hidden />
      <div>{title && <b>{title} </b>}{children}</div>
    </div>
  )
}

export const ErrorState = ({ error, onRetry }: { error: unknown; onRetry?: () => void }) => (
  <div className="flex flex-col items-center gap-3 bg-danger-bg p-6 text-center text-[#7e1622]" role="alert">
    <p>{error instanceof Error ? error.message : 'Не удалось загрузить данные'}</p>
    {onRetry && <Button variant="outline" size="sm" onClick={onRetry}>Повторить</Button>}
  </div>
)

export const EmptyState = ({ children, action }: { children: ReactNode; action?: ReactNode }) => (
  <div className="flex flex-col items-center gap-3 bg-panel p-10 text-center text-ink-2">
    {children}{action}
  </div>
)

/** Controlled native <dialog>. */
export function Dialog({ open, onClose, title, children, className, side }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; className?: string; side?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className={cn(
        'max-h-[90vh] w-[min(36rem,calc(100vw-2rem))] border-t-4 border-brand bg-white p-0 text-ink',
        side ? 'm-0 ml-auto h-full max-h-none w-[min(22rem,90vw)] border-t-0' : 'm-auto',
        className,
      )}
    >
      {open && (
        <div className="flex max-h-[inherit] flex-col">
          <div className="flex items-center justify-between border-b border-rule px-5 py-3">
            <h2 className="text-xl font-bold">{title}</h2>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Закрыть"><X className="h-4 w-4" /></Button>
          </div>
          <div className="overflow-y-auto p-5">{children}</div>
        </div>
      )}
    </dialog>
  )
}

export function Tabs<T extends string>({ items, value, onChange, className }: { items: { value: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; className?: string }) {
  return (
    <div role="tablist" className={cn('flex gap-7 overflow-x-auto overflow-y-hidden border-b border-rule [scrollbar-width:none]', className)}>
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          type="button"
          aria-selected={it.value === value}
          onClick={() => onChange(it.value)}
          className={cn('-mb-px whitespace-nowrap border-b-[3px] py-2.5 text-ink',
            it.value === value ? 'border-accent font-bold' : 'border-transparent hover:border-rule')}
        >
          {it.label}
        </button>
      ))}
    </div>
  )
}

export function Pagination({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (p: number) => void }) {
  const pages = Math.ceil(total / pageSize)
  if (pages <= 1) return null
  const nums = [...new Set([1, page - 1, page, page + 1, pages])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b)
  return (
    <nav className="flex flex-wrap items-center gap-x-5 gap-y-2" aria-label="Страницы">
      <span>Страница {page} из {pages}</span>
      <span className="flex items-center gap-1">
        {nums.map((n, i) => (
          <span key={n} className="flex items-center">
            {i > 0 && n - nums[i - 1] > 1 && <span className="px-1 text-muted">…</span>}
            <button type="button" onClick={() => onChange(n)} aria-current={n === page ? 'page' : undefined}
              className={cn('h-10 min-w-10 border-b-[3px] px-2', n === page ? 'border-accent font-bold' : 'border-transparent underline underline-offset-4 hover:text-accent')}>{n}</button>
          </span>
        ))}
      </span>
      {page > 1 && <Button variant="link" onClick={() => onChange(page - 1)}>Предыдущая</Button>}
      {page < pages && <Button variant="link" onClick={() => onChange(page + 1)}>Следующая</Button>}
    </nav>
  )
}

export const Count = ({ n, forms }: { n: number; forms: [string, string, string] }) => <>{n} {plural(n, forms)}</>
