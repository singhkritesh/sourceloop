import { useEffect, useRef } from 'react';
import { create } from 'zustand';
import { useQueryClient } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { refreshSession } from '../api/http';
import { initialRun, reduceEvent, type RunState } from '../lib/runReducer';
import { connectSse, SseHttpError } from '../lib/sse';
import { useAuth } from './auth';

export type ConnState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed';
interface RunStore {
  projectId: string | null;
  run: RunState;
  conn: ConnState;
  connInfo: { attempt: number; delayMs?: number; error?: string };
  heartbeatAt: number;
  reset: (pid: string | null) => void;
  applyMany: (frames: Array<{ event: string; data: unknown; conn: number }>) => void;
  setConn: (c: ConnState, info?: { attempt: number; delayMs?: number; error?: string }) => void;
  beat: () => void;
}

export const useRunStore = create<RunStore>((set) => ({
  projectId: null,
  run: initialRun(),
  conn: 'idle',
  connInfo: { attempt: 0 },
  heartbeatAt: 0,
  reset: (pid) => set({ projectId: pid, run: initialRun(), conn: 'idle', connInfo: { attempt: 0 }, heartbeatAt: 0 }),
  applyMany: (frames) =>
    set((s) => {
      let r = s.run;
      for (const f of frames) r = reduceEvent(r, f.event, f.data, f.conn);
      return { run: r };
    }),
  setConn: (conn, info) => set({ conn, connInfo: info ?? { attempt: 0 } }),
  beat: () => set({ heartbeatAt: Date.now() }),
}));

const REFRESH_TRIGGERS = new Set(['stage_changed', 'run_finished', 'escalation_created', 'approval_resolved', 'error']);

/** Opens the SSE stream for a project (fetch streaming, Last-Event-ID resume, backoff) and feeds the run store. */
export function useRunStream(projectId: string | undefined, terminal: boolean) {
  const qc = useQueryClient();
  const terminalRef = useRef(terminal);
  terminalRef.current = terminal;

  useEffect(() => {
    if (!projectId) return;
    const store = useRunStore.getState();
    store.reset(projectId);
    const ac = new AbortController();
    let buf: Array<{ event: string; data: unknown; conn: number }> = [];
    let finishedSeen = false;
    let connId = 0;
    let flushT: ReturnType<typeof setTimeout> | undefined;
    let msgT: ReturnType<typeof setTimeout> | undefined;
    let projT: ReturnType<typeof setTimeout> | undefined;

    const flush = () => {
      flushT = undefined;
      const frames = buf;
      buf = [];
      if (frames.length) useRunStore.getState().applyMany(frames);
    };
    const schedule = () => { flushT ??= setTimeout(flush, 40); };
    const refetchMessages = () => {
      msgT ??= setTimeout(() => { msgT = undefined; void qc.invalidateQueries({ queryKey: ['messages', projectId] }); }, 250);
    };
    const refetchProject = () => {
      projT ??= setTimeout(() => {
        projT = undefined;
        void qc.invalidateQueries({ queryKey: ['project', projectId] });
        void qc.invalidateQueries({ queryKey: ['approvals'] });
      }, 250);
    };

    void (async () => {
      let lastEventId = '';
      let refreshed = false;
      while (!ac.signal.aborted) {
        try {
          await connectSse(
            { url: projects.streamUrl(projectId), signal: ac.signal, lastEventId },
            {
              onFrame: (f) => {
                lastEventId = f.id ?? lastEventId;
                let data: unknown;
                try { data = JSON.parse(f.data); } catch { return; }
                buf.push({ event: f.event, data, conn: connId });
                if (f.event === 'run_finished') finishedSeen = true;
                schedule();
                if (f.event === 'message') refetchMessages();
                if (REFRESH_TRIGGERS.has(f.event)) refetchProject();
              },
              onHeartbeat: () => useRunStore.getState().beat(),
              onOpen: () => { connId += 1; refreshed = false; },
              onState: (s, info) => useRunStore.getState().setConn(s, info),
              isTerminal: () => terminalRef.current || finishedSeen,
            },
          );
          break; // aborted or terminal
        } catch (e) {
          if (ac.signal.aborted) break;
          if (e instanceof SseHttpError && e.status === 401 && !refreshed) {
            refreshed = true; // one refresh attempt per failure streak: never loop on 401
            if (await refreshSession()) continue;
          }
          if (e instanceof SseHttpError && (e.status === 401 || e.status === 403)) {
            // server closed the stream because auth changed: stop, drop the session, App routes to /login
            useAuth.setState({ me: null, status: 'anon' });
          }
          break; // 404 or anything else: give up (error shown through project query)
        }
      }
      flush();
      refetchMessages();
    })();

    return () => {
      ac.abort();
      clearTimeout(flushT);
      clearTimeout(msgT);
      clearTimeout(projT);
      useRunStore.getState().setConn('closed');
    };
  }, [projectId, qc]);
}
