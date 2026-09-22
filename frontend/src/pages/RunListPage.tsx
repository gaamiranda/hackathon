import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { API_URL, api, ApiError } from '../api/client'
import type { CreateRunInput, RunSummary } from '../api/types'
import { Button, ErrorLine, Panel } from '../components/Panel'
import { StateBadge } from '../components/StateBadge'
import { isoDateInDays } from '../format'
import { useHealth } from '../hooks/useHealth'

/** The two demo stories, kept in step with backend/scripts/seed_demo.py (`just seed STAGE [--scenario b]`).
 *  A: PLAN.md §15, 2,000 × Product X within 14 days, 30,000 USD.
 *  B: PLAN.md §11 T22, 10,000 M8 stainless hex bolts within 21 days, 9,500 USD (data/synthetic/scenario_b/). */
const PRESETS: { label: string; hint: string; build: () => CreateRunInput }[] = [
  {
    label: 'Demo A: 2,000 Product X, 14 days, 30,000 USD',
    hint: 'the three quotes in data/synthetic/',
    build: () => ({ product: 'Product X', quantity: 2000, required_by: isoDateInDays(14), budget: '30000.00', currency: 'USD' }),
  },
  {
    label: 'Demo B: 10,000 M8 bolts, 21 days, 9,500 USD',
    hint: 'the four quotes in data/synthetic/scenario_b/',
    build: () => ({ product: 'M8 stainless hex bolts', quantity: 10000, required_by: isoDateInDays(21), budget: '9500.00', currency: 'USD' }),
  },
]
const DEMO_REQUEST = PRESETS[0].build

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

  /** Prefills the form with a demo request and creates the run in one click. */
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
          <Button type="submit" disabled={busy || !!error}>
            {busy ? 'Creating…' : 'Create run'}
          </Button>
          <div className="space-y-1 border-t border-zinc-800 pt-2">
            <p className="text-[10px] uppercase tracking-wide text-zinc-500">Demo presets</p>
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setForm(preset.build())}
                className="block w-full rounded border border-zinc-700 px-2 py-1 text-left text-xs text-zinc-300 transition-colors hover:border-emerald-700 hover:bg-emerald-950/30"
                title={`fills the form above with ${preset.hint}; nothing is sent until you click Create run`}
                disabled={busy}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <ErrorLine error={formError} />
        </form>
      </Panel>
    </div>
  )
}
