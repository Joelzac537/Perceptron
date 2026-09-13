import { useMemo, useState } from 'react'
import { ROLE_COLOR, statusOf } from './status'

/* The loop's outcome graph, drawn as a dependency DAG.
 *
 * Layout is by dependency depth rather than by force simulation: these graphs are small
 * and hand-compiled, and a stable left-to-right reading order matters far more than
 * organic placement. Prerequisites sit left of what depends on them, so the eye travels
 * the same direction as the work.
 *
 * Plain SVG on purpose - no graph library. The whole file is ~150 lines and adds no
 * dependency to install, audit, or keep current.
 */

const NODE_W = 190
const NODE_H = 62
const GAP_X = 74
const GAP_Y = 18
const PAD = 16

/* Depth = longest path from a node with no prerequisites. Longest rather than shortest
 * so a node never renders to the left of something it depends on. */
function layout(nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const prereqs = new Map(nodes.map((n) => [n.id, []]))

  for (const edge of edges) {
    if (edge.relationship !== 'DEPENDS_ON') continue
    if (!byId.has(edge.source_node_id) || !byId.has(edge.target_node_id)) continue
    // source depends on target, so target is the prerequisite.
    prereqs.get(edge.source_node_id).push(edge.target_node_id)
  }
  // depends_on arrays are the same relation; union them so a graph that carries only one
  // representation still lays out correctly.
  for (const node of nodes) {
    for (const id of node.depends_on ?? []) {
      if (byId.has(id) && !prereqs.get(node.id).includes(id)) prereqs.get(node.id).push(id)
    }
  }

  const depth = new Map()
  const visiting = new Set()
  const depthOf = (id) => {
    if (depth.has(id)) return depth.get(id)
    if (visiting.has(id)) return 0 // defensive: a cycle should never reach the UI
    visiting.add(id)
    const parents = prereqs.get(id) ?? []
    const value = parents.length ? Math.max(...parents.map(depthOf)) + 1 : 0
    visiting.delete(id)
    depth.set(id, value)
    return value
  }
  nodes.forEach((n) => depthOf(n.id))

  const columns = new Map()
  for (const node of nodes) {
    const d = depth.get(node.id) ?? 0
    if (!columns.has(d)) columns.set(d, [])
    columns.get(d).push(node)
  }

  const placed = new Map()
  const tallest = Math.max(...[...columns.values()].map((c) => c.length), 1)
  for (const [d, column] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    const columnHeight = column.length * NODE_H + (column.length - 1) * GAP_Y
    const fullHeight = tallest * NODE_H + (tallest - 1) * GAP_Y
    const offset = (fullHeight - columnHeight) / 2
    column.forEach((node, i) => {
      placed.set(node.id, {
        node,
        x: PAD + d * (NODE_W + GAP_X),
        y: PAD + offset + i * (NODE_H + GAP_Y),
      })
    })
  }

  const width = PAD * 2 + columns.size * NODE_W + (columns.size - 1) * GAP_X
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y
  return { placed, prereqs, width, height }
}

function wrap(text, max = 26) {
  const words = String(text ?? '').split(/\s+/)
  const lines = []
  let line = ''
  for (const word of words) {
    if ((line + ' ' + word).trim().length > max) {
      if (line) lines.push(line)
      line = word
    } else {
      line = (line + ' ' + word).trim()
    }
    if (lines.length === 2) break
  }
  if (line && lines.length < 2) lines.push(line)
  const joined = lines.join(' ')
  if (joined.length < String(text ?? '').length) lines[lines.length - 1] += '…'
  return lines
}

export default function OutcomeGraph({ nodes = [], edges = [], rootId }) {
  const [hover, setHover] = useState(null)
  const { placed, prereqs, width, height } = useMemo(
    () => layout(nodes, edges),
    [nodes, edges],
  )

  if (!nodes.length) return <p className="empty">This loop has no outcome nodes yet.</p>

  const links = []
  for (const [id, parents] of prereqs.entries()) {
    for (const parentId of parents) {
      const from = placed.get(parentId)
      const to = placed.get(id)
      if (!from || !to) continue
      const x1 = from.x + NODE_W
      const y1 = from.y + NODE_H / 2
      const x2 = to.x
      const y2 = to.y + NODE_H / 2
      const mid = (x1 + x2) / 2
      links.push({
        key: `${parentId}->${id}`,
        d: `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`,
        lit: hover === id || hover === parentId,
      })
    }
  }

  return (
    <div className="graph-scroll">
      <svg width={width} height={height} role="img" aria-label="Outcome dependency graph">
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="var(--axis)" />
          </marker>
        </defs>

        {links.map((link) => (
          <path
            key={link.key}
            d={link.d}
            fill="none"
            stroke={link.lit ? 'var(--accent)' : 'var(--axis)'}
            strokeWidth={2}
            markerEnd="url(#arrow)"
            opacity={link.lit ? 1 : 0.75}
          />
        ))}

        {[...placed.values()].map(({ node, x, y }) => {
          const status = statusOf(node.status)
          const color = ROLE_COLOR[status.role]
          const isRoot = node.id === rootId
          return (
            <g
              key={node.id}
              transform={`translate(${x}, ${y})`}
              onMouseEnter={() => setHover(node.id)}
              onMouseLeave={() => setHover(null)}
              tabIndex={0}
              onFocus={() => setHover(node.id)}
              onBlur={() => setHover(null)}
            >
              <title>
                {`${node.title}\nStatus: ${status.label}${node.owner ? `\nOwner: ${node.owner}` : ''}${
                  node.deadline ? `\nDeadline: ${node.deadline}` : ''
                }`}
              </title>
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                fill="var(--raised)"
                stroke={hover === node.id ? color : 'var(--border)'}
                strokeWidth={hover === node.id ? 2 : 1}
              />
              {/* Status rail: colour plus the glyph below, never colour alone. */}
              <rect width={4} height={NODE_H} rx={2} fill={color} />
              {wrap(node.title).map((line, i) => (
                <text
                  key={i}
                  x={14}
                  y={20 + i * 14}
                  fontSize={11.5}
                  fill="var(--ink)"
                  fontWeight={isRoot ? 600 : 500}
                >
                  {line}
                </text>
              ))}
              <text x={14} y={NODE_H - 12} fontSize={10.5} fill={color} fontWeight={600}>
                {status.glyph} {status.label}
              </text>
              {node.owner ? (
                <text
                  x={NODE_W - 10}
                  y={NODE_H - 12}
                  fontSize={10.5}
                  fill="var(--muted)"
                  textAnchor="end"
                >
                  {node.owner}
                </text>
              ) : null}
              {isRoot ? (
                <text x={NODE_W - 10} y={16} fontSize={9.5} fill="var(--muted)" textAnchor="end">
                  GOAL
                </text>
              ) : null}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
