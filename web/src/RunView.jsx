import { useCallback, useEffect, useState } from 'react'
import Icon from './Icons.jsx'
import PendingCard from './PendingCard.jsx'

const LABELS = {
  running: 'Working', done: 'Done', stopped: 'Needs you', done_with_failures: 'Not done',
  budget_exhausted: 'Not done',
}
export const statusLabel = status => LABELS[status] || status || 'Starting'

const TOOL_LABEL = {
  goto: 'Opened a page', click: 'Clicked', fill: 'Typed', select: 'Chose an option', open_attachment: 'Read a document',
  record_fact: 'Remembered a fact', ask_human: 'Asked you', human: 'Your input', finish: 'Finished the work',
  verdict: 'Checked a criterion',
}
const BAD = new Set(['rejected', 'page_error', 'server_error', 'auth_expired', 'blocked', 'stale_ref', 'error',
  'not_found', 'verify_failed', 'denied'])
const WAIT = new Set(['needs_approval', 'asked', 'stopped'])
const STAGES = ['Understand', 'Execute', 'Verify', 'Report']

const fill = (text, facts) => text.replace(/\{(\w+)\}/g, (m, key) => (facts[key] ? facts[key].raw : m))

function duration(seconds) {
  if (seconds == null) return '–'
  const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60)
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

