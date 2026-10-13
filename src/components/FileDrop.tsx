import { useId, useState } from 'react'
import { FileUp, X } from 'lucide-react'
import { formatSize } from '@/lib/format'
import { Button, cn } from './ui'

/** Drag-and-drop zone with extension and size checks before anything is sent. */
export function FileDrop({ label, hint, accept, maxBytes, file, onFile, error }: {
  label: string; hint?: string; accept: string[]; maxBytes: number
  file: File | null; onFile: (f: File | null, error?: string) => void; error?: string
}) {
  const id = useId()
  const [over, setOver] = useState(false)
  function pick(f?: File | null) {
    if (!f) return
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!accept.includes(ext)) return onFile(null, `Неверный формат «${f.name}». Допустимо: ${accept.join(', ')}`)
    if (f.size > maxBytes) return onFile(null, `Файл ${formatSize(f.size)} больше лимита ${formatSize(maxBytes)}`)
    onFile(f)
  }
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-ink">{label}</p>
      {file ? (
        <div className="flex items-center gap-3 border border-field bg-white p-3">
          <FileUp className="h-5 w-5 text-ink" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{file.name}</p>
            <p className="text-xs text-muted">{formatSize(file.size)}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={() => onFile(null)} aria-label="Убрать файл"><X className="h-4 w-4" /></Button>
        </div>
      ) : (
        <label
          htmlFor={id}
          onDragOver={(e) => { e.preventDefault(); setOver(true) }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]) }}
          className={cn('flex cursor-pointer flex-col items-center gap-1 border-2 border-dashed p-6 text-center text-sm transition-colors',
            over ? 'border-brand bg-panel' : 'border-field bg-white hover:border-field', error && 'border-danger')}
        >
          <FileUp className="h-6 w-6 text-muted" aria-hidden />
          <span><span className="font-medium text-ink">Выберите файл</span> или перетащите сюда</span>
          <span className="text-xs text-muted">{hint ?? `${accept.join(', ')} · до ${formatSize(maxBytes)}`}</span>
          <input id={id} type="file" accept={accept.join(',')} className="sr-only" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }} />
        </label>
      )}
      {error && <p className="text-xs text-danger" role="alert">{error}</p>}
    </div>
  )
}
