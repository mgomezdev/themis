import { useParams } from 'react-router-dom';

/** Stand-in for a routed screen: renders its label (and the route params it received) so a test can
 *  tell which screen the router picked without booting the real one and all its data fetching.
 *  `splat` opts into showing the `*` param (App wraps everything in a `/*` route, so it is otherwise noise). */
export function marker(label: string, { splat = false } = {}) {
  return function Marker() {
    const params = useParams();
    const shown = Object.entries(params)
      .filter(([k]) => splat || k !== '*')
      .map(([k, v]) => `${k === '*' ? 'rest' : k}=${v}`).join(' ');
    return <div data-testid="screen">{label}{shown ? ` [${shown}]` : ''}</div>;
  };
}
