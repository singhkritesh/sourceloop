import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { mailbox, type MailItem } from '../api/endpoints';
import { errorMessage } from '../api/http';
import { Empty, fmtTime } from '../components/ui';
import { toast } from '../state/ui';

const KIND: Record<string, string> = {
  DECISION: 'Decision', REMINDER: 'Reminder', RFI: 'Request for information', FOLLOW_UP: 'Follow-up', RFI_REPLY: 'Answer received', LOST_RUN: 'Run interrupted',
};

/** The in-app stand-in for email and text messages. A decision can be answered right here by replying in plain words. */
export function Mailbox() {
  const qc = useQueryClient();
  const [channel, setChannel] = useState<'EMAIL' | 'SMS'>('EMAIL');
  const [text, setText] = useState<Record<string, string>>({});
  const q = useQuery({ queryKey: ['mailbox'], queryFn: () => mailbox.list(), refetchInterval: 15_000 });
  const send = useMutation({
    mutationFn: (v: { id: string; text: string }) => mailbox.reply(v.id, v.text),
    onSuccess: (r, v) => { toast.ok(r.answer); setText((t) => ({ ...t, [v.id]: '' })); void qc.invalidateQueries({ queryKey: ['mailbox'] }); void qc.invalidateQueries({ queryKey: ['approvals'] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const seen = useMutation({ mutationFn: (id: string) => mailbox.read(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ['mailbox'] }) });
  const items = (q.data?.items ?? []).filter((m) => m.channel === channel);
  const unread = q.data?.unread ?? { EMAIL: 0, SMS: 0 };
  // a thread = one decision: the message, the replies and the system's answers, oldest first
  const incoming = items.filter((m) => m.direction === 'IN' && m.kind !== 'NOTE');
  const thread = (m: MailItem) => items.filter((x) => x.approval_id && x.approval_id === m.approval_id && (x.direction === 'OUT' || x.kind === 'NOTE')).reverse();
  return (
    <div className="stack">
      <h1>Mail and texts</h1>
      <p className="muted">This is where SourceLoop would email or text you. Answer a decision by replying <b>APPROVE</b>, or <b>DECLINE</b> and a reason. It does exactly what the buttons do.</p>
      <div className="row" role="tablist">
        <button role="tab" aria-selected={channel === 'EMAIL'} className={`btn ${channel === 'EMAIL' ? 'primary' : ''}`} onClick={() => setChannel('EMAIL')}>Email{unread.EMAIL ? ` (${unread.EMAIL})` : ''}</button>
        <button role="tab" aria-selected={channel === 'SMS'} className={`btn ${channel === 'SMS' ? 'primary' : ''}`} onClick={() => setChannel('SMS')}>Texts{unread.SMS ? ` (${unread.SMS})` : ''}</button>
      </div>
      {!incoming.length && <Empty title={channel === 'EMAIL' ? 'No email yet' : 'No texts yet'}>Messages appear here when a decision is waiting for you.</Empty>}
      <div className={channel === 'SMS' ? 'chat-thread' : 'stack'}>
        {incoming.map((m) => (
          <div key={m.id} className={`${channel === 'SMS' ? 'chat-bubble' : 'card mail'} ${m.read ? '' : 'unread'}`} onMouseEnter={() => { if (!m.read) seen.mutate(m.id); }}>
            <div className="mail-meta"><span className="mail-from">{channel === 'SMS' ? 'SourceLoop text' : 'SourceLoop'}</span>
              <span className="mail-kind">{KIND[m.kind] ?? m.kind}</span><span className="spacer" /><span className="muted">{fmtTime(m.created_at)}</span></div>
            {m.subject && <b className="mail-subject">{m.subject}</b>}
            <p style={{ whiteSpace: 'pre-wrap', margin: '4px 0' }}>{m.body}</p>
            {m.project_id && <Link to={`/projects/${m.project_id}`}>Open the project</Link>}
            {thread(m).map((x) => (
              <div key={x.id} className={`chat-row ${x.direction === 'OUT' ? 'me' : 'agent'}`} style={{ marginTop: 8 }}>
                <div className="chat-bubble">{x.body}</div></div>))}
            {m.open && m.approval_id && (
              <form className="row wrap" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if ((text[m.id] ?? '').trim()) send.mutate({ id: m.id, text: text[m.id] ?? '' }); }}>
                <input className="input" style={{ flex: 1, minWidth: 180 }} aria-label="Your reply" placeholder="APPROVE, or DECLINE and a reason" maxLength={500}
                  value={text[m.id] ?? ''} onChange={(e) => setText((t) => ({ ...t, [m.id]: e.target.value }))} />
                <button className="btn primary" disabled={send.isPending || !(text[m.id] ?? '').trim()}>Send</button>
                <button type="button" className="btn" onClick={() => send.mutate({ id: m.id, text: 'APPROVE' })} disabled={send.isPending}>Approve</button>
              </form>)}
          </div>))}
      </div>
    </div>
  );
}
