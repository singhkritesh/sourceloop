/** Wrap an async fn so concurrent callers share ONE in-flight promise (used for token refresh on concurrent 401s). */
export function singleFlight<T>(fn: () => Promise<T>): () => Promise<T> {
  let inflight: Promise<T> | null = null;
  return () => {
    if (inflight) return inflight;
    const p = fn().finally(() => {
      if (inflight === p) inflight = null;
    });
    inflight = p;
    return p;
  };
}
