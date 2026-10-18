import { useId, useState } from 'react'
import { FileUp, X } from 'lucide-react'
import { formatSize, plural } from '@/lib/format'
import { Button, cn } from './ui'

/** Drag-and-drop zone with extension and size checks before anything is sent.
 *  `multiple`: any number of files, `maxBytes` limits all of them together and `onFiles` gets the whole list. */
export function FileDrop({ label, hint, accept, maxBytes, file = null, onFile, error, multiple, files = [], onFiles }: {
  label: string; hint?: string; accept: string[]; maxBytes: number
  file?: File | null; onFile?: (f: File | null, error?: string) => void; error?: string
  multiple?: boolean; files?: File[]; onFiles?: (files: File[], error?: string) => void
}) {
  const id = useId()
  const [over, setOver] = useState(false)
  const chosen = multiple ? files : file ? [file] : []

  function check(list: File[]): string | undefined {
    const bad = list.find((f) => !accept.includes(f.name.slice(f.name.lastIndexOf('.')).toLowerCase()))
    if (bad) return `Неверный формат «${bad.name}». Допустимо: ${accept.join(', ')}`
    const size = list.reduce((s, f) => s + f.size, 0)
    if (size > maxBytes) return `${list.length > 1 ? 'Файлы' : 'Файл'} ${formatSize(size)} больше лимита ${formatSize(maxBytes)}`
  }
  function pick(list?: FileList | null) {
    if (!list?.length) return
    if (multiple) {
      const all = [...files, ...list]
      const err = check(all)
      return err ? onFiles?.(files, err) : onFiles?.(all)
    }
    const err = check([list[0]])
    return err ? onFile?.(null, err) : onFile?.(list[0])
  }

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-ink">{label}</p>
      {chosen.length > 0 && (
        <div className="flex items-center gap-3 border border-field bg-white p-3">
          <FileUp className="h-5 w-5 text-ink" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">
              {chosen.length === 1 ? chosen[0].name : `${chosen.length} ${plural(chosen.length, ['файл', 'файла', 'файлов'])}: ${chosen.slice(0, 3).map((f) => f.name).join(', ')}${chosen.length > 3 ? '…' : ''}`}
            </p>
            <p className="text-xs text-muted">{formatSize(chosen.reduce((s, f) => s + f.size, 0))}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={() => (multiple ? onFiles?.([]) : onFile?.(null))} aria-label={chosen.length > 1 ? 'Убрать файлы' : 'Убрать файл'}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}
      {(multiple || chosen.length === 0) && (
        <label
          htmlFor={id}
          onDragOver={(e) => { e.preventDefault(); setOver(true) }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files) }}
          className={cn('flex cursor-pointer flex-col items-center gap-1 border-2 border-dashed p-6 text-center text-sm transition-colors',
            over ? 'border-brand bg-panel' : 'border-field bg-white hover:border-field', error && 'border-danger')}
        >
          <FileUp className="h-6 w-6 text-muted" aria-hidden />
          <span><span className="font-medium text-ink">{!multiple ? 'Выберите файл' : chosen.length ? 'Добавьте ещё' : 'Выберите файлы'}</span> или перетащите сюда</span>
          <span className="text-xs text-muted">{hint ?? `${accept.join(', ')} · до ${formatSize(maxBytes)}`}</span>
          <input id={id} type="file" accept={accept.join(',')} multiple={multiple} className="sr-only" onChange={(e) => { pick(e.target.files); e.target.value = '' }} />
        </label>
      )}
      {error && <p className="text-xs text-danger" role="alert">{error}</p>}
    </div>
  )
}
