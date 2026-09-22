import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { LlmBanner } from './components/LlmBanner'
import { HealthContext, useHealthPolling } from './hooks/useHealth'
import { RunListPage } from './pages/RunListPage'
import { RunPage } from './pages/RunPage'

export default function App() {
  // /health every 15 s for both pages (T17): the banner under the header says when the AI is degraded or gone.
  const health = useHealthPolling()
  return (
    <BrowserRouter>
      <HealthContext.Provider value={health}>
        {/* header + banner + page as one column, so the run page fills what is left whatever the banner's height */}
        <div className="flex h-screen flex-col">
          <header className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-4 py-2">
            <Link to="/" className="text-sm font-semibold tracking-widest text-zinc-100">
              PROCURE<span className="text-emerald-400">AI</span>
            </Link>
            <span className="text-xs font-medium tracking-wide text-zinc-400">Autonomous Procurement War Room</span>
            <span className="ml-auto hidden text-[11px] text-zinc-600 md:inline">An auditable AI procurement team — humans approve every consequential action</span>
          </header>
          <LlmBanner health={health} />
          <main className="min-h-0 flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<RunListPage />} />
              <Route path="/runs/:id" element={<RunPage />} />
            </Routes>
          </main>
        </div>
      </HealthContext.Provider>
    </BrowserRouter>
  )
}
