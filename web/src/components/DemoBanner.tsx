import { useQuery } from '@tanstack/react-query';
import { health } from '../api/endpoints';
import { Icon } from './Icon';

/** Tells people when answers are pre-recorded, so a run that ignores their wording does not look like a bug. */
export function DemoBanner({ compact }: { compact?: boolean }) {
  const q = useQuery({ queryKey: ['health'], queryFn: health.get, refetchInterval: 60_000 });
  if (q.data?.llm_mode !== 'replay') return null;
  return (
    <div className="callout info" role="note">
      <Icon name="info" />
      <div>
        <b>Demo mode.</b> The agents replay pre-recorded answers on synthetic data, so the result follows the sample scenario, not the exact words you type.
        {!compact && <> An administrator can switch to live mode in Settings to use the local model.</>}
      </div>
    </div>
  );
}
