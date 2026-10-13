const CYR: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k',
  л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts',
  ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
}

export function translit(s: string): string {
  return [...s].map((ch) => {
    const lower = ch.toLowerCase()
    const t = CYR[lower]
    if (t === undefined) return ch
    return ch === lower ? t : t.charAt(0).toUpperCase() + t.slice(1)
  }).join('')
}

const isInitial = (t: string) => /^\p{L}\.?$/u.test(t)

/** "Иванов Иван Иванович" | "Иванов И. И." | "И.И. Иванов" | "I. Ivanov" → surname + initial letters. */
export function parseName(display: string): { surname: string; initials: string[] } {
  const tokens = display.replace(/\.(?=\S)/g, '. ').trim().split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return { surname: '', initials: [] }
  const hasInitials = tokens.some(isInitial)
  let surnameIdx = 0
  if (hasInitials) {
    surnameIdx = tokens.findIndex((t) => !isInitial(t))
    if (surnameIdx < 0) surnameIdx = 0
  } else if (tokens.length === 2 && /^[A-Za-z]/.test(tokens[0])) {
    surnameIdx = 1 // "John Smith": western order
  }
  const surname = tokens[surnameIdx].replace(/,$/, '')
  const initials = tokens.filter((_, i) => i !== surnameIdx).map((t) => t.charAt(0).toUpperCase())
  return { surname, initials }
}

/** "Иванов Иван Иванович" → "Иванов И. И." */
export function shortName(fullName: string): string {
  const { surname, initials } = parseName(fullName)
  return [surname, ...initials.map((i) => i + '.')].join(' ')
}

/** "Иванов И. И." → "И. И. Иванов" */
export function initialsFirst(display: string): string {
  const { surname, initials } = parseName(display)
  return [...initials.map((i) => i + '.'), surname].join(' ')
}

/** Comparable key: latin surname + first initial. "Иванов И.И." and "I. Ivanov" → "ivanov:i". */
export function nameKey(display: string): string {
  const { surname, initials } = parseName(display)
  const s = translit(surname).toLowerCase().replace(/kh/g, 'h')
  const i = initials[0] ? translit(initials[0]).toLowerCase().charAt(0) : ''
  return `${s}:${i}`
}
