import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createImport, downloadTemplate, getTemplateColumns, getUploadLimits, importDoiList, previewImportTable, refreshAll } from '@/api'
import type { ImportPreview, OrcidImport, WorkType } from '@/api/types'
import { FileDrop } from '@/components/FileDrop'
import { TypeIcon } from '@/components/WorkCard'
import { Alert, Button, Card, ErrorState, Spinner, Textarea, cn } from '@/components/ui'
import { useTitle } from '@/components/Layout'

const VARIANTS = [
  // ВКР или курсовая — сервер узнаёт по титульнику каждого файла, поэтому вариант один
  { kind: 'archive', workType: 'vkr', title: 'ВКР и курсовые', desc: 'PDF или ZIP, можно пачкой. Поля, авторов и рубрики заполнит сервер; карточки без замечаний сразу публикуются, остальные ждут в «Проверке».' },
  { kind: 'table', workType: 'publication', title: 'Публикации файлом', desc: '.xlsx или .csv по шаблону либо BibTeX и RIS — выгрузки Scopus, Web of Science, Google Scholar, Zotero, Mendeley.' },
  { kind: 'table', workType: 'talk', title: 'Выступления таблицей', desc: 'Файл .xlsx или .csv по шаблону.' },
  { kind: 'doi', workType: 'publication', title: 'Публикации по DOI', desc: 'Вставьте DOI пачкой — карточки заполнит Crossref. Уже известные DOI пропускаются.' },
  { kind: 'text', workType: 'publication', title: 'Публикации списком', desc: 'Список текстом из eLIBRARY, отчёта или резюме: ИИ разберёт каждую запись в карточку.' },
  { kind: 'text', workType: 'talk', title: 'Выступления списком', desc: 'Программа конференции или список докладов текстом: ИИ разберёт каждую запись.' },
] as const
type Variant = (typeof VARIANTS)[number]

const DEMO: Partial<Record<WorkType, string>> = {
  vkr: '/mock/demo-vkr-meta.csv',
  publication: '/mock/demo-publications.csv',
  talk: '/mock/demo-talks.csv',
}

