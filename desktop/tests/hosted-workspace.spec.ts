import { expect, test } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

let server: ChildProcess;
let origin: string;
let serverErrors = '';

test.beforeAll(async () => {
  const binary = process.env.ARGUS_HOSTING_FROZEN_BACKEND;
  if (!binary) throw new Error('Set ARGUS_HOSTING_FROZEN_BACKEND to the newly built hosting backend');
  // Reuse Argus's read-only project fixture, executing it inside the real
  // frozen runtime. No installed Python package or model call supplies the UI.
  const fixture = readFileSync('tests/workbench-layout-server.py', 'utf8');
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !name.startsWith('PYTHON') && !name.startsWith('ARGUS_')
    && !/API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL/.test(name)));
  server = spawn(binary, ['-c', fixture], {
    env: { ...env, ARGUS_SKILL_COPILOT_TRIAL: '1', PYTHONUTF8: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stderr!.on('data', chunk => { serverErrors += chunk.toString(); });
  origin = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Frozen workspace fixture timed out: ${serverErrors}`)), 20_000);
    const lines = createInterface({ input: server.stdout! });
    lines.on('line', line => {
      if (!line.startsWith('{"origin":')) return;
      clearTimeout(timer);
      resolve(JSON.parse(line).origin);
      lines.close();
    });
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Frozen workspace exited ${code}: ${serverErrors}`)); });
  });
});

test.afterAll(() => { server?.kill(); });

test('frozen hosted workspace opens the native private Key settings', async ({ page }) => {
  await page.route('**/bridge.ts', route => route.fulfill({
    contentType: 'application/javascript',
    body: `export const desktopBridge = new Proxy({
      getStatus: async () => ({ state: 'ready', message: 'Ready' }),
      getSetup: async () => ({ complete: true, trialMode: true, host: '127.0.0.1', port: 8799,
        runnerKind: 'copilot', runnerConfigured: true, runnerBins: {}, detectedRunners: {},
        piConfiguration: { configDir: '' }, releaseIdentity: {}, runtimeIdentity: {} }),
      isWindowVisible: async () => true,
      getAppearance: async () => ({ theme: 'light', resolvedTheme: 'light' }),
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.8', userInitiated: false }),
      getTrialStatus: async () => ({ tokensRemaining: 900, tokenLimit: 1000, stale: false }),
      openCockpit: async () => ${JSON.stringify(`${origin}/?token=local-layout-test&project=s-layout&view=activity`)}
    }, { get: (object, key) => object[key] || (() => undefined) });`,
  }));
  const [config] = await Promise.all([
    page.waitForResponse(`${origin}/api/projects/s-layout/config`),
    page.goto('/'),
  ]);
  expect(config.ok()).toBe(true);
  expect((await config.json()).trial_mode).toBe(true);
  const frame = page.frameLocator('#cockpitFrame');
  await expect(frame.locator('a[href="/invite/compute"]')).toHaveCount(1);
  await frame.locator('summary').filter({ hasText: /Task settings and model|任务设置与模型/ }).click();
  await frame.getByRole('button', { name: /更换 Key|Change Key/ }).first().click();
  await expect(page.locator('#trialKey')).toBeVisible();
  await expect(page.locator('#trialBalance')).toContainText('900');
  const key = 'argus_trial_' + 'e'.repeat(64);
  await page.locator('#trialKey').fill(key);
  await page.locator('#ownOpen').click();
  await expect(page.locator('#trialKey')).toHaveValue('');
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain(key);
});
