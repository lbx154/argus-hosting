import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { VerticalStoreView } from '../components/VerticalStore';
import { EMPTY_FILTER, hostedTrialBuild } from '../lib/verticalStore';
import { requestFailureText } from '../lib/requestFailure';
import { ApiError } from '../../../core/src/http';
it('uses hosting build policy only in the hosting overlay', () => {
  expect(hostedTrialBuild({ VITE_ARGUS_HOSTED_TRIAL: '1' })).toBe(true);
  const html = renderToStaticMarkup(<VerticalStoreView payload={null} filter={EMPTY_FILTER} onFilter={() => {}} hosted supported loading={false} refreshing={false} error="" pending={null} failures={{}} onAct={async () => true} onRefresh={() => {}} onJump={() => {}} />);
  expect(html).toContain('provided by the host');
  const error = new ApiError('unavailable', 403, 'GET', '/api/control', 'trial_route_unavailable');
  expect(requestFailureText(error, key => key).refused).toBe(true);
});
