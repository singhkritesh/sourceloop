import { Link } from 'react-router-dom';
import { Empty } from '../components/ui';

export function NotFound({ reason }: { reason?: 'forbidden' }) {
  return (
    <div className="card" style={{ margin: 24 }}>
      <Empty icon="alert" title={reason === 'forbidden' ? 'Your role cannot open this screen' : 'Page not found'}>
        <Link className="btn" to="/">Back to start</Link>
      </Empty>
    </div>
  );
}
