import { expect, test, type Page } from '@playwright/test';

async function launch(page: Page, complete = false, options: { state?: string; cockpitReady?: Promise<void>; theme?: 'light' | 'dark' | 'system'; nativeVisible?: boolean; eyeMotion?: 'on' | 'off' | 'system'; delayAppearance?: boolean } = {}) {
  await page.route('**/bridge.ts', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: 'export const desktopBridge = window.desktopTest.bridge;',
  }));
  await page.route('http://127.0.0.1:18880/**', async (route) => {
    if (options.cockpitReady) await options.cockpitReady;
    await route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><title>Test cockpit</title><input id="draft" aria-label="Draft"><p>Manager · Engineer</p>',
    });
  });
  await page.addInitScript(({ configured, initialState, theme, nativeVisible, eyeMotion, delayAppearance }) => {
    const callbacks: Record<string, (value: unknown) => void> = {};
    const state = {
      status: { state: initialState || (configured ? 'ready' : 'idle'), message: configured ? '已就绪' : '请选择 Agent CLI' },
      complete: configured,
      nativeVisible,
      eyeMotion,
      delayAppearance,
      releaseAppearance: null as (() => void) | null,
      motionSaves: 0,
      motionError: false,
      activate: () => callbacks.launch?.(undefined),
      download: (value: unknown) => callbacks.download?.(value),
      pickerCalls: [] as string[],
      pickerPath: 'D:/中文 research/sample',
      trialMode: false,
      failure: '',
      saveCount: 0,
      saved: null as unknown,
      delaySave: false,
      url: 'http://127.0.0.1:18880/',
      savedAppearance: null as unknown,
      releaseSave: null as (() => void) | null,
      emit: (value: { state: string; message: string }) => {
        state.status = value;
        callbacks.status?.(value);
      },
      bridge: {} as Record<string, unknown>,
    };
    state.bridge = {
      getStatus: async () => state.status,
      isWindowVisible: async () => state.nativeVisible,
      onLaunchActivation: (callback: (value: unknown) => void) => { callbacks.launch = callback; },
      chooseLocalPath: async (kind: string) => { state.pickerCalls.push(kind); return state.pickerPath; },
      getAppearance: async () => {
        if (state.delayAppearance) await new Promise<void>(resolve => { state.releaseAppearance = resolve; });
        return { theme, resolvedTheme: theme === 'dark' ? 'dark' : 'light', startupEyeMotion: state.eyeMotion };
      },
      setStartupEyeMotion: async (motion: 'on' | 'off' | 'system') => {
        if (state.motionError) throw new Error('Synthetic preference save failure');
        state.motionSaves++;
        state.eyeMotion = motion;
        return { theme, resolvedTheme: theme === 'dark' ? 'dark' : 'light', startupEyeMotion: motion };
      },
      setWindowTheme: async () => undefined,
      setAppearance: async (appearance: unknown) => { state.savedAppearance = appearance; return appearance; },
      getSetup: async () => ({
        complete: state.complete, trialMode: state.trialMode, canRestoreOwnAccount: configured,
        host: '127.0.0.1', port: 18880,
        runnerKind: 'codex', runnerConfigured: state.complete,
        runnerBins: {}, detectedRunners: { codex: 'C:/agents/codex.cmd', pi: 'C:/agents/pi.cmd' },
        piConfiguration: { configDir: '' },
        releaseIdentity: { packageVersion: '0.1.1', releaseId: 'test', distribution: 'preview' },
        runtimeIdentity: { state: state.status.state },
      }),
      openCockpit: async () => state.url,
      onTrialProgress: (callback: (value: unknown) => void) => { callbacks.trialProgress = callback; },
      onTrialDownload: (callback: (value: unknown) => void) => { callbacks.download = callback; },
      getTrialStatus: async () => ({ tokensRemaining: 900000, tokenLimit: 1000000, stale: false }),
      resumeTrial: async () => ({ tokensRemaining: 900000, tokenLimit: 1000000, stale: false }),
      restoreOwnAccount: async () => { state.trialMode = false; return { ok: true }; },
      completeTrialSetup: async () => {
        state.saveCount++;
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.trialMode = true;
        state.complete = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      completeSetup: async (input: unknown) => {
        state.saveCount++;
        state.saved = input;
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.complete = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      restartBackend: async () => state.emit({ state: 'ready', message: '已就绪' }),
      chooseRunner: async () => 'C:/custom/codex.cmd',
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: false }),
      checkForUpdate: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: true, detail: '预览构建不安装发布更新。' }),
      dismissUpdate: async () => undefined,
      onStatus: (callback: (value: unknown) => void) => { callbacks.status = callback; },
      onUpdateStatus: () => undefined,
      onShowSetup: () => undefined,
      onNewChat: () => undefined,
      onOpenDelivery: () => undefined,
    };
    (window as unknown as { desktopTest: typeof state }).desktopTest = state;
  }, { configured: complete, initialState: options.state, theme: options.theme || 'light', nativeVisible: options.nativeVisible ?? true, eyeMotion: options.eyeMotion || 'on', delayAppearance: options.delayAppearance ?? false });
  await page.goto('/', { waitUntil: options.cockpitReady ? 'domcontentloaded' : 'load' });
}

