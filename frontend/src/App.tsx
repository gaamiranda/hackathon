import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { RunListPage } from './pages/RunListPage'
import { RunPage } from './pages/RunPage'

export default function App() {
  return (
    <BrowserRouter>
      <header className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-4 py-2">
        <Link to="/" className="text-sm font-semibold tracking-widest text-zinc-100">
          PROCURE<span className="text-emerald-400">AI</span>
        </Link>
        <span className="text-xs text-zinc-500">war room · week 1</span>
      </header>
      <Routes>
        <Route path="/" element={<RunListPage />} />
        <Route path="/runs/:id" element={<RunPage />} />
      </Routes>
    </BrowserRouter>
  )
}
