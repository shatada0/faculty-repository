// Thin fetch wrapper. Same code talks to MSW or to the real backend (section 7).

export class ApiError extends Error {
  /** requestId is the response's X-Request-ID (nginx sets it on its own 502/504 too). On 5xx it goes into the message as
   *  «Код ошибки»: it finds the request in the logs, and every ErrorState and Alert shows it as is. */
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(status >= 500 && requestId ? `${message}. Код ошибки: ${requestId}` : message)
  }
}

/** The last X-Request-ID seen: sent along with browser error reports. */
let lastRequestId: string | undefined
const remember = (id: string | null) => { if (id) lastRequestId = id; return id ?? undefined }

/** Browser error → POST /api/client-errors. sendBeacon gets through even while the page is unloading. */
export function reportError(error: unknown) {
  const e = error instanceof Error ? error : new Error(String(error))
  const body = {
    message: e.message.slice(0, 2000),
    stack: e.stack?.slice(0, 10000),
    url: location.href.slice(0, 2000),
    userAgent: navigator.userAgent.slice(0, 500),
    build: import.meta.env.VITE_BUILD || 'dev',
    traceId: (e instanceof ApiError && e.requestId) || lastRequestId,
  }
  navigator.sendBeacon?.('/api/client-errors', new Blob([JSON.stringify(body)], { type: 'application/json' }))
}

export type Query = Record<string, string | number | boolean | (string | number)[] | undefined | null>

export function qs(params: Query = {}): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    for (const x of Array.isArray(v) ? v : [v]) sp.append(k, String(x))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function toError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null)
  return new ApiError(res.status, body?.error?.code ?? 'http_error', body?.error?.message ?? `Ошибка сервера (${res.status})`,
    remember(res.headers.get('X-Request-ID')) ?? body?.error?.traceId)
}

/** fetch throws a TypeError ("Failed to fetch") when the server did not answer at all; aborts pass through. */
async function send(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'network', 'Нет связи с сервером')
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init
  const res = await send(`/api${path}`, {
    credentials: 'include',
    ...rest,
    headers: json !== undefined ? { 'Content-Type': 'application/json', ...rest.headers } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })
  if (!res.ok) throw await toError(res)
  remember(res.headers.get('X-Request-ID'))
  if (res.status === 204) return undefined as T
  return res.json()
}

/** Multipart upload with progress (fetch has no upload progress) and cancellation. */
export function upload<T>(path: string, form: FormData, onProgress?: (fraction: number) => void, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api${path}`)
    xhr.withCredentials = true
    xhr.responseType = 'json'
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve(xhr.response)
      const e = xhr.response?.error
      reject(new ApiError(xhr.status, e?.code ?? 'http_error', e?.message ?? `Ошибка сервера (${xhr.status})`,
        remember(xhr.getResponseHeader('X-Request-ID')) ?? e?.traceId))
    }
    xhr.onerror = () => reject(new ApiError(0, 'network', 'Нет связи с сервером'))
    xhr.onabort = () => reject(new DOMException('Загрузка отменена', 'AbortError'))
    signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(form)
  })
}

/** Download a file served by the API (template tables). */
export async function download(path: string, fallbackName: string) {
  const res = await send(`/api${path}`, { credentials: 'include' })
  if (!res.ok) throw await toError(res)
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(res.headers.get('Content-Disposition') ?? '')?.[1]
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: name ? decodeURIComponent(name) : fallbackName })
  a.click()
  URL.revokeObjectURL(url)
}
