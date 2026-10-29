import { describe, expect, it } from 'vitest';
import type { ProjectMessage } from '../api/types';
import { buildLadder } from './ladder';
import { groupFanout, buildFeed, type FeedItem } from './feed';
import { initialRun, reduceEvent } from './runReducer';

let n = 0;
const m = (type: string, from: string, to: string, payload: Record<string, unknown>, id = `id${++n}`): ProjectMessage => ({
  id, seq: n, stage: 'SOURCING', type, from_agent: from, to_agent: to, in_reply_to: null, payload, ts: null,
});

describe('negotiation ladder', () => {
  it('zig-zags buyer/supplier prices per supplier and marks the accepted offer', () => {
    n = 0;
    const msgs = [
      m('OFFER', 'supplier:A', 'buyer', { round: 1, unit_price: '129.8143', lead_time_weeks: 4 }),
      m('OFFER', 'supplier:B', 'buyer', { round: 1, unit_price: '104.9950' }),
      m('COUNTER', 'buyer', 'supplier:A', { round: 1, unit_price: '116.8329' }),
      m('COUNTER', 'supplier:A', 'buyer', { round: 2, unit_price: '123.3236' }),
      m('COUNTER', 'buyer', 'supplier:A', { round: 2, unit_price: '119.7537' }),
      m('COUNTER', 'supplier:A', 'buyer', { round: 3, unit_price: '121.5386' }, 'last'),
      m('ACCEPT', 'buyer', 'supplier:A', { offer_id: 'last' }),
    ];
    const l = buildLadder(msgs);
    expect(l.series.map((s) => s.supplierId)).toEqual(['A', 'B']);
    const a = l.series[0]!;
    expect(a.points.map((p) => [p.side, p.x])).toEqual([['supplier', 1], ['buyer', 1.5], ['supplier', 2], ['buyer', 2.5], ['supplier', 3]]);
    expect(a.accepted).toBe(true);
    expect(a.acceptedPrice).toBe('121.5386');
    expect(l.minPrice).toBeCloseTo(104.995);
    expect(l.maxPrice).toBeCloseTo(129.8143);
    expect(l.maxRound).toBe(3);
    expect(l.series[1]!.accepted).toBe(false);
  });
  it('ignores messages without numeric price/round and handles empty input', () => {
    n = 0;
    expect(buildLadder([]).series).toEqual([]);
    expect(buildLadder([m('OFFER', 'supplier:A', 'buyer', { round: 'x', unit_price: 'nan' })]).series).toEqual([]);
  });
});

describe('feed', () => {
  it('collapses fan-out / fan-in bursts of >= 3', () => {
    n = 0;
    const mk = (msg: ProjectMessage): FeedItem => ({ kind: 'message', key: msg.id, order: msg.seq, id: msg.id, msg });
    const items = [
      mk(m('INVITE', 'buyer', 'supplier:A', {})), mk(m('INVITE', 'buyer', 'supplier:B', {})), mk(m('INVITE', 'buyer', 'supplier:C', {})),
      mk(m('INTEREST_REPLY', 'supplier:A', 'buyer', {})), mk(m('INTEREST_REPLY', 'supplier:B', 'buyer', {})),
    ];
    const g = groupFanout(items);
    expect(g.map((x) => x.kind)).toEqual(['group', 'message', 'message']);
    expect((g[0] as Extract<FeedItem, { kind: 'group' }>).items).toHaveLength(3);
  });
  it('orders by audit seq, joins SSE meta with REST payloads', () => {
    n = 0;
    const msg = m('BRIEF', 'buyer', 'orchestrator', { category: 'x' }, 'm1');
    let s = initialRun();
    s = reduceEvent(s, 'stage_changed', { seq: 2, from: 'NEED', to: 'DISCOVERY' }, 1);
    s = reduceEvent(s, 'message', { seq: 1, message: { id: 'm1', type: 'BRIEF', from_agent: 'buyer', to_agent: 'orchestrator', stage: 'NEED', summary: 's' } }, 1);
    const feed = buildFeed(s, [msg]);
    expect(feed.map((f) => f.kind)).toEqual(['message', 'stage']);
    expect((feed[0] as Extract<FeedItem, { kind: 'message' }>).msg?.payload).toEqual({ category: 'x' });
  });
});

describe('run reducer', () => {
  it('is idempotent on replay and tracks calls/results', () => {
    let s = initialRun();
    const evs: Array<[string, unknown]> = [
      ['subagent_call', { seq: 8, agent: 'buyer', tool: 'research', input_summary: '' }],
      ['subagent_result', { seq: 8, agent: 'buyer', tool: 'research', confidence: 0.9, sources_count: 2, latency_ms: 20 }],
      ['audit_event', { seq: 8, action: 'x', rule_id: null, hash: 'h' }],
      ['policy_block', { seq: 9, side: 'supplier:A', rule_id: 'FLOOR', message_type: 'OFFER' }],
    ];
    for (let pass = 0; pass < 2; pass++) for (const [e, d] of evs) s = reduceEvent(s, e, d, pass + 1);
    expect(s.calls).toHaveLength(1); // second connection replaced seq-8 calls rather than duplicating
    expect(s.calls[0]).toMatchObject({ done: true, confidence: 0.9, sourcesCount: 2 });
    expect(s.audit).toHaveLength(1);
    expect(s.blocks).toHaveLength(1);
    expect(s.lastEventId).toBe(9);
  });
});
