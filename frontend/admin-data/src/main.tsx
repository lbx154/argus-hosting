import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider, useI18n } from '@argus-web/i18n';
import { WorkspaceErrorBoundary } from '@argus-web/components/WorkspaceErrorBoundary';
import AdminDataApp from './admin-data/AdminDataApp';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import 'katex/dist/katex.min.css';
import '@argus-web/index.css';

function AdminEntry() {
  const { locale } = useI18n();
  return <WorkspaceErrorBoundary locale={locale}><AdminDataApp /></WorkspaceErrorBoundary>;
}

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 3_000, retry: false, refetchOnWindowFocus: false } } });
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={queryClient}><I18nProvider>
    <AdminEntry />
  </I18nProvider></QueryClientProvider></React.StrictMode>,
);
