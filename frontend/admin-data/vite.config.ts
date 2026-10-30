import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

const source = fileURLToPath(new URL('./node_modules/@argus-source/', import.meta.url));
const api = process.env.ARGUS_WEB_API ?? 'http://127.0.0.1:8799';
export default defineConfig({
  base: '/admin/data/',
  plugins: [react()],
  resolve: {
    preserveSymlinks: true,
    dedupe: ['react', 'react-dom', '@tanstack/react-query', 'gsap'],
    alias: { '@argus-web': `${source}frontend/web/src`, '@argus-core': `${source}frontend/core/src` },
  },
  server: {
    port: 5174,
    fs: { allow: ['.', source] },
    proxy: Object.fromEntries(['/admin/api', '/admin/status', '/admin/logout', '/invite/status'].map(path => [path, { target: api, changeOrigin: true }])),
  },
  test: { maxWorkers: 4, include: ['src/**/*.test.{ts,tsx}'] },
  build: { outDir: 'dist', assetsInlineLimit: 0, sourcemap: false },
});
