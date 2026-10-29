import { describe, expect, it } from 'vitest';
import { buildLadder } from './ladder';
import { offersToMessages, priceText } from './offers';

const rows = [
  { supplier_id: 'S1', round: 1, side: 'supplier' as const, unit_price: '120.0000', lead_time_weeks: 6 },
  { supplier_id: 'S1', round: 1, side: 'buyer' as const, unit_price: '100.0000', lead_time_weeks: 6 },
  { supplier_id: 'S1', round: 2, side: 'supplier' as const, unit_price: '110.0000', lead_time_weeks: 6 },
  { supplier_id: 'S2', round: 1, side: 'supplier' as const, unit_price: '130.0000', lead_time_weeks: 8 },
];

describe('offers -> ladder', () => {
  it('draws one series per supplier, supplier turn on the round and buyer reply half a step later', () => {
    const l = buildLadder(offersToMessages(rows));
    expect(l.series.map((s) => s.supplierId)).toEqual(['S1', 'S2']);
    const s1 = l.series[0]!;
    expect(s1.points.map((p) => [p.side, p.round, p.x])).toEqual([['supplier', 1, 1], ['buyer', 1, 1.5], ['supplier', 2, 2]]);
    expect(l.minPrice).toBe(100);
    expect(l.maxPrice).toBe(130);
  });
  it('marks the awarded supplier’s last offer as accepted', () => {
    const l = buildLadder(offersToMessages(rows, 'S1'));
    const s1 = l.series.find((s) => s.supplierId === 'S1')!;
    expect(s1.accepted).toBe(true);
    expect(s1.acceptedPrice).toBe('110.0000');
    expect(l.series.find((s) => s.supplierId === 'S2')!.accepted).toBe(false);
  });
  it('is safe on no offers and trims trailing zeros for display only', () => {
    expect(buildLadder(offersToMessages([])).series).toEqual([]);
    expect(priceText('120.0000')).toBe('120');
    expect(priceText('110.5000')).toBe('110.5');
    expect(priceText(null)).toBe('');
  });
});
