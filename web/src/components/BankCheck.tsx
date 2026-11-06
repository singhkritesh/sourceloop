import { Badge } from './ui';
import { Icon } from './Icon';

export interface BankCheckData { ok?: boolean; passed?: boolean; account_ending: string | null; checks: { check: string; passed: boolean }[]; external_verification: string | null }

const LABEL: Record<string, string> = {
  ACCOUNT_PRESENT: 'The supplier gave an account',
  ACCOUNT_FORMAT: 'The account number has a valid shape',
  NAME_MATCHES_REGISTRY: 'The account holder name matches the registered supplier name',
};

/** The bank details check shown with the payment release. The full account number is never kept here: only its last four characters. */
export function BankCheckView({ data }: { data: BankCheckData }) {
  const ok = data.ok ?? data.passed ?? false;
  return (
    <section className="stack" aria-label="Bank details check">
      <h4>Bank details check</h4>
      <p>Account ending <b className="num">{data.account_ending ?? 'none given'}</b> <Badge tone={ok ? 'ok' : 'bad'}>{ok ? 'checks passed' : 'checks failed'}</Badge></p>
      <ul className="stack" style={{ margin: 0, paddingLeft: 18 }}>
        {data.checks.map((c) => <li key={c.check}>{c.passed ? 'Passed' : 'Failed'}: {LABEL[c.check] ?? c.check}</li>)}
      </ul>
      {data.external_verification === 'NOT_CONFIGURED' && (
        <div className="callout warn" role="note"><Icon name="alert" /><div><b>Not verified with a bank.</b> No bank verification service is connected in this build. These checks only compare what the supplier sent with what is on record.
          Confirm the account with the supplier by a channel you already trust before you release payment.</div></div>
      )}
      {!ok && <div className="callout bad" role="alert"><Icon name="alert" /><div>Payment cannot be released while a check fails. Decline, with a reason, and ask the supplier to correct its details.</div></div>}
    </section>
  );
}
