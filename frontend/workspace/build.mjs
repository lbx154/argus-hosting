import { cpSync, existsSync, mkdirSync, rmSync, realpathSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

// A disposable source copy keeps host policy out of the Argus checkout.
const source = process.env.ARGUS_SOURCE_DIR && realpathSync(process.env.ARGUS_SOURCE_DIR);
const stage = process.env.ARGUS_HOSTING_WEB_STAGE && resolve(process.env.ARGUS_HOSTING_WEB_STAGE);
const here = fileURLToPath(new URL('.', import.meta.url));
if (!source || !existsSync(resolve(source, 'frontend/web/package-lock.json'))) throw new Error('Set ARGUS_SOURCE_DIR to an Argus source checkout');
if (!stage || existsSync(stage) || stage === source || stage.startsWith(source + '/') || source.startsWith(stage + '/')) throw new Error('Set ARGUS_HOSTING_WEB_STAGE to a new disposable directory outside Argus');
mkdirSync(stage, { recursive: true });
const skip = new Set(['.git', 'node_modules', 'dist', 'bundle']);
for (const path of ['frontend/web', 'frontend/core', 'packages/contracts', 'package.json', 'tsconfig.base.json']) {
  cpSync(resolve(source, path), resolve(stage, path), { recursive: true, filter: path => !skip.has(basename(path)) });
}
function run(cmd, args, cwd, env = process.env) {
  const result = spawnSync(cmd, args, { cwd, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
run('git', ['apply', '--check', resolve(here, 'hosting.patch')], stage);
run('git', ['apply', resolve(here, 'hosting.patch')], stage);
const web = resolve(stage, 'frontend/web');
// These tests exercise policy owned by this repository, independent of the plain web suite.
rmSync(resolve(web, 'src/test'), { recursive: true });
cpSync(resolve(here, 'test'), resolve(web, 'src/test'), { recursive: true });
run('npm', ['ci'], web);
run('npx', ['vitest', 'run', 'src/test', '--maxWorkers=4'], web);
run('npm', ['run', 'build'], web, { ...process.env, VITE_ARGUS_HOSTED_TRIAL: '1' });
console.log(`Hosting workspace assets: ${resolve(web, 'dist')}`);
