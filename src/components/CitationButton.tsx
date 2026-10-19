import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getCitation } from '@/api'
import { Button, Skeleton } from './ui'

/** Copy to the clipboard; `failed` when the browser refuses (no permission, not a secure page). */
export function useCopy() {
  const [state, setState] = useState<'copied' | 'failed'>()
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
    setTimeout(() => setState(undefined), 2500)
  }
  return { copied: state === 'copied', failed: state === 'failed', copy }
}

/** "Как цитировать": the ГОСТ reference with a copy button. The page's "Цитировать" button scrolls here. */
export function Citation({ workId }: { workId: string }) {
  const q = useQuery({ queryKey: ['citation', workId], queryFn: () => getCitation(workId) })
  const { copied, failed, copy } = useCopy()
  return (
    <section id="cite" className="scroll-mt-6 bg-panel px-6 py-5">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-bold">Как цитировать</h2>
        <span className="text-sm text-ink-2">ГОСТ Р 7.0.100–2018</span>
      </div>
      {q.isPending ? <Skeleton className="h-16 bg-panel-2" />
        : q.isError ? <p className="text-danger">Не удалось получить ссылку. <button type="button" className="underline" onClick={() => q.refetch()}>Повторить</button></p>
        : (
          <>
            <p className="leading-relaxed">{q.data.gost}</p>
            <Button variant="link" size="sm" className="mt-3" onClick={() => copy(q.data.gost)}>{copied ? 'Скопировано' : failed ? 'Не удалось — выделите текст' : 'Скопировать'}</Button>
          </>
        )}
    </section>
  )
}
