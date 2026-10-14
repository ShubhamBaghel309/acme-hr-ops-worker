// Small line icons in the spirit of SF Symbols. One per tool, plus a few UI glyphs.
const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' }

const PATHS = {
  goto: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.5 2.6 3.6 5.4 3.6 8.5s-1.1 5.9-3.6 8.5c-2.5-2.6-3.6-5.4-3.6-8.5s1.1-5.9 3.6-8.5z" /></>,
  click: <><path d="M9 4.5v2M4.5 9h2M5.8 5.8l1.4 1.4" /><path d="M10 10l9 3.6-3.8 1.6-1.6 3.8z" /></>,
  fill: <><rect x="3" y="7" width="18" height="10" rx="2.5" /><path d="M7 12h.01M10.5 12h.01M14 12h3" /></>,
  select: <><rect x="3" y="6" width="18" height="12" rx="2.5" /><path d="M14 11l2 2 2-2" /><path d="M7 12h4" /></>,
  open_attachment: <><path d="M14 3.5H7.5A2 2 0 0 0 5.5 5.5v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z" /><path d="M14 3.5V8h4.5M9 12.5h6M9 16h4" /></>,
  record_fact: <><path d="M12 3.5l2.4 4.9 5.3.8-3.8 3.7.9 5.3L12 15.7l-4.8 2.5.9-5.3-3.8-3.7 5.3-.8z" /></>,
  ask_human: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  human: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  finish: <><path d="M5 20V4.5M5 5h11l-2 3.5 2 3.5H5" /></>,
  verdict: <><path d="M12 3.5l7 2.6v5.4c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6.1z" /><path d="M8.8 12.2l2.2 2.2 4.4-4.6" /></>,
  sparkle: <><path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z" /><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  bolt: <><path d="M13 3.5L5.5 13.5H12l-1 7 7.5-10H12z" /></>,
  steps: <><path d="M5 7h14M5 12h14M5 17h9" /></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
}

export default function Icon({ name, size = 18, className = '' }) {
  return (
    <svg className={`ico ${className}`} width={size} height={size} viewBox="0 0 24 24" {...S} aria-hidden="true">
      {PATHS[name] || PATHS.click}
    </svg>
  )
}
