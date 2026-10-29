import type { SVGProps } from 'react';

const P: Record<string, string> = {
  logo: 'M5 12a7 7 0 0 1 12-5m2 5a7 7 0 0 1-12 5M17 4v3h-3M7 20v-3h3',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z',
  shield: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
  alert: 'M12 4l9 16H3zM12 10v4M12 17.5v.01',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8v.01',
  check: 'M5 13l4 4L19 7',
  x: 'M6 6l12 12M18 6L6 18',
  plus: 'M12 5v14M5 12h14',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',
  moon: 'M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z',
  auto: 'M12 3v18M12 3a9 9 0 0 1 0 18',
  logout: 'M10 5H5v14h5M15 8l4 4-4 4M19 12H9',
  bolt: 'M13 3L5 14h6l-1 7 8-11h-6z',
  tool: 'M14 6a4 4 0 0 0-5 5L4 16l4 4 5-5a4 4 0 0 0 5-5l-3 2-2-2 2-3z',
  inbox: 'M3 13l3-8h12l3 8v6H3zM3 13h5l1 2h6l1-2h5',
  folder: 'M3 6h6l2 2h10v11H3z',
  chevron: 'M9 6l6 6-6 6',
  play: 'M7 5l12 7-12 7z',
  stop: 'M6 6h12v12H6z',
  refresh: 'M20 11A8 8 0 0 0 5 8M4 13a8 8 0 0 0 15 3M20 4v7h-7M4 20v-7h7',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  save: 'M5 4h12l3 3v13H5zM8 4v5h8V4M8 20v-6h8v6',
  download: 'M12 4v11M7 11l5 5 5-5M5 20h14',
  upload: 'M12 16V5M7 9l5-5 5 5M5 20h14',
  trash: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
  key: 'M14 10a4 4 0 1 0-3.9 4H11l2 2h2v2h2v2h3v-3l-6-6.2',
  doc: 'M7 3h8l4 4v14H7zM14 3v5h5',
};

export function Icon({ name, ...rest }: { name: keyof typeof P | string } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      <path d={P[name] ?? P.info} />
    </svg>
  );
}
