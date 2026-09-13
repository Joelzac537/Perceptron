/* Status vocabulary, shared by every view.
 *
 * Each status carries a glyph and a label alongside its colour, because a status colour
 * must never be the only thing distinguishing two states: warning and serious sit below
 * 3:1 on the light surface by design, and some readers cannot separate the hues at all.
 */

const STATUS = {
  // Settled, successfully.
  VERIFIED: { role: 'good', glyph: '✓', label: 'Verified' },
  COMPLETED: { role: 'good', glyph: '✓', label: 'Completed' },

  // In flight. Uses the series hue rather than a status role: "working" is not a
  // warning, and the status palette has no informational slot.
  ACTIVE: { role: 'active', glyph: '▶', label: 'Active' },

  // Waiting on something outside our control.
  WAITING: { role: 'warning', glyph: '◷', label: 'Waiting' },
  PENDING: { role: 'warning', glyph: '◷', label: 'Pending' },

  // Cannot proceed until a prerequisite resolves.
  BLOCKED: { role: 'serious', glyph: '⊘', label: 'Blocked' },

  // Settled, unsuccessfully.
  FAILED: { role: 'critical', glyph: '✕', label: 'Failed' },
  CANCELLED: { role: 'critical', glyph: '✕', label: 'Cancelled' },

  // Replaced by a newer node; kept as history.
  SUPERSEDED: { role: 'muted', glyph: '⤳', label: 'Superseded' },
}

export const ROLE_COLOR = {
  good: 'var(--good)',
  warning: 'var(--warning)',
  serious: 'var(--serious)',
  critical: 'var(--critical)',
  active: 'var(--accent)',
  muted: 'var(--muted)',
}

export function statusOf(value) {
  return STATUS[value] ?? { role: 'muted', glyph: '·', label: value ?? 'Unknown' }
}

/* Evidence relationships are a different vocabulary from node status: they describe what
 * an event said about a node, not what state the node is in. */
const RELATIONSHIP = {
  PROVES: { role: 'good', glyph: '✓', label: 'Proves' },
  PARTIALLY_SUPPORTS: { role: 'active', glyph: '◐', label: 'Partially supports' },
  INSUFFICIENT: { role: 'warning', glyph: '◷', label: 'Insufficient' },
  SUPERSEDES: { role: 'serious', glyph: '⤳', label: 'Supersedes' },
  CONTRADICTS: { role: 'critical', glyph: '✕', label: 'Contradicts' },
  UNRELATED: { role: 'muted', glyph: '·', label: 'Unrelated' },
}

export function relationshipOf(value) {
  const key = String(value ?? '').replace(/^EvidenceRelationship\./, '')
  return RELATIONSHIP[key] ?? { role: 'muted', glyph: '·', label: key || 'Unknown' }
}

export const APP_LABEL = {
  GMAIL: 'Gmail',
  SLACK: 'Slack',
  GOOGLEDRIVE: 'Drive',
  GOOGLECALENDAR: 'Calendar',
  gmail: 'Gmail',
  slack: 'Slack',
  google_drive: 'Drive',
  drive: 'Drive',
  calendar: 'Calendar',
  loopgraph: 'LoopGraph',
}
