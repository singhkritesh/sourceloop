import { singleFlight } from '../lib/singleFlight';

export const API_BASE = '/api/v1';
const MOCK_STUDIO = import.meta.env.DEV && import.meta.env.VITE_MOCK_STUDIO === '1';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    /** seconds, from Retry-After (429) */
    public retryAfter?: number,
    /** parsed JSON body (e.g. validation report on 422) */
    public body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** CSRF value lives in a readable cookie (double submit); it is a per-session token, not a credential. */
export function readCsrf(cookie: string = typeof document === 'undefined' ? '' : document.cookie): string {
  for (const part of cookie.split(';')) {
    const [k, ...v] = part.trim().split('=');
    if (k === 'csrf') return decodeURIComponent(v.join('='));
  }
  return '';
}

type Listener = () => void;
let onUnauthenticated: Listener = () => undefined;
export function setUnauthenticatedHandler(fn: Listener): void {
  onUnauthenticated = fn;
}

/** exactly one refresh in flight no matter how many requests hit 401 simultaneously */
export const refreshSession = singleFlight(async (): Promise<boolean> => {
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': readCsrf() },
    });
    return res.ok;
  } catch {
    return false;
  }
});

export interface ReqOpts {
  body?: unknown;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
  /** do not attempt refresh/redirect on 401 (login, refresh itself) */
  noAuthRetry?: boolean;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = undefined;
  }
  const env = (body as { error?: { code?: string; message?: string; request_id?: string } } | undefined)?.error;
  const ra = Number(res.headers.get('Retry-After'));
  return new ApiError(
    res.status,
    env?.code ?? `HTTP_${res.status}`,
    env?.message ?? res.statusText ?? 'request failed',
    env?.request_id ?? res.headers.get('X-Request-ID') ?? undefined,
    Number.isFinite(ra) && ra > 0 ? ra : undefined,
    body,
  );
}

function buildUrl(path: string, query?: ReqOpts['query']): string {
  const u = `${API_BASE}${path}`;
  if (!query) return u;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  const s = qs.toString();
  return s ? `${u}?${s}` : u;
}

export async function request<T>(method: string, path: string, opts: ReqOpts = {}): Promise<T> {
  if (MOCK_STUDIO && path.startsWith('/studio')) {
    const { mockStudio } = await import('./mock/studioMock');
    return mockStudio<T>(method, path, opts.body, opts.query);
  }
  const doFetch = () => {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = readCsrf();
    return fetch(buildUrl(path, opts.query), {
      method,
      credentials: 'include',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  };
  let res = await doFetch();
  if (res.status === 401 && !opts.noAuthRetry) {
    if (await refreshSession()) res = await doFetch();
    if (res.status === 401) {
      onUnauthenticated();
    }
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const http = {
  get: <T>(path: string, opts?: ReqOpts) => request<T>('GET', path, opts),
  post: <T>(path: string, body?: unknown, opts?: ReqOpts) => request<T>('POST', path, { ...opts, body: body ?? {} }),
  put: <T>(path: string, body?: unknown, opts?: ReqOpts) => request<T>('PUT', path, { ...opts, body }),
  patch: <T>(path: string, body?: unknown, opts?: ReqOpts) => request<T>('PATCH', path, { ...opts, body }),
  del: <T>(path: string, opts?: ReqOpts) => request<T>('DELETE', path, opts),
};

/** Plain-language messages for server error codes. Unknown codes fall back to the server message. */
export const CODE_MESSAGES: Record<string, string> = {
  PAYLOAD_TOO_LARGE: 'That request is too large. Shorten the text or remove attachments and try again.',
  QUOTA_EXCEEDED: 'A usage quota has been reached. Try again later or ask an administrator to raise the limit.',
  RUNTIME_UNAVAILABLE: 'This project was lost when the server restarted, so it can no longer be resumed or approved.',
  INVALID_EDIT: 'The edit was not accepted. Check the highlighted fields; the approval is still pending.',
  UNAUTHENTICATED: 'Your session has ended. Sign in again.',
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError && CODE_MESSAGES[e.code]) return CODE_MESSAGES[e.code] as string;
  if (e instanceof ApiError && e.status === 413) return CODE_MESSAGES.PAYLOAD_TOO_LARGE as string;
  if (e instanceof ApiError) return e.requestId ? `${e.message} (${e.code}, req ${e.requestId.slice(0, 8)})` : `${e.message} (${e.code})`;
  return e instanceof Error ? e.message : String(e);
}
