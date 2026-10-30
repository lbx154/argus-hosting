import { existsSync, lstatSync, mkdirSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = process.env.ARGUS_SOURCE_DIR;
if (!source || !existsSync(resolve(source, 'frontend/web/src/components/WorkspaceShell.tsx'))) {
  throw new Error('Set ARGUS_SOURCE_DIR to an Argus source checkout with frontend/web/src.');
}
const link = resolve(root, 'node_modules/@argus-source');
mkdirSync(dirname(link), { recursive: true });
if (existsSync(link) || (() => { try { return lstatSync(link).isSymbolicLink(); } catch { return false; } })()) {
  if (!lstatSync(link).isSymbolicLink()) throw new Error(`${link} must be a generated symlink`);
  rmSync(link);
}
symlinkSync(realpathSync(source), link, 'dir');
const commands = {
  dev: [['vite']], preview: [['vite', 'preview']],
  typecheck: [['tsc', '--noEmit']],
  test: [['vitest', 'run', '--maxWorkers=4']],
  build: [['tsc', '--noEmit'], ['vite', 'build']],
};
const command = process.argv[2];
if (!commands[command]) throw new Error('Expected dev, preview, typecheck, test or build');
for (const [binary, ...args] of commands[command]) {
  const result = spawnSync(resolve(root, 'node_modules/.bin', binary), [...args, ...process.argv.slice(3)], { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
