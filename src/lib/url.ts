/** Only paths on this site: `/x` yes; `//host`, `/\host` (browsers read `\` as `/`) and `https://…` no. */
export const safeNext = (next: string | null) => (next && /^\/(?![/\\])/.test(next) ? next : null)

/** Navigation from code bypasses React's `javascript:` guard, so only http(s) links (absolute or relative) pass. */
export const isWebUrl = (url: string, base = window.location.href) => ['http:', 'https:'].includes(new URL(url, base).protocol)
