import type { FileLink } from '@/api/types'
import { isWebUrl } from '@/lib/url'

/** Open a signed file link in a new tab: the tab opens synchronously so popup blockers allow it, the link is fetched after. */
export async function openSigned(getLink: () => Promise<FileLink>) {
  const win = window.open('', '_blank')
  if (win) win.opener = null
  try {
    const { url } = await getLink()
    if (!isWebUrl(url)) throw new Error('Некорректная ссылка на файл')
    if (win) win.location.href = url
    else window.location.href = url
  } catch (e) {
    win?.close()
    throw e
  }
}
