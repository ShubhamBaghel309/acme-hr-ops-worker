import { useEffect, useState } from 'react'
import RunView, { statusLabel } from './RunView.jsx'

export default function App() {
  const [runs, setRuns] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [selected, setSelected] = useState(null)
  const [request, setRequest] = useState('')
  const [scenario, setScenario] = useState('')
  const [error, setError] = useState(null)

  const loadRuns = () => fetch('/api/runs').then(r => r.json()).then(setRuns).catch(() => {})

  useEffect(() => {
    loadRuns()
    const timer = setInterval(loadRuns, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    fetch('/api/scenarios').then(r => r.json()).then(d => setScenarios(d.scenarios)).catch(() => {})
  }, [])

  async function start(event) {
    event.preventDefault()
    setError(null)
    const r = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request, scenario: scenario || null }),
    })
    const data = await r.json()
    if (!r.ok) return setError(data.detail || 'Could not start the task.')
    setSelected(data.thread_id)
    setRequest('')
    loadRuns()
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1 className="app-title">AI Worker</h1>

        <form onSubmit={start}>
          <div className="section-title">New task</div>
          <div className="composer">
            <textarea value={request} onChange={e => setRequest(e.target.value)} aria-label="Task"
                      placeholder="Describe the outcome you want…" />
            <div className="composer-row">
              <select className="select" value={scenario} onChange={e => setScenario(e.target.value)}
                      aria-label="Sandbox scenario">
                <option value="">Keep sandbox</option>
                {scenarios.map(s => <option key={s} value={s}>{s.replaceAll('_', ' ')}</option>)}
              </select>
              <button className="btn btn-primary" disabled={!request.trim()}>Start</button>
            </div>
          </div>
          {error && <p className="hint" style={{ color: 'var(--red)' }}>{error}</p>}
        </form>

        <div>
          <div className="section-title">Sessions</div>
          <ul className="source-list">
            {runs.map(r => (
              <li key={r.thread_id} className={selected === r.thread_id ? 'selected' : ''}
                  onClick={() => setSelected(r.thread_id)}>
                <span className={`dot ${r.status}`} />
                <div className="row-text">
                  <div className="row-title">{r.request}</div>
                  <div className="row-subtitle">{statusLabel(r.status)}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <main className="content">
        {selected ? <RunView key={selected} threadId={selected} />
          : <div className="empty">Describe a task, or choose a session<br />to see what the worker did.</div>}
      </main>
    </div>
  )
}
