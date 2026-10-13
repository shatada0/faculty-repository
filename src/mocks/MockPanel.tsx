// Dev-only toggles for mock latency and random failures (section 8).
import { useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { saveSettings, settings } from './handlers'

export default function MockPanel() {
  const [open, setOpen] = useState(false)
  const [s, setS] = useState({ ...settings })
  const toggle = (k: keyof typeof settings) => {
    settings[k] = !settings[k]
    saveSettings()
    setS({ ...settings })
  }
  return (
    <div className="relative text-sm">
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-64 space-y-2 border-t-4 border-brand bg-white p-4 text-ink">
          <p className="font-bold">Моки API</p>
          <label className="flex items-center gap-2"><input type="checkbox" checked={s.delay} onChange={() => toggle('delay')} />Задержки ответов</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={s.errors} onChange={() => toggle('errors')} />Случайные ошибки (15%)</label>
          <p className="text-muted">Данные в памяти, сбрасываются при перезагрузке. Вход: moderator / moderator.</p>
        </div>
      )}
      <button type="button" onClick={() => setOpen(!open)} className="flex h-6 w-6 items-center justify-center text-[#e6e6e6] hover:text-white" aria-expanded={open} aria-label="Настройки моков" title="Моки API">
        <FlaskConical className="h-4 w-4" aria-hidden />
      </button>
    </div>
  )
}
