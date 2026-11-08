import type { ReactNode } from 'react';

export type IconName =
  | 'flow' | 'arrow-right' | 'plus' | 'minus' | 'fit' | 'undo' | 'redo'
  | 'check' | 'check-circle' | 'panel' | 'close' | 'upload' | 'download'
  | 'chevron-down' | 'chevron-right' | 'refresh' | 'clock' | 'file' | 'code'
  | 'spark' | 'alert' | 'more' | 'settings';

const shapes: Record<IconName, ReactNode> = {
  flow: <>
    <path d="M9 12h3V6h3M12 12v6h3" />
    <rect x="3" y="9" width="6" height="6" rx="1.4" />
    <rect x="15" y="3" width="6" height="6" rx="1.4" />
    <rect x="15" y="15" width="6" height="6" rx="1.4" />
  </>,
  'arrow-right': <path d="M4 12h16m-6-6 6 6-6 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  fit: <path d="M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5" />,
  undo: <path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 7 7v3" />,
  redo: <path d="m16 4 5 5-5 5m5-5H11a7 7 0 0 0-7 7v3" />,
  check: <path d="m5 12 4 4L19 6" />,
  'check-circle': <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
  panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  upload: <path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />,
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  'chevron-right': <path d="m9 6 6 6-6 6" />,
  refresh: <><path d="M20 9a8 8 0 0 0-14-3L3 9m0-5v5h5M4 15a8 8 0 0 0 14 3l3-3m0 5v-5h-5" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z" /><path d="M14 3v6h6M8 13h8m-8 4h5" /></>,
  code: <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16" />,
  spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" /><path d="M20 2v4m-2-2h4" /></>,
  alert: <><path d="m10.3 4-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0Z" /><path d="M12 9v5m0 3v.1" /></>,
  more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
  settings: <>
    <path d="M4 6h7m4 0h5M4 12h2m4 0h10M4 18h10m4 0h2" />
    <circle cx="13" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="16" cy="18" r="2" />
  </>,
};

type IconProps = { name: IconName; size?: number; className?: string };

export function Icon({ name, size = 18, className }: IconProps) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false" className={className}
    >
      {shapes[name]}
    </svg>
  );
}
