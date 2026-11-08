import { createRoot } from 'react-dom/client';
import { lazy, Suspense, useEffect, useState } from 'react';
import { App as AntApp, ConfigProvider, Spin } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import { AuthGate, AuthProvider } from './auth';
import 'bpmn-js/dist/assets/diagram-js.css';
import 'bpmn-js/dist/assets/bpmn-js.css';
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';
import './styles.css';
import './auth.css';

const Workspace = lazy(() => import('./App'));

function Root() {
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return <ConfigProvider locale={ruRU} theme={{ token: {
    colorPrimary: '#4f46e5', colorInfo: '#4f46e5', colorText: '#202a3a', colorTextSecondary: '#5d6b82',
    colorTextPlaceholder: '#64748b',
    colorBorder: '#dce1eb', colorBgLayout: '#f3f5f9', borderRadius: 8,
    fontFamily: "'Segoe UI Variable', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: 13, controlHeight: 36, motion: !reducedMotion,
  } }}>
    <AntApp><AuthProvider><AuthGate>
      <Suspense fallback={<div className="auth-state" role="status"><Spin size="large" /><p>Загружаем редактор</p></div>}>
        <Workspace />
      </Suspense>
    </AuthGate></AuthProvider></AntApp>
  </ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<Root />);
