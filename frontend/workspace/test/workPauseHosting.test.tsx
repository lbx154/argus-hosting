import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkPauseBanner } from '../components/WorkPauseBanner';
import { I18nProvider } from '../i18n';
import { errorText } from '../lib/format';
afterEach(() => vi.unstubAllGlobals());
const reason = 'workspace storage is full (34.0 GB of 30.0 GB); work stays paused until files are removed';
it.each([
  ['zh-CN', '工作区存储已满（34.0 GB / 30.0 GB），任务已暂停，文件都保留着'],
  ['en', 'Workspace storage is full (34.0 GB / 30.0 GB). Work is paused and every file is kept'],
])('shows hosted storage policy from the runtime reason (%s)', (locale, expected) => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  vi.stubGlobal('document', { documentElement: { lang: '' } });
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<I18nProvider><WorkPauseBanner pause={{ reason, since: 1 }} /></I18nProvider>); });
  expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain(expected);
  act(() => renderer.update(<I18nProvider><WorkPauseBanner pause={null} /></I18nProvider>));
  expect(renderer.toJSON()).toBeNull();
  act(() => renderer.unmount());
});
it('words a refused start with the hosted storage sentence', () => {
  expect(errorText(new Error(reason))).toMatch(/34\.0 GB \/ 30\.0 GB/);
});
