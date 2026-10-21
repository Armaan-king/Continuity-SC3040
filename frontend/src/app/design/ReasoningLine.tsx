type ReasoningLineProps = {
  icon: string
  iconClassName: string
  text: string
  detail?: string
  spinner?: boolean
}

export function ReasoningLine({
  icon,
  iconClassName,
  text,
  detail,
  spinner = false,
}: ReasoningLineProps) {
  return (
    <div className="flex items-start gap-sm px-sm py-1.5 hover:bg-surface-variant/50 rounded transition-colors group cursor-default">
      <span
        className={`material-symbols-outlined text-[14px] mt-[2px] ${iconClassName}${spinner ? ' spinner' : ''}`}
        style={{ fontVariationSettings: "'FILL' 1" }}
      >
        {icon}
      </span>
      <div className="flex flex-col">
        <span className="font-data-tabular text-[12px] text-on-surface">{text}</span>
        {detail ? (
          <span className="font-data-tabular text-[10px] text-on-surface-variant opacity-50 group-hover:opacity-100 transition-opacity">
            {detail}
          </span>
        ) : null}
      </div>
    </div>
  )
}
