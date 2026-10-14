import { useState } from 'react'

// What the worker is waiting for, shown as a sheet: approve an exact request, or answer a question.
export default function PendingCard({ pending, threadId, onDone }) {
  const [note, setNote] = useState('')
  const [answer, setAnswer] = useState('')
  const [sending, setSending] = useState(false)

  async function send(body) {
    setSending(true)
    await fetch(`/api/runs/${threadId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    setSending(false)
    onDone()
  }

  return (
    <div className="scrim">
      <div className="sheet" role="dialog" aria-modal="true">
        <div className="grabber" />
        {pending.kind === 'approval' ? (
          <>
            <h2 className="sheet-title">{pending.description}</h2>
            <p className="sheet-subtitle">
              The worker is holding this exact request. Approving allows only what you see here.
            </p>
            <div className="group">
              {Object.entries(pending.payload).map(([key, value]) => (
                <div className="group-row plain" key={key}>
                  <div className="row-body">
                    <div className="fact-key">{key}</div>
                    <pre>{value}</pre>
                  </div>
                </div>
              ))}
            </div>
            <input className="input" placeholder="Add a note (optional)" value={note}
                   onChange={e => setNote(e.target.value)} />
            <div className="sheet-actions">
              <button className="btn btn-destructive btn-large" disabled={sending}
                      onClick={() => send({ decision: 'deny', note })}>Deny</button>
              <button className="btn btn-primary btn-large" disabled={sending}
                      onClick={() => send({ decision: 'approve', note })}>Approve</button>
            </div>
          </>
        ) : (
          <>
            <h2 className="sheet-title">The worker has a question</h2>
            <p className="sheet-subtitle">It paused rather than guess.</p>
            <div className="group"><div className="group-row plain"><pre>{pending.question}</pre></div></div>
            <textarea className="input" rows={3} value={answer} onChange={e => setAnswer(e.target.value)}
                      placeholder="Your answer" autoFocus />
            <div className="sheet-actions">
              <button className="btn btn-primary btn-large" disabled={sending || !answer.trim()}
                      onClick={() => send({ answer })}>Send Answer</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
