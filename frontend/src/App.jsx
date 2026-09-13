import { useEffect, useMemo, useState } from 'react'
import { API, injectEvent, usePoll } from './api'
import OutcomeGraph from './OutcomeGraph'
import { APP_LABEL, ROLE_COLOR, relationshipOf, statusOf } from './status'
import './App.css'

const POLL_MS = 3000

function Pill({ role, glyph, label, title }) {
  return (
    <span className="pill" style={{ color: ROLE_COLOR[role] }} title={title}>
      <span aria-hidden="true">{glyph}</span>
      {label}
    </span>
  )
}

/* Confidence as a thin bar plus its own number.
 *
 * Direct-labelled rather than relying on the bar: these appear one at a time beside prose,
 * so there is no shared scale to compare against and the number is what a reader needs.
 */
function Confidence({ value }) {
  const pct = Math.round((Number(value) || 0) * 100)
  return (
    <span className="confidence" title={`Confidence ${pct}%`}>
      <span className="confidence-track">
        <span className="confidence-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular">{pct}%</span>
    </span>
  )
}

function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <div className="stat-value tabular">{value}</div>
      <div className="stat-label">{label}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  )
}

function Connections({ data }) {
  const entries = Object.entries(data?.connections ?? {})
  if (!entries.length) return <span className="muted">no apps</span>
  return (
    <div className="connections">
      {entries.map(([slug, info]) => {
        const ok = info?.status === 'ACTIVE'
        return (
          <span
            key={slug}
            className="conn"
            style={{ color: ok ? ROLE_COLOR.good : ROLE_COLOR.muted }}
            title={`${slug}: ${info?.status ?? 'unknown'}`}
          >
            <span aria-hidden="true">{ok ? '●' : '○'}</span>
            {APP_LABEL[slug] ?? slug}
          </span>
        )
      })}
    </div>
  )
}

function Outcome({ result }) {
  if (result.error || result.error_code) {
    return <Pill role="critical" glyph="✕" label={result.error_code ?? 'Pipeline error'} title={result.error} />
  }
  if (result.duplicate) return <Pill role="muted" glyph="⤳" label="Duplicate, skipped" />
  if (result.matches?.length) {
    return (
      <span className="outcome-row">
        <Pill role="good" glyph="→" label={result.matches.length > 1 ? 'Matched loops' : 'Matched loop'} />
        <Confidence value={result.matches[0].confidence} />
      </span>
    )
  }
  if (result.created_loop) return <Pill role="active" glyph="✦" label="New goal created" />
  return <Pill role="muted" glyph="·" label="Not relevant" />
}

