type GraphEdgeProps = {
  pathClassName: string
  d: string
  badgeRectClassName?: string
  badgeRectX?: number
  badgeRectY?: number
  badgeRectWidth?: number
  badgeRectHeight?: number
  badgeTextClassName?: string
  badgeTextX?: number
  badgeTextY?: number
  badgeText?: string
  subLabelText?: string
  subLabelX?: number
  subLabelY?: number
  subLabelClassName?: string
}

export function GraphEdge({
  pathClassName,
  d,
  badgeRectClassName,
  badgeRectX,
  badgeRectY,
  badgeRectWidth,
  badgeRectHeight,
  badgeTextClassName,
  badgeTextX,
  badgeTextY,
  badgeText,
  subLabelText,
  subLabelX,
  subLabelY,
  subLabelClassName,
}: GraphEdgeProps) {
  return (
    <>
      <path className={pathClassName} d={d}></path>
      {badgeRectClassName ? (
        <rect
          className={badgeRectClassName}
          height={badgeRectHeight}
          width={badgeRectWidth}
          x={badgeRectX}
          y={badgeRectY}
        ></rect>
      ) : null}
      {badgeText ? (
        <text
          className={badgeTextClassName}
          dominantBaseline="middle"
          textAnchor="middle"
          x={badgeTextX}
          y={badgeTextY}
        >
          {badgeText}
        </text>
      ) : null}
      {subLabelText ? (
        <text className={subLabelClassName} textAnchor="middle" x={subLabelX} y={subLabelY}>
          {subLabelText}
        </text>
      ) : null}
    </>
  )
}
