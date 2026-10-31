import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '@argus-web/i18n';
import AdminDataApp from '../admin-data/AdminDataApp';

vi.mock('@argus-web/lib/motion', () => ({ useGsapMotion: () => undefined }));
const download = vi.hoisted(() => vi.fn());
vi.mock('@argus-web/lib/downloadBlob', () => ({ downloadBlob: download }));

let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
let authorized = true;
let readonly = false;
let account = 'trial-11';
const navigate = vi.fn();
const requests: Array<{ path: string; init: RequestInit }> = [];
const project = { tenant_id: 'trial-11', sid: 's-visible', title: 'Project title', eligible: true };
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const button = (label: string) => renderer!.root.findAllByType('button').find(node => text(node) === label)!;

beforeEach(() => {
  authorized = true; readonly = false; account = 'trial-11'; requests.length = 0;
  navigate.mockClear(); download.mockClear();
  vi.stubGlobal('localStorage', { getItem: (key: string) => key === 'argus.locale' ? 'en' : null, setItem: vi.fn(), removeItem: vi.fn() });
  const browser = Object.assign(new EventTarget(), {
    location: { origin: 'https://argus.test', href: 'https://argus.test/admin/data/?tenant=trial-11&sid=s-visible', search: '?tenant=trial-11&sid=s-visible', assign: navigate },
    history: { state: null, replaceState: vi.fn() }, parent: null as unknown, setTimeout, clearTimeout,
    matchMedia: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
  browser.parent = browser;
  vi.stubGlobal('window', browser);
  vi.stubGlobal('document', { documentElement: { dataset: {}, lang: 'en' } });
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit) => {
    requests.push({ path, init });
    if (path === '/admin/status') return authorized
      ? Response.json({ role: 'admin', key_id: 'admin', readonly })
      : Response.json({ detail: 'expired' }, { status: 401 });
    if (path.startsWith('/admin/api/training/collaboration?')) return Response.json({
      projects: [project], tasks: [], offset: 0, next_offset: null, has_more_projects: false, total_projects: 1,
      counts: {}, purpose: 'internal_training', completeness: { page_truncated: false },
    });
    if (path === '/admin/api/tenants/trial-11/projects') return Response.json({ projects: [{ id: 's-visible', title: 'Readable project name' }] });
    if (path.startsWith('/admin/api/training/collaboration/')) return Response.json({ segments: [] });
    if (path.startsWith('/admin/api/training/observations/')) return Response.json({
      tenant_id: 'trial-11', sid: 's-visible', purpose: 'internal_training', task_id: null, episodes: [],
      pagination: { has_more: false, next_cursor: null },
    });
    if (path === '/admin/api/research/status') return Response.json({ state: 'running' });
    if (path === '/invite/status') return Response.json({ role: 'trial', key_id: account, readonly: false });
    if (path === '/admin/api/training/export-observations') return new Response('zip bytes', {
      headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="processes.zip"' },
    });
    throw new Error(`Unexpected request: ${path}`);
  }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
});
afterEach(() => {
  if (renderer) act(() => renderer!.unmount());
  renderer = undefined;
  client.clear();
  vi.unstubAllGlobals();
});
async function open() {
  await act(async () => { renderer = create(<QueryClientProvider client={client}><I18nProvider><AdminDataApp /></I18nProvider></QueryClientProvider>); });
}

it('loads administrator data after login, exports the selected project, and checks the user identity before navigation', async () => {
  await open();
  await vi.waitFor(() => expect(text(renderer!.root)).toContain('Readable project name'));
  await vi.waitFor(() => expect(requests.some(r => r.path.startsWith('/admin/api/training/observations/'))).toBe(true));
  expect(requests[0].path).toBe('/admin/status');
  expect(requests.every(r => r.init.credentials === 'same-origin')).toBe(true);
  expect(requests.every(r => !new Headers(r.init.headers).has('Authorization'))).toBe(true);
  act(() => button('Export').props.onClick());
  await act(async () => { button('Download ZIP').props.onClick(); });
  await vi.waitFor(() => expect(download).toHaveBeenCalledOnce());
  expect(download.mock.calls[0][1]).toBe('processes.zip');
  const exported = requests.find(r => r.path === '/admin/api/training/export-observations')!;
  expect(exported.init.method).toBe('POST');
  expect(JSON.parse(exported.init.body as string)).toEqual({ purpose: 'internal_training', projects: [{ tenant_id: 'trial-11', sid: 's-visible' }] });
  await act(async () => { await button('Open in user workspace').props.onClick(); });
  expect(navigate).toHaveBeenCalledExactlyOnceWith('/?project=s-visible');
  account = 'trial-02';
  await act(async () => { await button('Open in user workspace').props.onClick(); });
  expect(navigate).toHaveBeenCalledTimes(1);
  expect(renderer!.root.findAllByType('a').some(a => a.props.href === '/invite')).toBe(true);
});

it('returns expired administrator sessions to the administrator login without reading data', async () => {
  authorized = false;
  await open();
  await vi.waitFor(() => expect(renderer!.root.findAllByType('a').some(a => a.props.href === '/admin/login')).toBe(true));
  expect(requests.map(r => r.path)).toEqual(['/admin/status']);
});

it('keeps export disabled for a read-only administrator', async () => {
  readonly = true;
  await open();
  await vi.waitFor(() => expect(text(renderer!.root)).toContain('Readable project name'));
  expect(button('Export').props.disabled).toBe(true);
  expect(renderer!.root.findByProps({ type: 'checkbox' }).props.disabled).toBe(true);
});
