import { useEffect, useRef, useState } from 'react'
import Icon from './Icons.jsx'
import RunView, { statusLabel } from './RunView.jsx'

const EXAMPLES = [
  {
    title: 'Onboard a new hire', scenario: 'onboard_happy', request: 'Onboard Priya Sharma - her offer is signed.',
    blurb: 'Reads the offer letter, creates the HR record, raises IT access and emails everyone. Pauses for your approval.',
  },
  {
    title: 'Survive a broken system', scenario: 'onboard_chaos', request: 'Onboard Priya Sharma - her offer is signed.',
    blurb: 'Sessions expire, a save fails after saving, buttons get renamed. It recovers without creating duplicates.',
  },
  {
    title: 'Offboard a leaver', scenario: 'offboard_happy', request: 'Please offboard Rahul Mehta.',
    blurb: 'Same worker, different procedure. Ends employment, revokes access and tells the manager.',
  },
]

const HOW = [
  ['sparkle', 'Understands', 'Reads the company playbook and turns one sentence into a plan.'],
  ['click', 'Acts', 'Works the real apps in a browser, step by step.'],
  ['verdict', 'Verifies', 'An independent check re-reads every record.'],
  ['open_attachment', 'Proves', 'Every claim comes with a quote and a screenshot.'],
]

function Home({ onPick }) {
  return (
    <div className="home">
      <div className="hero">
        <div className="eyebrow"><Icon name="sparkle" size={15} /> Autonomous AI worker</div>
        <h1 className="hero-title">You ask once.<br /><span className="hero-dim">It does the work, then proves it.</span></h1>
        <p className="hero-sub">
          An AI employee that operates a company's own apps in a real browser, follows its procedures,
          asks before anything risky, and verifies the result before calling it done.
        </p>
      </div>

      <div className="section-label">Try it</div>
      <div className="examples">
        {EXAMPLES.map(e => (
          <button key={e.scenario} className="example" onClick={() => onPick(e)}>
            <div className="example-title">{e.title}</div>
            <div className="example-request">“{e.request}”</div>
            <div className="example-blurb">{e.blurb}</div>
            <div className="example-cta">Use this task <Icon name="arrow" size={14} /></div>
          </button>
        ))}
      </div>

      <div className="section-label">How it works</div>
      <div className="how">
        {HOW.map(([icon, title, text], i) => (
          <div className="how-item" key={title} style={{ animationDelay: `${i * 70}ms` }}>
            <div className="how-icon"><Icon name={icon} size={20} /></div>
            <div className="how-title">{title}</div>
            <div className="how-text">{text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const [runs, setRuns] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [selected, setSelected] = useState(null)
  const [request, setRequest] = useState('')
  const [scenario, setScenario] = useState('')
  const [error, setError] = useState(null)
  const taskBox = useRef(null)

  const loadRuns = () => fetch('/api/runs').then(r => r.json()).then(setRuns).catch(() => {})

  useEffect(() => {
    loadRuns()
    const timer = setInterval(loadRuns, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    fetch('/api/scenarios').then(r => r.json()).then(d => setScenarios(d.scenarios)).catch(() => {})
  }, [])

  function pick(example) {
    setRequest(example.request)
    setScenario(example.scenario)
    setSelected(null)
    taskBox.current?.focus()
  }

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
        <h1 className="app-title" onClick={() => setSelected(null)}>AI Worker</h1>

        <form onSubmit={start}>
          <div className="section-title">New task</div>
          <div className={`composer ${request ? 'filled' : ''}`}>
            <textarea ref={taskBox} value={request} onChange={e => setRequest(e.target.value)} aria-label="Task"
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
            {runs.length === 0 && <li className="none">No sessions yet</li>}
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
        {selected ? <RunView key={selected} threadId={selected} /> : <Home onPick={pick} />}
      </main>
    </div>
  )
}