// Only the test harness owns this object; production ships no test IPC or globals.
async function configure(page: Page, changes: Record<string, unknown>) {
  await page.evaluate((values) => Object.assign((window as any).desktopTest, values), changes);
}

test('trial byte progress remains available in the exclusive account-mode layout', async ({ page }) => {
  await launch(page);
  await page.locator('#trialOpen').click();
  await configure(page, { delaySave: true });
  await page.locator('#trialKey').fill(`argus_trial_${'a'.repeat(64)}`);
  await page.locator('#trialSubmit').click();
  await page.evaluate(() => (window as any).desktopTest.download({ downloaded_bytes: 1024, total_bytes: 2048 }));
  await expect(page.locator('#trialDownload')).toBeVisible();
  await expect(page.locator('#trialDownloadBar')).toHaveJSProperty('value', 50);
  await expect(page.locator('#trialDownloadDetails')).toContainText('50%');
  await expect(page.locator('#ownContent')).toBeHidden();
  await page.evaluate(() => (window as any).desktopTest.download({ downloaded_bytes: 2048, total_bytes: null }));
  await expect(page.locator('#trialDownloadBar')).not.toHaveAttribute('value');
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
});

for (const theme of ['light', 'dark']) {
  test(`account choice has one focused panel and no added eye decoration (${theme})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 960, height: 640 });
    await launch(page);
    await expect(page.locator('#wizard')).toBeVisible();
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await expect(page.locator('#ownOpen')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#trialPanel')).toBeHidden();
    await expect(page.locator('#ownContent')).toBeVisible();
    const layout = await page.evaluate(() => {
      // Sample both boxes in one frame; the original wizard entrance animation
      // translates the whole dialog between separate Playwright round trips.
      const own = document.getElementById('ownOpen')!.getBoundingClientRect();
      const trial = document.getElementById('trialOpen')!.getBoundingClientRect();
      return { topDifference: Math.abs(own.y - trial.y), widthDifference: Math.abs(own.width - trial.width) };
    });
    expect(layout.topDifference).toBeLessThan(1);
    expect(layout.widthDifference).toBeLessThan(1);
    await expect(page.locator('#wizardNext')).toBeInViewport();
    await expect(page.locator('.argus-splash-eye circle')).toHaveCount(2);
    await expect(page.locator('.argus-wizard-eye circle')).toHaveCount(2);
    await expect(page.locator('.argus-loading-ring')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath('own-account.png'), animations: 'disabled' });
    await page.locator('#trialOpen').click();
    await expect(page.locator('#ownContent')).toBeHidden();
    await expect(page.locator('#trialPanel')).toBeVisible();
    await expect(page.locator('#trialKey')).toBeInViewport();
    await expect(page.locator('#trialSubmit')).toBeInViewport();
    await expect(page.locator('#wizardNext')).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath('trial-account.png'), animations: 'disabled' });
    await page.locator('#trialKey').fill('discard-on-mode-change');
    await page.locator('#ownOpen').click();
    await expect(page.locator('#trialKey')).toHaveValue('');
    await expect(page.locator('#ownContent')).toBeVisible();
  });
}

test('trial form reports failures and can complete without repeating own-account setup', async ({ page }) => {
  await launch(page);
  await page.locator('#trialOpen').click();
  await page.locator('#trialKey').fill('invalid');
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialProgress')).toContainText('完整的内部测试 Key');
  await configure(page, { failure: '内测 Key 未被识别' });
  await page.locator('#trialKey').fill('argus_trial_' + 'e'.repeat(64));
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialKey')).toHaveValue('');
  await expect(page.locator('#trialProgress')).toContainText('未被识别');
  await configure(page, { failure: '' });
  const key = 'argus_trial_' + 'e'.repeat(64);
  await page.locator('#trialKey').fill(key);
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#cockpit')).toBeVisible();
  const stored = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
  expect(stored).not.toContain(key);
  await expect(page.locator('#trialKey')).toHaveValue('');
  await expect(page.locator('#wizard')).toBeHidden();
});
