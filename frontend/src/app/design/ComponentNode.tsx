type NodeStatus = 'pending' | 'searching' | 'pass' | 'conflict'

type ConflictVariant = 'entry' | 'repeat' | 'warmup'

type ComponentNodeProps = {
  x: number
  y: number
  width: number
  height: number
  title: string
  mpn?: string
  subtitle?: string
  status: NodeStatus
  conflictVariant?: ConflictVariant
  stockBadge?: string | null
  categoryEmphasis?: boolean
  reveal?: boolean
}

function truncate(text: string, max: number) {
  if (text.length <= max) {
    return text
  }

  return `${text.slice(0, Math.max(0, max - 1))}…`
}

function nodeClassName(status: NodeStatus, conflictVariant?: ConflictVariant) {
  if (status === 'conflict') {
    if (conflictVariant === 'repeat') {
      return 'node node-conflict node-conflict-repeat'
    }

    if (conflictVariant === 'warmup') {
      return 'node node-conflict'
    }

    return 'node node-conflict node-conflict-entry'
  }

  if (status === 'pass') {
    return 'node node-pass'
  }

  if (status === 'searching') {
    return 'node node-searching'
  }

  return 'node node-pending'
}

function statusDot(status: NodeStatus) {
  if (status === 'conflict') {
    return { fill: '#ffb4ab', className: 'fill-error' }
  }

  if (status === 'pass') {
    return { fill: '#4ade80', className: '' }
  }

  if (status === 'searching') {
    // Cyan is a *status* here, not the brand. It used to be both, which is why a searching
    // node and a primary button were the same colour. Now that the brand is copper, cyan
    // means one thing — work in progress — and it sits 180° from conflict red and 45° from
    // valid green, so it cannot be mistaken for either.
    return { fill: '#00E5FF', className: '' }
  }

  return { fill: '#3F444E', className: 'fill-outline-variant' }
}

function stockBadgeWidth(label: string) {
  return Math.max(46, Math.min(78, label.length * 5 + 14))
}

export function ComponentNode({
  x,
  y,
  width,
  height,
  title,
  mpn,
  subtitle,
  status,
  conflictVariant,
  stockBadge,
  categoryEmphasis,
  reveal = false,
}: ComponentNodeProps) {
  const dot = statusDot(status)
  const showMpn = Boolean(mpn) && (status === 'pass' || status === 'conflict')
  const badge = stockBadge ? truncate(stockBadge, 14) : null
  const badgeWidth = badge ? stockBadgeWidth(badge) : 0
  const subtitleY = showMpn ? 44 : 30

  return (
    <g className={reveal ? 'graph-node-reveal' : undefined}>
      <g className="cursor-pointer group" transform={`translate(${x}, ${y})`}>
        <rect className={nodeClassName(status, conflictVariant)} height={height} rx="6" width={width}></rect>

        <text className="node-text-title" x="10" y="14">
          {truncate(title, 18)}
        </text>

        {showMpn ? (
          <text className="node-text-mpn" x="10" y="30">
            {truncate(mpn ?? '', 20)}
          </text>
        ) : null}

        {subtitle ? (
          <text
            className={
              categoryEmphasis ? 'font-data-tabular text-[9px] fill-tertiary-container font-semibold' : 'font-data-tabular text-[9px] fill-on-surface-variant'
            }
            x="10"
            y={subtitleY}
          >
            {truncate(subtitle, showMpn ? 22 : 24)}
          </text>
        ) : null}

        {badge ? (
          <g transform={`translate(${width - badgeWidth - 8}, ${height - 16})`}>
            <rect className="badge-error-bg" height="12" rx="3" width={badgeWidth}></rect>
            <text className="badge-error-text" dominantBaseline="middle" textAnchor="middle" x={badgeWidth / 2} y="6.5">
              {badge}
            </text>
          </g>
        ) : null}

        <circle className={dot.className} cx={width - 10} cy="11" fill={dot.fill} r="3"></circle>
      </g>
    </g>
  )
}
