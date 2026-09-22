import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { API_URL, api, ApiError } from '../api/client'
import type { CreateRunInput, RunSummary } from '../api/types'
import { Button, ErrorLine, Panel } from '../components/Panel'
import { StateBadge } from '../components/StateBadge'
import { isoDateInDays } from '../format'
import { useHealth } from '../hooks/useHealth'

/** PLAN.md §15: 2,000 × Product X within 14 days, budget 30,000 USD. */
const DEMO_REQUEST = (): CreateRunInput => ({ product: 'Product X', quantity: 2000, required_by: isoDateInDays(14), budget: '30000.00', currency: 'USD' })

export function RunListPage() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[] | null>(null)
  const health = useHealth()
  const [error, setError] = useState<ApiError | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<CreateRunInput>(DEMO_REQUEST)

  const load = useCallback(() => {
    setError(null)
    api
      .listRuns()
      .then(setRuns)
      .catch((e: ApiError) => setError(e))
  }, [])
  useEffect(load, [load])

  const create = async (input: CreateRunInput) => {
    setBusy(true)
    setFormError(null)
    try {
      const { run_id } = await api.createRun({ ...input, quantity: Number(input.quantity) })
      navigate(`/runs/${run_id}`)
    } catch (err) {
      setFormError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void create(form)
  }

  /** Prefills the form with the §15 demo request and creates the run in one click. */
  const createDemo = () => {
    const demo = DEMO_REQUEST()
    setForm(demo)
    void create(demo)
  }

  const field = 'w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none'
  const unreachable = error?.status === 0

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_360px] gap-4 p-6">
      <Panel title="Runs">
        {error ? (
          <div className="rounded border border-red-900 bg-red-950/40 p-4 text-sm">
            <p className="font-semibold text-red-200">{unreachable ? 'Backend unreachable' : 'Could not load runs'}</p>
            <p className="mt-1 text-zinc-400">{unreachable ? `Nothing is answering at ${API_URL}. Start the backend (just run) or check VITE_API_URL.` : error.message}</p>
            <div className="mt-3">
              <Button tone="ghost" onClick={load}>
                Retry
              </Button>
            </div>
          </div>
        ) : runs === null ? (
          <p className="text-sm text-zinc-500">Loading runs…</p>
        ) : runs.length === 0 ? (
          <div className="rounded border border-dashed border-zinc-700 p-6 text-center text-sm">
            <p className="text-zinc-300">No runs yet.</p>
            <p className="mt-1 text-zinc-500">Start with the demo request — 2,000 units of Product X within 14 days — then drop the three supplier quotes on the run page.</p>
            <div className="mt-4">
              <Button onClick={createDemo} disabled={busy}>
                {busy ? 'Creating…' : 'Create the demo run'}
              </Button>
            </div>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="py-1 pr-4">Run</th>
                <th className="pr-4">Product</th>
                <th className="pr-6 text-right">Quantity</th>
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
      </Panel>

      <Panel
        title="New run"
        right={
          health && (
            <span className="mono text-xs text-zinc-500" title={`LLM gateway ${health.llm_gateway} · llm ${health.llm}`}>
              {health.mode === 'mock' ? 'mock agents' : 'live agents'} · via {health.llm_backend}
              {health.llm_backend === 'openclaw' && <span className={health.openclaw === 'reachable' ? 'text-emerald-400' : 'text-red-400'}> ({health.openclaw})</span>} · judge{' '}
              {health.guardrail_judge === 'jev' ? 'Jev' : 'mock'}
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
          <div className="flex items-center gap-2">
            <Button type="submit" disabled={busy || !!error}>
              {busy ? 'Creating…' : 'Create run'}
            </Button>
            <button type="button" onClick={() => setForm(DEMO_REQUEST())} className="text-xs text-zinc-500 hover:text-zinc-300" disabled={busy}>
              Reset to demo request
            </button>
          </div>
          <ErrorLine error={formError} />
        </form>
      </Panel>
    </div>
  )
}
