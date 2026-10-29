/** Negotiation ladder data: per supplier, the zig-zag of prices over rounds. Prices are Decimal strings from the API;
 *  they are converted to numbers ONLY for drawing (display values keep the original string). */
import type { ProjectMessage } from '../api/types';

export interface LadderPoint {
  msgId: string;
  side: 'supplier' | 'buyer';
  type: 'OFFER' | 'COUNTER';
  round: number;
  /** x position: supplier turn sits on the round, buyer reply half a step after it */
  x: number;
  price: number;
  priceText: string;
  leadTimeWeeks?: number;
  accepted: boolean;
}
export interface LadderSeries {
  supplierId: string;
  points: LadderPoint[];
  accepted: boolean;
  acceptedPrice?: string;
  rejected: boolean;
}
export interface Ladder {
  series: LadderSeries[];
  minPrice: number;
  maxPrice: number;
  maxRound: number;
}

const supplierOf = (a: string): string | null => (a.startsWith('supplier:') ? a.slice('supplier:'.length) : null);
const num = (v: unknown): number | null => {
  const n = typeof v === 'string' ? Number(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : null;
};

export function buildLadder(messages: ProjectMessage[]): Ladder {
  const sorted = [...messages].sort((a, b) => a.seq - b.seq);
  const byId = new Map(sorted.map((m) => [m.id, m]));
  const map = new Map<string, LadderSeries>();
  const get = (id: string): LadderSeries => {
    let s = map.get(id);
    if (!s) map.set(id, (s = { supplierId: id, points: [], accepted: false, rejected: false }));
    return s;
  };
  for (const m of sorted) {
    if (m.type === 'OFFER' || m.type === 'COUNTER') {
      const fromSup = supplierOf(m.from_agent);
      const sup = fromSup ?? supplierOf(m.to_agent);
      const price = num(m.payload.unit_price);
      const round = num(m.payload.round);
      if (!sup || price === null || round === null) continue;
      const side = fromSup ? 'supplier' : 'buyer';
      get(sup).points.push({
        msgId: m.id, side, type: m.type, round,
        x: side === 'supplier' ? round : round + 0.5,
        price, priceText: String(m.payload.unit_price),
        leadTimeWeeks: num(m.payload.lead_time_weeks) ?? undefined,
        accepted: false,
      });
    } else if (m.type === 'ACCEPT') {
      const target = byId.get(String(m.payload.offer_id ?? ''));
      const sup = supplierOf(m.from_agent) ?? supplierOf(m.to_agent);
      if (!sup) continue;
      const s = get(sup);
      const pt = s.points.find((p) => p.msgId === target?.id);
      if (pt) {
        pt.accepted = true;
        s.accepted = true;
        s.acceptedPrice = pt.priceText;
      }
    } else if (m.type === 'REJECT') {
      const sup = supplierOf(m.from_agent) ?? supplierOf(m.to_agent);
      if (sup && map.has(sup)) get(sup).rejected = true;
    }
  }
  const series = [...map.values()].filter((s) => s.points.length > 0).sort((a, b) => a.supplierId.localeCompare(b.supplierId));
  const prices = series.flatMap((s) => s.points.map((p) => p.price));
  const rounds = series.flatMap((s) => s.points.map((p) => p.round));
  return {
    series,
    minPrice: prices.length ? Math.min(...prices) : 0,
    maxPrice: prices.length ? Math.max(...prices) : 0,
    maxRound: rounds.length ? Math.max(...rounds) : 0,
  };
}
