/** The business views get offers as plain rows (GET /projects/{id}/overview). The price chart draws from message-shaped points, so rows are
 *  converted here. Prices stay Decimal strings; the chart converts to numbers only for drawing. */
import type { OfferRow, ProjectMessage } from '../api/types';

export function offersToMessages(offers: OfferRow[], awardedSupplier?: string | null): ProjectMessage[] {
  const out: ProjectMessage[] = [];
  const lastSupplierOffer = new Map<string, string>();
  const sorted = [...offers].sort((a, b) => a.round - b.round || (a.side === 'buyer' ? 1 : 0) - (b.side === 'buyer' ? 1 : 0));
  sorted.forEach((o, i) => {
    const fromSupplier = o.side !== 'buyer';
    const id = `offer-${o.supplier_id}-${o.round}-${fromSupplier ? 's' : 'b'}`;
    out.push({
      id, seq: i + 1, stage: 'SOURCING', type: fromSupplier ? 'OFFER' : 'COUNTER',
      from_agent: fromSupplier ? `supplier:${o.supplier_id}` : 'buyer', to_agent: fromSupplier ? 'buyer' : `supplier:${o.supplier_id}`,
      in_reply_to: null, ts: null,
      payload: { unit_price: o.unit_price, lead_time_weeks: o.lead_time_weeks, round: o.round, capacity_units: o.capacity_units ?? undefined },
    });
    if (fromSupplier) lastSupplierOffer.set(o.supplier_id, id);
  });
  if (awardedSupplier && lastSupplierOffer.has(awardedSupplier)) {
    out.push({
      id: `accept-${awardedSupplier}`, seq: out.length + 1, stage: 'SOURCING', type: 'ACCEPT', from_agent: 'buyer', to_agent: `supplier:${awardedSupplier}`,
      in_reply_to: null, ts: null, payload: { offer_id: lastSupplierOffer.get(awardedSupplier) },
    });
  }
  return out;
}

export const priceText = (p?: string | null): string => (p == null ? '' : String(p).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1'));