function Check({ kind }) {
  if (kind === 'pass') return (
    <svg className="mark pass" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path className="draw" d="M7.5 12.5l3 3 6-6.5" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>)
  if (kind === 'fail') return (
    <svg className="mark fail" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" stroke="#fff" strokeWidth="2" strokeLinecap="round" /></svg>)
  if (kind === 'warn') return (
    <svg className="mark warn" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor" />
      <path d="M12 7v6" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" /><circle cx="12" cy="16.5" r="1.3" fill="#fff" /></svg>)
  if (kind === 'spin') return <span className="spinner" />
  return <svg className="mark pending" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" fill="none" /></svg>
}

function stageOf(run) {
  if (run.report) return 4
  if (!run.spec) return 0
  return run.mode === 'verify' ? 2 : 1
}

function StageTrack({ run }) {
  const at = stageOf(run)
  return (
    <div className="stages">
      {STAGES.map((name, i) => {
        const state = i < at ? 'done' : i === at ? (run.pending ? 'waiting' : 'current') : 'todo'
        return (
          <div key={name} className={`stage ${state}`}>
            <div className="stage-dot">{state === 'done' ? <Icon name="verdict" size={13} /> : i + 1}</div>
            <div className="stage-name">{name}</div>
          </div>
        )
      })}
      <div className="stage-line"><div className="stage-fill" style={{ width: `${Math.min(at, 3) / 3 * 100}%` }} /></div>
    </div>
  )
}

function useClock(active) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

function stats(run, now) {
  const timed = run.steps.filter(s => s.at)
  const first = timed[0]?.at
  const last = run.active || run.pending ? now : timed[timed.length - 1]?.at
  const tokens = (run.counters?.tokens_in || 0) + (run.counters?.tokens_out || 0)
  return {
    steps: run.steps.filter(s => s.tool !== 'human').length,
    elapsed: first && last ? last - first : null,
    tokens,
    approvals: run.steps.filter(s => s.tool === 'human' && s.outcome === 'approved').length,
    recovered: run.steps.filter(s => BAD.has(s.outcome)).length,
  }
}

function StatRow({ s }) {
  return (
    <div className="stat-row">
      <div className="stat"><div className="stat-value">{s.steps}</div><div className="stat-label">actions</div></div>
      <div className="stat"><div className="stat-value">{duration(s.elapsed)}</div><div className="stat-label">elapsed</div></div>
      <div className="stat"><div className="stat-value">{s.tokens ? `${Math.round(s.tokens / 1000)}k` : '–'}</div><div className="stat-label">tokens</div></div>
      <div className="stat"><div className="stat-value">{s.recovered}</div><div className="stat-label">problems handled</div></div>
    </div>
  )
}

function Criteria({ items, live }) {
  return (
    <div className="group">
      {items.map(c => (
        <div className={`group-row crit ${c.status}`} key={c.id}>
          <Check kind={c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : live ? 'spin' : 'pending'} />
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

function BrowserFrame({ shot, url, onZoom, live }) {
  return (
    <div className="window">
      <div className="window-bar">
        <span className="light r" /><span className="light y" /><span className="light g" />
        <div className="address">{live && <span className="rec" />}{url || 'acme portal'}</div>
      </div>
      <div className="window-body">
        {shot ? <img key={shot} className="shot" src={`/runs/${shot}`} alt="What the worker sees" onClick={() => onZoom(shot)} />
          : <div className="window-empty"><span className="spinner" /> Opening the browser…</div>}
      </div>
    </div>
  )
}

function Live({ run, onZoom, s }) {
  const steps = run.steps
  const last = [...steps].reverse().find(x => x.tool !== 'human') || null
  const shot = [...steps].reverse().find(x => x.screenshot)?.screenshot
  const facts = run.facts
  const criteria = (run.spec?.criteria || []).map(c => ({ ...c, text: fill(c.text, facts), ...(run.verdicts[c.id] || { status: 'not checked' }) }))
  return (
    <>
      <div className="live">
        <BrowserFrame shot={shot} url={last?.url} onZoom={onZoom} live={run.active} />
        <div className="now">
          <div className="now-label">{run.pending ? 'Waiting for you' : run.mode === 'verify' ? 'Verifying' : 'Right now'}</div>
          {last ? (
            <div className="now-step" key={last.n}>
              <div className="now-icon"><Icon name={last.tool} size={22} /></div>
              <div>
                <div className="now-title">{TOOL_LABEL[last.tool] || last.tool}</div>
                <div className="now-why">{last.why || last.message || '…'}</div>
              </div>
            </div>
          ) : <div className="now-why">Reading the request and the company playbook…</div>}
          <StatRow s={s} />
        </div>
      </div>
      {run.spec && (
        <>
          <div className="group-title">{run.mode === 'verify' ? 'Being verified now' : 'Done means'}</div>
          <Criteria items={criteria} live={run.mode === 'verify' && run.active} />
          <div className="group-title">The plan it made</div>
          <div className="group">
            {run.spec.plan.map((p, i) => (
              <div className="group-row plain" key={i}><span className="step-n">{i + 1}</span><div className="row-body">{p}</div></div>
            ))}
          </div>
        </>
      )}
    </>
  )
}

function Result({ run, onZoom, s }) {
  const { report } = run
  const good = report.status === 'done'
  const kind = good ? 'pass' : report.status === 'stopped' ? 'warn' : 'fail'
  const gallery = report.criteria.filter(c => c.screenshot)
  return (
    <>
      <div className={`result ${kind}`}>
        <div className="result-mark"><Check kind={kind} /></div>
        <div className="result-title">{report.headline}</div>
        <div className="result-sub">
          {good ? 'Every success criterion was independently verified against the live systems.'
            : report.stopped_because ? report.stopped_because : 'Some criteria were not met. Details below.'}
        </div>
        <StatRow s={s} />
      </div>

      {report.answer.length > 0 && (
        <>
          <div className="group-title">Answer</div>
          <div className="group">
            {report.answer.map(a => (
              <div className="group-row plain" key={a.key}><div className="row-body">
                <div className="answer">{a.raw}</div>
                <div className="quote">“<span>{a.quote}</span>”</div>
              </div></div>
            ))}
          </div>
        </>
      )}

      <div className="group-title">Independently verified</div>
      <Criteria items={report.criteria} />

      {gallery.length > 0 && (
        <>
          <div className="group-title">Evidence</div>
          <div className="gallery">
            {gallery.map(c => (
              <figure key={c.id} onClick={() => onZoom(c.screenshot)}>
                <img src={`/runs/${c.screenshot}`} alt={`Evidence for ${c.id}`} />
                <figcaption><Check kind={c.status === 'pass' ? 'pass' : 'fail'} /> {c.id} · {c.url}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}

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
          <div className="group-row plain act" key={s.n}>
            <div className={`act-icon ${s.mode} ${BAD.has(s.outcome) ? 'bad' : WAIT.has(s.outcome) ? 'wait' : ''}`}>
              <Icon name={s.tool} size={16} />
            </div>
            <div className="row-body">
              <div className="step-title">
                <span className="step-tool">{TOOL_LABEL[s.tool] || s.tool}</span>
                <span className={`step-outcome ${BAD.has(s.outcome) ? 'bad' : WAIT.has(s.outcome) ? 'wait' : ''}`}>
                  {s.outcome.replaceAll('_', ' ')}
                </span>
                {s.mode === 'verify' && <span className="step-mode">verify</span>}
                <span className="step-n">#{s.n}</span>
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
      <div className="facts-grid">
        {entries.map(([key, f]) => (
          <div className="fact-card" key={key}>
            <div className="fact-key">{key.replaceAll('_', ' ')}</div>
            <div className="fact-value">{f.raw}</div>
            <div className="quote">“<span>{f.quote}</span>”</div>
            <div className="row-tertiary">from {f.source}</div>
          </div>
        ))}
      </div>
      <p className="footnote">Every fact was checked against its quoted source before the worker was allowed to use it.</p>
    </>
  )
}

export default function RunView({ threadId }) {
  const [run, setRun] = useState(null)
  const [tab, setTab] = useState('overview')
  const [zoom, setZoom] = useState(null)

  const load = useCallback(() => {
    fetch(`/api/runs/${threadId}`).then(r => (r.ok ? r.json() : null)).then(data => data && setRun(data)).catch(() => {})
  }, [threadId])

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [load])

  const now = useClock(Boolean(run && (run.active || run.pending)))
  if (!run) return <div className="empty"><span className="spinner" /></div>

  const s = stats(run, now)
  const statusDot = run.pending ? 'waiting' : run.active ? 'running' : run.status
  const statusText = run.pending ? 'Waiting for you' : run.active ? (run.mode === 'verify' ? 'Verifying its work' : 'Working') : statusLabel(run.status)
  const resume = () => fetch(`/api/runs/${threadId}/resume`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }).then(load)

  return (
    <div className="page">
      <h1 className="large-title">{run.request}</h1>
      <div className="status-line">
        <span className={`dot ${statusDot}`} />{statusText}
        <span className="muted">· {run.thread_id}</span>
      </div>

      <StageTrack run={run} />

      {run.error && <div className="notice error">The run stopped with an error: {run.error}</div>}
      {run.interrupted && !run.pending && !run.error && (
        <div className="notice info">This session stopped mid-run.
          <button className="btn btn-secondary" onClick={resume}>Continue</button></div>
      )}

      <div className="segmented" role="tablist">
        {[['overview', 'Overview'], ['activity', `Activity (${run.steps.length})`], ['facts', `Facts (${Object.keys(run.facts).length})`]].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''}
                  onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {tab === 'overview' && (run.report ? <Result run={run} onZoom={setZoom} s={s} /> : <Live run={run} onZoom={setZoom} s={s} />)}
      {tab === 'activity' && <Activity steps={run.steps} onZoom={setZoom} />}
      {tab === 'facts' && <Facts facts={run.facts} />}

      {run.pending && <PendingCard pending={run.pending} threadId={threadId} onDone={load} />}
      {zoom && <div className="lightbox" onClick={() => setZoom(null)}><img src={`/runs/${zoom}`} alt="Screenshot" /></div>}
    </div>
  )
}
