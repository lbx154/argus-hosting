import { describe, expect, it } from 'vitest';
import { missionOutcomePresentation } from '../../../core/src/missionOutcome';
describe('a task the trial allowance paused', () => {
  it('says the allowance ran out, not that the model service refused calls', () => {
    const paused = missionOutcomePresentation({
      type: 'life.mission.completed', status: 'parked', park_reason: 'provider_fence', stop_kind: 'provider_fence',
      success: false, resumable: true, outcome_class: 'ended', failure_kind: 'allowance_exhausted',
    } as never, 'zh-CN');
    expect(paused.label).toBe('任务已暂停，因为试用额度已用完；进度已保存，可以继续。');
    const fenced = missionOutcomePresentation({
      type: 'life.mission.completed', status: 'parked', park_reason: 'provider_fence', success: false, outcome_class: 'ended',
    } as never, 'zh-CN');
    expect(fenced.label).toContain('模型服务当前不接受调用');
  });
});
