/** Items grouped by year, newest year first (lists on a person's page and in "Мои работы"). */
export function groupByYear<T extends { year: number }>(items: T[]): [number, T[]][] {
  const m = new Map<number, T[]>()
  for (const x of items) m.set(x.year, [...(m.get(x.year) ?? []), x])
  return [...m].sort((a, b) => b[0] - a[0])
}