function EventFeed({ results, selected, onSelect }) {
  if (!results.length) {
    return (
      <p className="empty">
        Nothing yet. Send yourself an email, or use the sample below — the poller checks
        every 10 seconds.
      </p>
    )
  }
  return (
    <ul className="feed">
      {results.map((result) => {
        const loopId = result.matches?.[0]?.loop_id ?? result.created_loop
        const active = loopId && loopId === selected
        return (
          <li key={result.event_id}>
            <button
              className={`feed-item${active ? ' is-active' : ''}`}
              onClick={() => loopId && onSelect(loopId)}
              disabled={!loopId}
            >
              <div className="feed-head">
                <span className="source">{APP_LABEL[result.source_app] ?? result.source_app}</span>
                {result.seconds != null ? (
                  <span className="muted tabular">{result.seconds}s</span>
                ) : null}
              </div>
              <div className="feed-subject">{result.subject || '(no subject)'}</div>
              <Outcome result={result} />
              {result.matches?.[0]?.reason ? (
                <div className="feed-reason">{result.matches[0].reason}</div>
              ) : null}
              {result.errors?.length ? (
                <div className="feed-warn">⚠ {result.errors[0]}</div>
              ) : null}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function LoopDetail({ loopId }) {
  const { data, loading } = usePoll(loopId ? `/loops/${loopId}` : null, POLL_MS, !!loopId)

  if (!loopId) {
    return (
      <div className="panel detail">
        <p className="empty">Select an event or a goal to see its outcome graph.</p>
      </div>
    )
  }
  if (loading && !data) return <div className="panel detail"><p className="empty">Loading…</p></div>
  if (!data?.found) return <div className="panel detail"><p className="empty">Goal not found.</p></div>

  const loop = data.loop ?? {}
  const status = statusOf(loop.status)
  const requirements = data.requirements ?? []
  const evidence = data.evidence ?? []

  return (
    <div className="panel detail">
      <header className="detail-head">
        <div>
          <h2>{loop.title}</h2>
          <p className="goal">{loop.goal}</p>
        </div>
        <Pill role={status.role} glyph={status.glyph} label={status.label} />
      </header>

      <section>
        <h3>Outcome graph</h3>
        <OutcomeGraph nodes={data.nodes ?? []} edges={data.edges ?? []} rootId={loop.root_node_id} />
      </section>

      {evidence.length ? (
        <section>
          <h3>Evidence assessed</h3>
          <ul className="rows">
            {evidence.map((item) => {
              const rel = relationshipOf(item.relationship)
              return (
                <li key={item.id} className="row">
                  <Pill role={rel.role} glyph={rel.glyph} label={rel.label} />
                  <span className="row-main">{item.reason}</span>
                  <Confidence value={item.confidence} />
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}

      {requirements.length ? (
        <section>
          <h3>What would prove it</h3>
          <ul className="rows">
            {requirements.map((req) => (
              <li key={req.id} className="row">
                <span className="tag">{req.type}</span>
                <span className="row-main">{req.description}</span>
                <span className="muted">{(req.source_apps ?? []).join(', ')}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

const SAMPLE = {
  source_app: 'GMAIL',
  event_type: 'MESSAGE_RECEIVED',
  payload: {
    messageId: `ui_demo_${Date.now()}`,
    threadId: `thread_ui_${Date.now()}`,
    sender: 'landlord@brookfieldproperties.example',
    subject: 'Signed lease renewal required by October 3',
    messageText:
      'Hi Alex, your lease renewal for Unit 4B expires soon. Please sign and return the renewal agreement by October 3, 2026. If we do not receive the signed document by that date the unit will be listed as available.',
    labelIds: ['INBOX'],
    messageTimestamp: new Date().toISOString(),
  },
}

export default function App() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme ?? 'light',
  )
  const [selected, setSelected] = useState(null)
  const [sending, setSending] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const health = usePoll('/health', POLL_MS)
  const connections = usePoll('/connections', 15000)
  const pipeline = usePoll('/pipeline?limit=25', POLL_MS)
  const loops = usePoll('/loops', POLL_MS)

  // Memoised so the identity is stable across renders; a fresh [] each time would
  // invalidate the counts useMemo on every poll tick.
  const results = useMemo(() => pipeline.data?.results ?? [], [pipeline.data])
  const routable = useMemo(() => loops.data?.routable ?? [], [loops.data])
  const offline = health.error != null

  const counts = useMemo(() => {
    const done = results.filter((r) => !r.duplicate && !r.error && !r.error_code)
    return {
      processed: done.length,
      matched: done.filter((r) => r.matches?.length).length,
      created: done.filter((r) => r.created_loop).length,
    }
  }, [results])

  async function sendSample() {
    setSending(true)
    try {
      await injectEvent({
        ...SAMPLE,
        payload: { ...SAMPLE.payload, messageId: `ui_demo_${Date.now()}` },
      })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden="true">◎</span>
          <div>
            <strong>LoopGraph</strong>
            <span className="muted"> — obligations that track themselves</span>
          </div>
        </div>

        <Connections data={connections.data} />

        <div className="topbar-right">
          <span
            className="live"
            style={{ color: offline ? ROLE_COLOR.critical : ROLE_COLOR.good }}
          >
            <span aria-hidden="true">{offline ? '✕' : '●'}</span>
            {offline ? 'Backend offline' : health.data?.pipeline_ready ? 'Live' : 'No API key'}
          </span>
          <button className="ghost" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? '☀ Light' : '☾ Dark'}
          </button>
        </div>
      </header>

      {offline ? (
        <div className="banner">
          Cannot reach the backend at <code>{API}</code>. Start it with{' '}
          <code>uvicorn app.main:app --app-dir backend --port 8000</code>.
        </div>
      ) : null}

      <div className="stats">
        <Stat label="Events processed" value={counts.processed} />
        <Stat label="Matched to a goal" value={counts.matched} />
        <Stat label="New goals created" value={counts.created} />
        <Stat
          label="Goals tracked"
          value={routable.length}
          hint={health.data?.loops_tracked != null ? `${health.data.loops_tracked} stored` : null}
        />
      </div>

      <main className="grid">
        <div className="column">
          <div className="panel">
            <header className="panel-head">
              <h2>Live event feed</h2>
              <button className="ghost" onClick={sendSample} disabled={sending || offline}>
                {sending ? 'Sending…' : '+ Sample event'}
              </button>
            </header>
            <EventFeed results={results} selected={selected} onSelect={setSelected} />
          </div>

          <div className="panel">
            <header className="panel-head">
              <h2>Goals being tracked</h2>
            </header>
            {routable.length ? (
              <ul className="loops">
                {routable.map((loop) => {
                  const status = statusOf(loop.status)
                  return (
                    <li key={loop.loop_id}>
                      <button
                        className={`loop-item${loop.loop_id === selected ? ' is-active' : ''}`}
                        onClick={() => setSelected(loop.loop_id)}
                      >
                        <span className="loop-title">{loop.title}</span>
                        <Pill role={status.role} glyph={status.glyph} label={status.label} />
                        <span className="muted">
                          {loop.open_nodes?.length ?? 0} open
                          {loop.people?.length ? ` · ${loop.people.join(', ')}` : ''}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="empty">No goals yet. One will appear when an event creates an obligation.</p>
            )}
          </div>
        </div>

        <LoopDetail loopId={selected} />
      </main>
    </div>
  )
}
