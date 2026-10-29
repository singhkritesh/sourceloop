import { describe, expect, it } from 'vitest';
import { backoffDelay, connectSse, SseParser, type SseFrame } from './sse';

const evt = (id: number, event: string, data: string) => `id: ${id}\nevent: ${event}\ndata: ${data}\n\n`;

describe('SseParser', () => {
  it('parses whole frames', () => {
    const p = new SseParser();
    const f = p.push(evt(1, 'message', '{"a":1}'));
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ kind: 'event', event: 'message', data: '{"a":1}', id: '1', lastEventId: '1' });
  });

  it('is robust to arbitrary chunk boundaries', () => {
    const wire = ': connected\n\n' + evt(1, 'stage_changed', '{"to":"DISCOVERY"}') + ': heartbeat\n\n' + evt(2, 'audit_event', '{"seq":2}');
    for (let size = 1; size <= wire.length; size += 3) {
      const p = new SseParser();
      const frames: SseFrame[] = [];
      for (let i = 0; i < wire.length; i += size) frames.push(...p.push(wire.slice(i, i + size)));
      const ev = frames.filter((x) => x.kind === 'event');
      expect(ev.map((x) => x.event)).toEqual(['stage_changed', 'audit_event']);
      expect(ev.map((x) => x.id)).toEqual(['1', '2']);
      expect(frames.filter((x) => x.kind === 'comment').map((x) => x.data)).toEqual(['connected', 'heartbeat']);
    }
  });

  it('handles CRLF, split CRLF, and multi-line data', () => {
    const p = new SseParser();
    const a = p.push('id: 7\r\nevent: x\r\ndata: l1\r');
    const b = p.push('\ndata: l2\r\n\r\n');
    expect([...a, ...b].filter((f) => f.kind === 'event')).toEqual([
      expect.objectContaining({ id: '7', event: 'x', data: 'l1\nl2' }),
    ]);
  });

  it('heartbeat comments never produce events and do not disturb lastEventId', () => {
    const p = new SseParser();
    p.push(evt(5, 'e', '1'));
    const f = p.push(': heartbeat\n\n: heartbeat\n\n');
    expect(f.every((x) => x.kind === 'comment')).toBe(true);
    expect(p.lastEventId).toBe('5');
  });

  it('defaults event name to message and tolerates data without space', () => {
    const f = new SseParser().push('data:abc\n\n');
    expect(f[0]).toMatchObject({ event: 'message', data: 'abc', id: null });
  });
});

describe('backoff', () => {
  it('grows 1s -> 10s', () => {
    expect([1, 2, 3, 4, 5, 9].map((a) => backoffDelay(a))).toEqual([1000, 2000, 4000, 8000, 10000, 10000]);
  });
});

function sseResponse(chunks: string[], hold = false): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      if (!hold) c.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('connectSse', () => {
  it('reconnects with Last-Event-ID after a drop and stops on terminal close', async () => {
    const calls: Array<Record<string, string>> = [];
    let n = 0;
    const fetchImpl = (async (_u: string, init: RequestInit) => {
      calls.push({ ...(init.headers as Record<string, string>) });
      n += 1;
      if (n === 1) return sseResponse([evt(1, 'a', '1'), evt(2, 'a', '2').slice(0, 10)]); // truncated frame, then close
      return sseResponse([evt(3, 'run_finished', '{}')]);
    }) as unknown as typeof fetch;
    const got: string[] = [];
    let done = false;
    const ac = new AbortController();
    const sleeps: number[] = [];
    await connectSse(
      { url: '/s', signal: ac.signal, fetchImpl, sleep: async (ms) => { sleeps.push(ms); } },
      { onFrame: (f) => { got.push(`${f.id}:${f.event}`); if (f.event === 'run_finished') done = true; }, isTerminal: () => done },
    );
    expect(got).toEqual(['1:a', '3:run_finished']);
    expect(calls[0]?.['Last-Event-ID']).toBeUndefined();
    expect(calls[1]?.['Last-Event-ID']).toBe('1');
    expect(sleeps.length).toBe(1);
  });

  it('retries 5xx with backoff but gives up on 401', async () => {
    const ac = new AbortController();
    let n = 0;
    const fetchImpl = (async () => {
      n += 1;
      return new Response('x', { status: n < 3 ? 503 : 401 });
    }) as unknown as typeof fetch;
    const sleeps: number[] = [];
    await expect(
      connectSse({ url: '/s', signal: ac.signal, fetchImpl, sleep: async (ms) => { sleeps.push(ms); } }, { onFrame: () => undefined }),
    ).rejects.toMatchObject({ status: 401 });
    expect(sleeps).toEqual([1000, 2000]);
  });

  it('treats a silent connection as dead (idle watchdog)', async () => {
    const ac = new AbortController();
    let n = 0;
    const fetchImpl = (async (_u: string, init: RequestInit) => {
      n += 1;
      if (n === 1) {
        const enc = new TextEncoder();
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(enc.encode(': heartbeat\n\n'));
            init.signal?.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError')));
          },
        });
        return new Response(stream, { status: 200 });
      }
      ac.abort();
      return sseResponse([], false);
    }) as unknown as typeof fetch;
    const states: string[] = [];
    await connectSse(
      { url: '/s', signal: ac.signal, fetchImpl, idleTimeoutMs: 30, sleep: async () => undefined },
      { onFrame: () => undefined, onState: (s) => states.push(s) },
    );
    expect(n).toBe(2);
    expect(states).toContain('reconnecting');
  });

  it.each([401, 403])('does not reconnect after a clean close followed by HTTP %i (auth change)', async (status) => {
    const ac = new AbortController();
    let n = 0;
    const fetchImpl = (async () => {
      n += 1;
      return n === 1 ? sseResponse([evt(1, 'a', '1')]) : new Response('x', { status });
    }) as unknown as typeof fetch;
    const sleeps: number[] = [];
    const states: string[] = [];
    await expect(
      connectSse({ url: '/s', signal: ac.signal, fetchImpl, sleep: async (ms) => { sleeps.push(ms); } }, { onFrame: () => undefined, onState: (s) => states.push(s) }),
    ).rejects.toMatchObject({ status });
    expect(n).toBe(2);
    expect(states[states.length - 1]).toBe('closed');
  });
});
