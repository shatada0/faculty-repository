const pr = new Intl.PluralRules('ru')

/** plural(5, ['работа', 'работы', 'работ']) → "работ" */
export function plural(n: number, [one, few, many]: [string, string, string]): string {
  const r = pr.select(n)
  return r === 'one' ? one : r === 'few' ? few : many
}

const toDate = (iso: string) => new Date(iso.length === 10 ? iso + 'T00:00:00' : iso)
const short = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short' })
const dateTime = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export const formatDateTime = (iso: string) => dateTime.format(toDate(iso))
/** "5 окт." */
export const formatShortDate = (iso: string) => short.format(toDate(iso))

const megabytes = new Intl.NumberFormat('ru', { maximumFractionDigits: 1 })
/** "12 КБ", "1,5 МБ", "500 МБ" */
export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`
  return `${megabytes.format(bytes / 1024 / 1024)} МБ`
}
