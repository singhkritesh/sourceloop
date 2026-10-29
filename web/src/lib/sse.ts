/**
 * SSE over fetch streaming (NOT EventSource: we need credentials/header control and reconnect control).
 * - SseParser: incremental, chunk-boundary safe, handles CRLF/LF/CR, comments (heartbeat), multi-line data, id retention.
 * - connectSse: reconnect with exponential backoff (1s -> 10s), Last-Event-ID resume, heartbeat watchdog.
 */
export interface SseFrame {
  kind: 'event' | 'comment';
  event: string;
  data: string;
  id: string | null;
  /** last event id known to the parser at the time this frame was emitted (persists across frames per SSE spec) */
  lastEventId: string;
}

export class SseParser {
  private buf = '';
  private event = '';
  private dataLines: string[] = [];
  private id: string | null = null;
  private hasFields = false;
  lastEventId = '';

  push(chunk: string): SseFrame[] {
    this.buf += chunk;
    const out: SseFrame[] = [];
    // normalise line endings; keep a trailing lone CR in the buffer (it may be the first half of CRLF)
    let carry = '';
    if (this.buf.endsWith('\r')) {
      carry = '\r';
      this.buf = this.buf.slice(0, -1);
    }
    const lines = this.buf.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    this.buf = (lines.pop() ?? '') + carry;
    for (const line of lines) {
      const f = this.line(line);
      if (f) out.push(f);
    }
    return out;
  }

  private line(line: string): SseFrame | null {
    if (line === '') {
      if (!this.hasFields) return null;
      const frame: SseFrame = {
        kind: 'event',
        event: this.event || 'message',
        data: this.dataLines.join('\n'),
        id: this.id,
        lastEventId: this.lastEventId,
      };
      this.event = '';
      this.dataLines = [];
      this.id = null;
      this.hasFields = false;
      return frame;
    }
    if (line.startsWith(':')) {
      return { kind: 'comment', event: '', data: line.slice(1).trim(), id: null, lastEventId: this.lastEventId };
    }
    const i = line.indexOf(':');
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? '' : line.slice(i + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    switch (field) {
      case 'event':
        this.event = value;
        this.hasFields = true;
        break;
      case 'data':
        this.dataLines.push(value);
        this.hasFields = true;
        break;
      case 'id':
        if (!value.includes('\0')) {
          this.id = value;
          this.lastEventId = value;
        }
        this.hasFields = true;
        break;
      default:
        break; // retry / unknown fields ignored
    }
    return null;
  }
}

export interface SseHandlers {
  onOpen?: (attempt: number) => void;
  onFrame: (frame: SseFrame) => void;
  onHeartbeat?: () => void;
  onState?: (s: 'connecting' | 'live' | 'reconnecting' | 'closed', info?: { attempt: number; delayMs?: number; error?: string }) => void;
  /** return true when the run is terminal so a clean server close ends the loop instead of reconnecting */
  isTerminal?: () => boolean;
}

export interface SseOptions {
  url: string;
  signal: AbortSignal;
  lastEventId?: string;
  /** no bytes (data or heartbeat comment) for this long => treat the connection as dead (server heartbeat is 15 s) */
  idleTimeoutMs?: number;
  minBackoffMs?: number;
  maxBackoffMs?: number;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

export function backoffDelay(attempt: number, min = 1000, max = 10_000): number {
  return Math.min(max, min * 2 ** Math.max(0, attempt - 1));
}

const defaultSleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
  });

export class SseHttpError extends Error {
  constructor(public status: number, public retryAfterMs?: number) {
    super(`SSE HTTP ${status}`);
  }
}

/** One connection; resolves when the server closes cleanly, rejects on error/idle timeout/abort. */
export async function readOnce(opts: SseOptions, h: SseHandlers, lastId: { v: string }, attempt: number): Promise<void> {
  const f = opts.fetchImpl ?? fetch;
  const idleMs = opts.idleTimeoutMs ?? 45_000;
  const inner = new AbortController();
  const onAbort = () => inner.abort();
  opts.signal.addEventListener('abort', onAbort, { once: true });
  let idle: ReturnType<typeof setTimeout> | undefined;
  let timedOut = false;
  const arm = () => {
    clearTimeout(idle);
    idle = setTimeout(() => { timedOut = true; inner.abort(); }, idleMs);
  };
  try {
    arm();
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    if (lastId.v) headers['Last-Event-ID'] = lastId.v;
    const res = await f(opts.url, { credentials: 'include', signal: inner.signal, headers, cache: 'no-store' });
    if (!res.ok || !res.body) {
      const ra = Number(res.headers.get('Retry-After'));
      throw new SseHttpError(res.status, Number.isFinite(ra) && ra > 0 ? ra * 1000 : undefined);
    }
    h.onOpen?.(attempt);
    const parser = new SseParser();
    parser.lastEventId = lastId.v;
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      arm();
      for (const fr of parser.push(value)) {
        if (fr.kind === 'comment') h.onHeartbeat?.();
        else {
          if (fr.id) lastId.v = fr.id;
          h.onFrame(fr);
        }
      }
    }
  } catch (e) {
    if (timedOut) throw new Error('SSE idle timeout', { cause: e });
    throw e;
  } finally {
    clearTimeout(idle);
    opts.signal.removeEventListener('abort', onAbort);
  }
}

/** Reconnect loop. Resolves when aborted or when the stream ended cleanly while h.isTerminal() is true. */
export async function connectSse(opts: SseOptions, h: SseHandlers): Promise<void> {
  const lastId = { v: opts.lastEventId ?? '' };
  const sleep = opts.sleep ?? defaultSleep;
  let attempt = 0;
  while (!opts.signal.aborted) {
    h.onState?.(attempt === 0 ? 'connecting' : 'reconnecting', { attempt });
    try {
      await readOnce(opts, { ...h, onOpen: (a) => { h.onState?.('live', { attempt: a }); h.onOpen?.(a); } }, lastId, attempt);
      if (opts.signal.aborted) break;
      if (h.isTerminal?.()) break;
      attempt = 1; // clean close but not terminal: reconnect quickly
    } catch (e) {
      if (opts.signal.aborted) break;
      if (e instanceof SseHttpError && [401, 403, 404].includes(e.status)) {
        h.onState?.('closed', { attempt, error: `HTTP ${e.status}` });
        throw e;
      }
      attempt += 1;
      const delay = e instanceof SseHttpError && e.retryAfterMs ? e.retryAfterMs : backoffDelay(attempt, opts.minBackoffMs, opts.maxBackoffMs);
      h.onState?.('reconnecting', { attempt, delayMs: delay, error: e instanceof Error ? e.message : String(e) });
      await sleep(delay, opts.signal);
      continue;
    }
    const delay = backoffDelay(attempt, opts.minBackoffMs, opts.maxBackoffMs);
    h.onState?.('reconnecting', { attempt, delayMs: delay });
    await sleep(delay, opts.signal);
  }
  h.onState?.('closed', { attempt });
}
