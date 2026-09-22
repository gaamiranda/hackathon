import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Health, RunSummary } from '../api/types'
import { Button, ErrorLine, Panel } from '../components/Panel'
import { StateBadge } from '../components/StateBadge'
import { isoDateInDays } from '../format'

export function RunListPage() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ product: 'Product X', quantity: 2000, required_by: isoDateInDays(14), budget: '30000.00', currency: 'USD' })

  useEffect(() => {
    api.listRuns().then(setRuns).catch((e: ApiError) => setError(e.message))
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { run_id } = await api.createRun({ ...form, quantity: Number(form.quantity) })
      navigate(`/runs/${run_id}`)
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const field = 'w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none'

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_360px] gap-4 p-6">
      <Panel title="Runs">
        {runs.length === 0 ? (
          <p className="text-sm text-zinc-500">No runs yet. Create one on the right.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="py-1 pr-4">Run</th>
                <th className="pr-4">Product</th>
                <th className="pr-6 text-right">Qty</th>
                <th className="pr-4">State</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {[...runs].reverse().map((r) => (
                <tr key={r.run_id} className="border-t border-zinc-800 hover:bg-zinc-800/40">
                  <td className="py-2 pr-4">
                    <Link className="mono text-emerald-400 hover:underline" to={`/runs/${r.run_id}`}>
                      {r.run_id}
                    </Link>
                  </td>
                  <td className="pr-4">{r.product}</td>
                  <td className="mono pr-6 text-right">{r.quantity.toLocaleString()}</td>
                  <td className="pr-4">
                    <StateBadge state={r.state} />
                  </td>
                  <td className="text-zinc-400">{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <ErrorLine error={error} />
      </Panel>

      <Panel
        title="New run"
        right={
          health && (
            <span className="mono text-xs text-zinc-500">
              backend: {health.mode} · llm {health.llm_gateway} · via {health.llm_backend}
              {health.llm_backend === 'openclaw' && <span className={health.openclaw === 'reachable' ? 'text-emerald-400' : 'text-red-400'}> ({health.openclaw})</span>} · judge {health.guardrail_judge}
            </span>
          )
        }
      >
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs text-zinc-400">
            Product
            <input className={field} value={form.product} onChange={(e) => setForm({ ...form, product: e.target.value })} required />
          </label>
          <label className="block text-xs text-zinc-400">
            Quantity
            <input className={field} type="number" min={1} value={form.quantity} onChange={(e) => setForm({ ...form, quantity: Number(e.target.value) })} required />
          </label>
          <label className="block text-xs text-zinc-400">
            Required by
            <input className={field} type="date" value={form.required_by} onChange={(e) => setForm({ ...form, required_by: e.target.value })} required />
          </label>
          <div className="grid grid-cols-[1fr_90px] gap-2">
            <label className="block text-xs text-zinc-400">
              Budget
              <input className={field} inputMode="decimal" pattern="^\d+(\.\d{1,2})?$" value={form.budget} onChange={(e) => setForm({ ...form, budget: e.target.value })} required />
            </label>
            <label className="block text-xs text-zinc-400">
              Currency
              <input className={field} maxLength={3} pattern="[A-Z]{3}" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} required />
            </label>
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create run'}
          </Button>
        </form>
      </Panel>
    </div>
  )
}
