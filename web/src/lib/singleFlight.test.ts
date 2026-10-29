import { describe, expect, it, vi } from 'vitest';
import { singleFlight } from './singleFlight';
import { refreshSession, request, setUnauthenticatedHandler } from '../api/http';

describe('singleFlight', () => {
  it('shares one in-flight promise among concurrent callers, then allows a new one', async () => {
    let calls = 0;
    const f = singleFlight(async () => { calls += 1; await new Promise((r) => setTimeout(r, 10)); return calls; });
    const [a, b, c] = await Promise.all([f(), f(), f()]);
    expect([a, b, c]).toEqual([1, 1, 1]);
    expect(await f()).toBe(2);
  });
  it('clears on rejection', async () => {
    let n = 0;
    const f = singleFlight(async () => { n += 1; if (n === 1) throw new Error('x'); return n; });
    await expect(f()).rejects.toThrow('x');
    expect(await f()).toBe(2);
  });
});

describe('http: concurrent 401s trigger exactly one refresh', () => {
  it('refreshes once, retries each request once', async () => {
    let refreshes = 0;
    let authed = false;
    const seen: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      seen.push(url);
      if (url.endsWith('/auth/refresh')) { refreshes += 1; await new Promise((r) => setTimeout(r, 15)); authed = true; return new Response('{}', { status: 200 }); }
      if (!authed) return new Response(JSON.stringify({ error: { code: 'UNAUTHENTICATED', message: 'x', request_id: 'r' } }), { status: 401 });
      return new Response(JSON.stringify({ ok: url }), { status: 200 });
    }));
    const out = await Promise.all([request('GET', '/a'), request('GET', '/b'), request('GET', '/c')]);
    expect(refreshes).toBe(1);
    expect(out).toHaveLength(3);
    expect(seen.filter((u) => u.endsWith('/a'))).toHaveLength(2);
    vi.unstubAllGlobals();
  });

  it('calls the unauthenticated handler when refresh fails', async () => {
    const h = vi.fn();
    setUnauthenticatedHandler(h);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
    await expect(request('GET', '/x')).rejects.toMatchObject({ status: 401 });
    expect(h).toHaveBeenCalledTimes(1);
    expect(await refreshSession()).toBe(false);
    vi.unstubAllGlobals();
  });
});
