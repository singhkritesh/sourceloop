import { useMemo, useState } from 'react';
import type { ProjectMessage } from '../api/types';
import { buildLadder, type LadderPoint } from '../lib/ladder';

const W = 680, H = 270, M = { l: 54, r: 18, t: 14, b: 38 };
const color = (i: number) => (i < 8 ? `var(--series-${i + 1})` : 'var(--text-3)');

export function Ladder({ messages, names }: { messages: ProjectMessage[]; names?: Record<string, string> }) {
  const ladder = useMemo(() => buildLadder(messages), [messages]);
  const nm = (id: string) => names?.[id] ?? id;
  const [hover, setHover] = useState<{ sid: string; p: LadderPoint } | null>(null);
  const [table, setTable] = useState(false);
  if (!ladder.series.length) return <p className="muted" style={{ padding: '8px 0' }}>No offers yet. Prices appear once suppliers quote in the sourcing stage.</p>;

  const pad = Math.max((ladder.maxPrice - ladder.minPrice) * 0.12, ladder.maxPrice * 0.01, 0.5);
  const y0 = ladder.minPrice - pad, y1 = ladder.maxPrice + pad;
  const xmax = Math.max(ladder.maxRound + 0.6, 2.2);
  const sx = (x: number) => M.l + ((x - 0.7) / (xmax - 0.7)) * (W - M.l - M.r);
  const sy = (p: number) => M.t + (1 - (p - y0) / (y1 - y0)) * (H - M.t - M.b);
  const ticks = Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4);
  const rounds = Array.from({ length: ladder.maxRound }, (_, i) => i + 1);
  const direct = ladder.series.length <= 4;

  return (
    <div>
      <div className="row wrap" style={{ marginBottom: 6 }}>
        <div className="legend grow" aria-label="Legend">
          {ladder.series.map((s, i) => (
            <span key={s.supplierId}><i style={{ background: color(i) }} />{nm(s.supplierId)}{s.accepted ? ' (accepted)' : ''}</span>
          ))}
          <span><svg width="14" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="var(--text-2)" /></svg> supplier</span>
          <span><svg width="14" height="10" aria-hidden="true"><rect x="1" y="1" width="8" height="8" fill="var(--surface)" stroke="var(--text-2)" strokeWidth="2" /></svg> buyer counter</span>
        </div>
        <button className="btn sm" onClick={() => setTable((t) => !t)} aria-pressed={table}>{table ? 'Chart' : 'Table'}</button>
      </div>
      {table ? (
        <table className="table num">
          <thead><tr><th>Supplier</th><th>Round</th><th>Side</th><th>Unit price</th><th>Lead (wk)</th><th /></tr></thead>
          <tbody>
            {ladder.series.flatMap((s) => s.points.map((p) => (
              <tr key={p.msgId}><td>{nm(s.supplierId)}</td><td>{p.round}</td><td>{p.side}</td><td>{p.priceText}</td><td>{p.leadTimeWeeks ?? ''}</td><td>{p.accepted ? 'accepted' : ''}</td></tr>
            )))}
          </tbody>
        </table>
      ) : (
        <>
          <div className="hint num" style={{ minHeight: 18 }} aria-live="polite">
            {hover ? `${nm(hover.sid)} · round ${hover.p.round} · ${hover.p.side === 'buyer' ? 'buyer counter' : hover.p.type.toLowerCase()} · ${hover.p.priceText}${hover.p.leadTimeWeeks ? ` · ${hover.p.leadTimeWeeks} wk` : ''}${hover.p.accepted ? ' · ACCEPTED' : ''}` : 'Hover or focus a point for details'}
          </div>
          <svg className="ladder" viewBox={`0 0 ${W} ${H}`} role="img" width="100%" aria-label={`Negotiation ladder: unit price by round for ${ladder.series.length} suppliers`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" />
                <text x={M.l - 8} y={sy(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-3)" className="num">{t.toFixed(1)}</text>
              </g>
            ))}
            {rounds.map((r) => (
              <text key={r} x={sx(r)} y={H - 18} textAnchor="middle" fontSize="11" fill="var(--text-3)">Round {r}</text>
            ))}
            <line x1={M.l} x2={W - M.r} y1={H - M.b + 4} y2={H - M.b + 4} stroke="var(--border-strong)" />
            {ladder.series.map((s, i) => {
              const c = color(i);
              const pts = [...s.points].sort((a, b) => a.x - b.x);
              const d = pts.map((p, k) => `${k ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.price).toFixed(1)}`).join(' ');
              const first = pts[0] as LadderPoint;
              return (
                <g key={s.supplierId}>
                  {pts.length > 1 && <path d={d} fill="none" stroke={c} strokeWidth="2" strokeLinejoin="round" strokeDasharray={s.rejected && !s.accepted ? '4 3' : undefined} />}
                  {pts.map((p) => {
                    const cx = sx(p.x), cy = sy(p.price);
                    const label = `${nm(s.supplierId)} round ${p.round} ${p.side} ${p.priceText}${p.accepted ? ' accepted' : ''}`;
                    const on = { onMouseEnter: () => setHover({ sid: s.supplierId, p }), onFocus: () => setHover({ sid: s.supplierId, p }), onMouseLeave: () => setHover(null), onBlur: () => setHover(null) };
                    return (
                      <g key={p.msgId} tabIndex={0} role="img" aria-label={label} {...on}>
                        <circle cx={cx} cy={cy} r={12} fill="transparent" />
                        {p.accepted && <circle cx={cx} cy={cy} r={9} fill="none" stroke="var(--ok)" strokeWidth="2" />}
                        {p.side === 'supplier'
                          ? <circle cx={cx} cy={cy} r={5} fill={c} stroke="var(--surface)" strokeWidth="2" />
                          : <rect x={cx - 4.5} y={cy - 4.5} width={9} height={9} fill="var(--surface)" stroke={c} strokeWidth="2" />}
                        <title>{label}</title>
                      </g>
                    );
                  })}
                  {direct && <text x={sx(first.x) + 10} y={sy(first.price) - 9} fontSize="11" fill="var(--text-2)" fontWeight="600">{nm(s.supplierId)}</text>}
                </g>
              );
            })}
          </svg>
        </>
      )}
    </div>
  );
}
