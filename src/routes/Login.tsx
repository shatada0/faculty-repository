import { useState } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, login } from '@/api'
import { homeFor, useMe, useTitle } from '@/components/Layout'
import { Alert, Button, Card, Field, Input } from '@/components/ui'
import { safeNext } from '@/lib/url'

export default function Login() {
  const [sp] = useSearchParams()
  const next = safeNext(sp.get('next'))
  const navigate = useNavigate()
  const qc = useQueryClient()
  const me = useMe()
  useTitle('Вход')
  const [form, setForm] = useState({ login: '', password: '' })
  const [error, setError] = useState<string>()
  const [busy, setBusy] = useState(false)

  if (me.data) return <Navigate to={next ?? homeFor(me.data)} replace />

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(undefined)
    try {
      const { user } = await login(form.login.trim(), form.password)
      qc.setQueryData(['me'], user)
      navigate(next ?? homeFor(user), { replace: true })
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? 'Неверный логин или пароль' : e instanceof Error ? e.message : 'Не удалось войти')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-sm pt-6 sm:pt-16">
      <Card className="p-6">
        <h1 className="mb-1 text-xl font-bold">Вход</h1>
        <p className="mb-5 text-sm text-ink-2">Для модераторов и авторов работ</p>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Логин">{(id) => <Input id={id} autoComplete="username" required value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} />}</Field>
          <Field label="Пароль">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />}</Field>
          {error && <Alert tone="red">{error}</Alert>}
          <Button type="submit" className="w-full" loading={busy}>Войти</Button>
        </form>
      </Card>
    </div>
  )
}
