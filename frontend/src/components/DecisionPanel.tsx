import type { Run } from '../api/types'
import { money } from '../format'
import { Panel } from './Panel'

export function DecisionPanel({ run }: { run: Run }) {
  const names = Object.fromEntries(run.quotes.map((q) => [q.supplier_id, q.supplier_name]))
  const rec = run.recommendation
  return (
    <Panel title="Decision">
      {run.scorecards.length === 0 ? (
        <p className="text-sm text-zinc-500">No scorecards yet. Upload quotes and evaluate.</p>
      ) : (
        <ol className="space-y-2">
          {run.scorecards.map((c, i) => {
            const top = rec?.recommended_supplier_id === c.supplier_id
            return (
              <li key={c.supplier_id} className={`rounded border p-3 text-sm ${top ? 'border-emerald-700 bg-emerald-950/40' : 'border-zinc-800 bg-zinc-950/40'}`}>
                <div className="flex items-baseline gap-2">
                  <span className="mono text-zinc-500">#{i + 1}</span>
                  <span className={`truncate ${top ? 'font-semibold text-emerald-300' : ''}`}>{names[c.supplier_id] ?? c.supplier_id}</span>
                  <span className="mono text-xs text-zinc-500">{c.supplier_id}</span>
                  <span className="mono ml-auto text-lg">{c.total_score.toFixed(1)}</span>
                </div>
                <div className="mt-1.5 h-2 rounded bg-zinc-800">
                  <div className={`h-2 rounded ${c.eligible ? 'bg-emerald-500' : 'bg-zinc-600'}`} style={{ width: `${Math.max(0, Math.min(100, c.total_score))}%` }} />
                </div>
                <div className="mono mt-1.5 flex gap-4 text-xs text-zinc-400">
                  <span>
                    landed <span className="text-zinc-200">{money(c.landed_cost)}</span>
                  </span>
                  <span>
                    lead <span className="text-zinc-200">{c.lead_time_days} d</span>
                  </span>
                  <span className="ml-auto">{c.eligible ? 'eligible' : <span className="text-red-300">ineligible</span>}</span>
                </div>
                {c.eligible ? (
                  <div className="mono mt-1 text-[10px] text-zinc-500">
                    {Object.entries(c.score_breakdown)
                      .map(([k, v]) => `${k} ${v.toFixed(1)}`)
                      .join(' · ')}
                  </div>
                ) : (
                  <div className="mt-1 text-xs text-red-300">{c.ineligibility_reasons.join('; ')}</div>
                )}
              </li>
            )
          })}
        </ol>
      )}

      {rec && (
        <div className="mt-4 space-y-3 text-sm">
          <div>
            <h3 className="text-xs uppercase tracking-wider text-zinc-500">Recommendation</h3>
            <p className="mt-1 text-emerald-200">
              {rec.recommended_supplier_id ? `${names[rec.recommended_supplier_id] ?? rec.recommended_supplier_id} (${rec.recommended_supplier_id})` : 'No eligible supplier'}
            </p>
          </div>
          <div>
            <h3 className="text-xs uppercase tracking-wider text-zinc-500">Rationale</h3>
            <p className="mt-1 leading-relaxed text-zinc-200">{rec.rationale}</p>
          </div>
          {rec.change_explanation && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-zinc-500">What changed</h3>
              <p className="mt-1 leading-relaxed text-amber-100">{rec.change_explanation}</p>
            </div>
          )}
          {rec.escalation && <p className="rounded border border-amber-800 bg-amber-950/50 p-2 text-amber-200">Escalation: {rec.escalation.reason}</p>}
        </div>
      )}
    </Panel>
  )
}
