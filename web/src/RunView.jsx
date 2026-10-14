import { useCallback, useEffect, useState } from 'react'
import PendingCard from './PendingCard.jsx'

const LABELS = {
  running: 'Working', done: 'Done', stopped: 'Needs you', done_with_failures: 'Not done',
  budget_exhausted: 'Not done',
}
export const statusLabel = status => LABELS[status] || status || 'Starting'

const BAD = new Set(['rejected', 'page_error', 'server_error', 'auth_expired', 'blocked', 'stale_ref', 'error',
  'not_found', 'verify_failed', 'denied'])
const WAIT = new Set(['needs_approval', 'asked', 'stopped'])

const fill = (text, facts) => text.replace(/\{(\w+)\}/g, (m, key) => (facts[key] ? facts[key].raw : m))

function Icon({ kind }) {
  if (kind === 'pass') return (
    <svg className="icon pass" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="M7.5 12.5l3 3 6-6.5" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>)
  if (kind === 'fail') return (
    <svg className="icon fail" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" stroke="#fff" strokeWidth="2" strokeLinecap="round" /></svg>)
  if (kind === 'warn') return (
    <svg className="icon" style={{ color: 'var(--orange)' }} viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="M12 7v6" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" /><circle cx="12" cy="16.5" r="1.3" fill="#fff" /></svg>)
  return (
    <svg className="icon pending" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" fill="none" /></svg>)
}

function statusOf(run) {
  if (run.pending) return { dot: 'waiting', text: 'Waiting for you' }
  if (run.active) return { dot: 'running', text: run.mode === 'verify' ? 'Verifying its work' : 'Working' }
  return { dot: run.status, text: statusLabel(run.status) }
}

function Criteria({ criteria }) {
  return (
    <div className="group">
      {criteria.map(c => (
        <div className="group-row" key={c.id}>
          <Icon kind={c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : 'pending'} />
          <div className="row-body">
            <div className="row-label">{c.text}</div>
            {(c.quotes || []).slice(0, 3).map((q, i) => <div className="quote" key={i}>“<span>{q}</span>”</div>)}
            {c.status === 'fail' && c.note && <div className="row-secondary">Verifier: {c.note}</div>}
            {c.url && c.status !== 'not checked' && <div className="row-tertiary">Checked on {c.url}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

function Summary({ run }) {
  const { report, spec, facts, verdicts } = run
  if (!report) {
    if (!spec) return <p className="footnote">Reading the request and the company's procedures…</p>
    const criteria = spec.criteria.map(c => ({ ...c, text: fill(c.text, facts), ...(verdicts[c.id] || { status: 'not checked' }) }))
    return (
      <>
        <div className="group-title">Goal</div>
        <div className="group"><div className="group-row plain"><div className="row-body">
          <div className="row-label">{spec.goal}</div>
          <div className="row-tertiary">Procedure: {spec.sop}</div>
        </div></div></div>
        <div className="group-title">Plan</div>
        <div className="group">
          {spec.plan.map((p, i) => (
            <div className="group-row plain" key={i}><span className="step-n">{i + 1}</span><div className="row-body">{p}</div></div>
          ))}
        </div>
        <div className="group-title">Success criteria</div>
        <Criteria criteria={criteria} />
      </>
    )
  }
  const good = report.status === 'done'
  return (
    <>
      <div className="group">
        <div className="banner">
          <Icon kind={good ? 'pass' : report.status === 'stopped' ? 'warn' : 'fail'} />
          <div>
            <div className="banner-title">{report.headline}</div>
            <div className="row-secondary">
              {good ? 'Every criterion was independently verified.'
                : report.stopped_because ? `Stopped: ${report.stopped_because}` : 'Some criteria were not met.'}
            </div>
          </div>
        </div>
      </div>

      {report.answer.length > 0 && (
        <>
          <div className="group-title">Answer</div>
          <div className="group">
            {report.answer.map(a => (
              <div className="group-row plain" key={a.key}><div className="row-body">
                <div className="row-label">{a.raw}</div>
                <div className="quote">“<span>{a.quote}</span>”</div>
              </div></div>
            ))}
          </div>
        </>
      )}

      <div className="group-title">Independently verified</div>
      <Criteria criteria={report.criteria} />

      {report.human.length > 0 && (
        <>
          <div className="group-title">Your decisions</div>
          <div className="group">
            {report.human.map((h, i) => (
              <div className="group-row plain" key={i}><div className="row-body">
                {h.decision
                  ? <><div className="row-label">{h.description}</div><div className="row-secondary">{h.decision === 'approved' ? 'Approved' : 'Denied'}</div></>
                  : <><div className="row-label">{h.question}</div><div className="row-secondary">{h.answer}</div></>}
              </div></div>
            ))}
          </div>
        </>
      )}

      {report.recoveries.length > 0 && (
        <>
          <div className="group-title">Handled along the way</div>
          <div className="group">
            {report.recoveries.map(r => (
              <div className="group-row plain" key={r.step}><div className="row-body">
                <div className="row-label">{r.message}</div>
                <div className="row-tertiary">Step {r.step} · {r.outcome.replaceAll('_', ' ')}</div>
              </div></div>
            ))}
          </div>
        </>
      )}

      <p className="footnote">
        {report.steps.work} work steps · {report.steps.verify} verification steps ·{' '}
        {(report.tokens.tokens_in + report.tokens.tokens_out).toLocaleString()} tokens
      </p>
    </>
  )
}

function Activity({ steps, onZoom }) {
  if (!steps.length) return <p className="footnote">No actions yet.</p>
  return (
    <div className="group">
      {[...steps].reverse().map(s => {
        const args = s.tool === 'human' ? '' : Object.values(s.args).map(v => (typeof v === 'string' ? v : JSON.stringify(v))).join(' · ')
        return (
          <div className="group-row plain" key={s.n}>
            <span className="step-n">{s.n}</span>
            <div className="row-body">
              <div className="step-title">
                <span className="step-tool">{s.tool.replaceAll('_', ' ')}</span>
                <span className={`step-outcome ${BAD.has(s.outcome) ? 'bad' : WAIT.has(s.outcome) ? 'wait' : ''}`}>
                  {s.outcome.replaceAll('_', ' ')}
                </span>
                {s.mode === 'verify' && <span className="step-mode">verify</span>}
              </div>
              {s.why && <div className="row-secondary">{s.why}</div>}
              {args && <div className="row-tertiary">{args.length > 160 ? args.slice(0, 160) + '…' : args}</div>}
              {s.message && <div className="row-tertiary">{s.message}</div>}
            </div>
            {s.screenshot && <img className="thumb" src={`/runs/${s.screenshot}`} alt="" onClick={() => onZoom(s.screenshot)} />}
          </div>
        )
      })}
    </div>
  )
}

function Facts({ facts }) {
  const entries = Object.entries(facts)
  if (!entries.length) return <p className="footnote">Nothing recorded yet.</p>
  return (
    <>
      <div className="group">
        {entries.map(([key, f]) => (
          <div className="group-row plain" key={key}><div className="row-body">
            <div className="fact-key">{key.replaceAll('_', ' ')}</div>
            <div className="fact-value">{f.raw}</div>
            <div className="quote">“<span>{f.quote}</span>” · {f.source}</div>
          </div></div>
        ))}
      </div>
      <p className="footnote">Every fact was checked against the quoted source before the worker could use it.</p>
    </>
  )
}

export default function RunView({ threadId }) {
  const [run, setRun] = useState(null)
  const [tab, setTab] = useState('summary')
  const [zoom, setZoom] = useState(null)

  const load = useCallback(() => {
    fetch(`/api/runs/${threadId}`).then(r => (r.ok ? r.json() : null)).then(data => data && setRun(data)).catch(() => {})
  }, [threadId])

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [load])

  if (!run) return <div className="empty">Loading…</div>

  const status = statusOf(run)
  const resume = () => fetch(`/api/runs/${threadId}/resume`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }).then(load)

  return (
    <div className="page">
      <h1 className="large-title">{run.request}</h1>
      <div className="status-line">
        <span className={`dot ${status.dot}`} />{status.text}
        <span className="muted">· {run.thread_id}</span>
      </div>

      {run.error && <div className="notice error">The run stopped with an error: {run.error}</div>}
      {run.interrupted && !run.pending && !run.error && (
        <div className="notice info">This session stopped mid-run.
          <button className="btn btn-secondary" onClick={resume}>Continue</button></div>
      )}

      <div className="segmented" role="tablist">
        {[['summary', 'Summary'], ['activity', `Activity (${run.steps.length})`], ['facts', 'Facts']].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''}
                  onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === 'summary' && <Summary run={run} />}
      {tab === 'activity' && <Activity steps={run.steps} onZoom={setZoom} />}
      {tab === 'facts' && <Facts facts={run.facts} />}

      {run.pending && <PendingCard pending={run.pending} threadId={threadId} onDone={load} />}
      {zoom && <div className="lightbox" onClick={() => setZoom(null)}><img src={`/runs/${zoom}`} alt="Screenshot" /></div>}
    </div>
  )
}
