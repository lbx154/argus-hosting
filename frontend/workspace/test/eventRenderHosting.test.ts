import { expect, it } from 'vitest';
import { renderLine } from '../../../core/src/eventRender';
const line = (event: unknown) => renderLine(event as never, {locale: 'en', showReasoning: true, unknownEventPolicy: 'hide', density: 'compact'});
const zh = (event: unknown) => renderLine(event as never, {locale: 'zh-CN', showReasoning: true, unknownEventPolicy: 'hide', density: 'compact'});
  it('explains a refused Manager check without claiming a team decision failed', () => {
    const event = {
      type: 'life.manager.supervision.failed', status: 'failed', failure_stage: 'provider',
      stop_kind: 'provider_fence', error_code: 'trial_quota_exceeded',
      summary: '402: provider body with private account details',
    };
    expect(zh(event)).toMatchObject({ text: '经理这次没能看完进展（试用额度不够了），没有做任何调整。', tone: 'warn' });
    expect(line(event)?.text).toContain('trial quota');
    expect(zh(event)?.text).not.toContain('团队调整');
    expect(line(event)?.text).not.toContain('private account');
  });