function TablePreview({ p }: { p: ImportPreview }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-ink-2">
        Строк: {p.total}. {p.bad > 0 ? <span className="text-warn">С пропущенными обязательными полями: {p.bad} — они будут отмечены как ошибки.</span> : 'Обязательные поля заполнены.'}
        {p.total > p.rows.length && ` Показаны первые ${p.rows.length}.`}
      </p>
      <div className="max-h-80 overflow-auto border border-rule">
        <table className="text-xs">
          <thead className="sticky top-0 bg-panel">
            <tr><th className="p-2 text-left">№</th>{p.header.map((h, i) => <th key={i} className="whitespace-nowrap p-2 text-left font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {p.rows.map((r) => (
              <tr key={r.line} className={r.missing.length ? 'bg-warn-bg' : 'odd:bg-white even:bg-panel'} title={r.missing.length ? `Не заполнено: ${r.missing.join(', ')}` : undefined}>
                <td className="p-2 text-muted">{r.line}{r.missing.length > 0 && <span className="sr-only"> (не заполнено: {r.missing.join(', ')})</span>}</td>
                {p.header.map((_, j) => <td key={j} className="max-w-[16rem] truncate p-2">{r.cells[j]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function ImportNew() {
  useTitle('Новый импорт')
  const [variant, setVariant] = useState<Variant>(VARIANTS[0])
  return (
    <div className="space-y-6">
      <h1 className="text-4xl font-bold">Новый импорт</h1>

      <fieldset className="grid sm:grid-cols-3">
        <legend className="mb-2 font-bold">Что загружаем</legend>
        {VARIANTS.map((v) => (
          <label key={v.title} className={cn('flex cursor-pointer gap-3 p-5 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-brand', variant === v ? 'bg-brand text-white' : 'bg-panel hover:bg-panel-2')}>
            <input type="radio" name="variant" className="sr-only" checked={variant === v} onChange={() => setVariant(v)} />
            <TypeIcon type={v.workType} className="mt-1 h-5 w-5" />
            <span><span className="block text-lg font-bold">{v.title}</span><span className="text-[15px]">{v.desc}</span></span>
          </label>
        ))}
      </fieldset>

      {/* key: another variant starts with a clean form (files, preview, errors) */}
      <ImportForm key={variant.title} variant={variant} />
    </div>
  )
}

function ImportForm({ variant }: { variant: Variant }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [files, setFiles] = useState<File[]>([]) // archive: PDFs and ZIPs
  const [fileErr, setFileErr] = useState<string>()
  const [meta, setMeta] = useState<File | null>(null)
  const [metaErr, setMetaErr] = useState<string>()
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [serverErr, setServerErr] = useState<string>()
  const [text, setText] = useState('') // kind text / doi: the pasted list
  const [doiResult, setDoiResult] = useState<OrcidImport>()
  const [busy, setBusy] = useState(false) // DOI lookup: nothing to upload, just a spinner on the button
  const abort = useRef<AbortController | null>(null)

  const isArchive = variant.kind === 'archive'
  const isPaste = variant.kind === 'text' || variant.kind === 'doi'
  const tableFile = isArchive ? meta : file
  const limits = useQuery({ queryKey: ['upload-limits'], queryFn: getUploadLimits, staleTime: Infinity })
  const columns = useQuery({ queryKey: ['admin-template', variant.workType], queryFn: () => getTemplateColumns(variant.workType), staleTime: Infinity })

  async function onTable(f: File | null, err?: string) {
    const [set, setErr] = isArchive ? [setMeta, setMetaErr] : [setFile, setFileErr]
    set(f); setErr(err); setPreview(null)
    if (!f) return
    try {
      setPreview(await previewImportTable(variant.workType, f))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось прочитать таблицу')
    }
  }

  const headersBad = !!preview?.missingColumns.length
  const chosen = isPaste ? !!text.trim() : isArchive ? files.length > 0 : !!file
  const canUpload = chosen && !headersBad && progress === null && !(tableFile && !preview)

  async function upload() {
    if (!chosen) return
    if (variant.kind === 'doi') return importDois()
    const form = new FormData()
    form.append('kind', variant.kind)
    form.append('workType', variant.workType)
    if (variant.kind === 'text') form.append('text', text)
    else for (const f of isArchive ? files : [file!]) form.append('file', f)
    if (meta) form.append('metaTable', meta)
    abort.current = new AbortController()
    setServerErr(undefined)
    setProgress(0)
    try {
      const imp = await createImport(form, setProgress, abort.current.signal)
      refreshAll(qc)
      navigate(`/admin/imports/${imp.id}`)
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setServerErr(e instanceof Error ? e.message : 'Ошибка загрузки')
      setProgress(null)
    }
  }

  // DOI go straight to drafts (no import record): the server answers with a ready summary
  async function importDois() {
    setServerErr(undefined); setDoiResult(undefined); setBusy(true)
    try {
      setDoiResult(await importDoiList(text))
      refreshAll(qc)
    } catch (e) {
      setServerErr(e instanceof Error ? e.message : 'Ошибка импорта')
    } finally {
      setBusy(false)
    }
  }

  // where the table template goes: on top for table imports, under the optional table of a PDF upload
  const template = (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Button variant="outline" size="sm" onClick={() => downloadTemplate(variant.workType)}>
        Шаблон таблицы
      </Button>
      {columns.data && <span className="text-muted">Колонки: {columns.data.map((c) => c.name + (c.required ? '*' : '')).join(', ')}</span>}
      {import.meta.env.VITE_USE_MOCKS === 'true' && DEMO[variant.workType] && (
        <a href={DEMO[variant.workType]} download className="text-ink hover:underline">Демо-таблица</a>
      )}
    </div>
  )

  return (
    <Card className="space-y-5">
      {variant.kind === 'table' && template}

      {isPaste ? (
        <label className="block space-y-1">
          <span className="font-medium">{variant.kind === 'doi' ? 'DOI — через пробел, запятую или с новой строки, можно ссылками doi.org' : 'Список: по записи на абзац или пронумерованный («1. …», «2. …»)'}</span>
          <Textarea rows={12} value={text} onChange={(e) => { setText(e.target.value); setDoiResult(undefined) }}
            placeholder={variant.kind === 'doi' ? '10.1137/090752286\nhttps://doi.org/10.1016/j.jcp.2015.10.015' : '1. Орлов Д. С. Графовые нейронные сети // Вестник университета. 2025. № 2. С. 10–20.'} />
        </label>
      ) : limits.isError ? <ErrorState error={limits.error} onRetry={() => limits.refetch()} />
        : !limits.data ? <Spinner />
        : isArchive ? (
        <div className="grid gap-4 md:grid-cols-2">
          <FileDrop label="Работы: PDF или ZIP" {...limits.data.archive} multiple files={files} error={fileErr}
            onFiles={(f, e) => { setFiles(f); setFileErr(e) }} />
          <div className="space-y-2">
            <FileDrop label="Таблица сведений (необязательно)" {...limits.data.table} file={meta} error={metaErr}
              onFile={(f, e) => onTable(f, e)} />
            <p className="text-sm text-muted">Нужна, только если хотите задать поля сами: они лягут поверх того, что прочитает ИИ.</p>
            {template}
          </div>
        </div>
      ) : (
        <FileDrop label="Таблица" {...limits.data.table} file={file} error={fileErr} onFile={(f, e) => onTable(f, e)} />
      )}

      {headersBad && (
        <Alert tone="red" title="Неверные заголовки колонок">
          В таблице нет обязательных колонок: {preview!.missingColumns.join(', ')}.{' '}
          <button type="button" className="font-medium underline" onClick={() => downloadTemplate(variant.workType)}>Скачайте шаблон</button> и перенесите данные в него.
        </Alert>
      )}
      {preview && !headersBad && preview.total > 0 && <TablePreview p={preview} />}
      {preview && !headersBad && preview.total === 0 && <Alert>В таблице нет строк с данными.</Alert>}
      {serverErr && <Alert tone="red" title="Сервер отклонил загрузку">{serverErr}</Alert>}
      {doiResult && (
        <Alert tone="blue">
          {doiResult.message}{' '}
          {doiResult.created > 0 && <button type="button" className="font-medium underline" onClick={() => navigate('/admin/review?type=publication')}>Проверить черновики</button>}
          {doiResult.notFound.length > 0 && <span className="mt-1 block text-sm">Нет в Crossref: {doiResult.notFound.join(', ')}</span>}
        </Alert>
      )}

      {progress !== null ? (
        <div className="space-y-2">
          <div className="h-2 overflow-hidden bg-panel-2" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Загрузка">
            <div className="h-full bg-brand transition-all" style={{ width: `${Math.max(3, progress * 100)}%` }} />
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span>Загружено {Math.round(progress * 100)}%</span>
            <Button variant="outline" size="sm" onClick={() => abort.current?.abort()}>Отменить</Button>
          </div>
        </div>
      ) : (
        <Button onClick={upload} disabled={!canUpload} loading={busy}>{isPaste ? 'Импортировать' : 'Загрузить'}</Button>
      )}
    </Card>
  )
}
