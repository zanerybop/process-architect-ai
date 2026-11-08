import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, Button, Form, Input, Spin } from 'antd';
import { ArrowRightOutlined, LockOutlined, UserOutlined } from '@ant-design/icons';
import { api, ApiError } from './api';
import { Icon } from './Icon';
import type { AuthSession, AuthUser } from './types';

type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'expired' | 'error';
type AuthContextValue = {
  user: AuthUser | null;
  status: AuthStatus;
  error: string;
  acceptSession: (session: AuthSession) => void;
  retry: () => Promise<void>;
  logout: () => Promise<void>;
};
const AuthContext = createContext<AuthContextValue | null>(null);
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось войти. Повторите попытку.';

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('AuthProvider is required');
  return context;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [error, setError] = useState('');
  const acceptSession = useCallback((session: AuthSession) => {
    setUser(session.user);
    setStatus(session.user ? 'authenticated' : 'anonymous');
    setError('');
  }, []);
  const retry = useCallback(async () => {
    setStatus('loading');
    setError('');
    try { acceptSession(await api.session()); }
    catch (failure) { setStatus('error'); setError(errorText(failure)); }
  }, [acceptSession]);

  useEffect(() => {
    let active = true;
    void api.session().then((session) => {
      if (active) acceptSession(session);
    }).catch((failure) => {
      if (active) { setStatus('error'); setError(errorText(failure)); }
    });
    const expire = () => { if (active) setStatus('expired'); };
    window.addEventListener('auth:expired', expire);
    return () => { active = false; window.removeEventListener('auth:expired', expire); };
  }, [acceptSession]);

  useEffect(() => {
    if (status !== 'authenticated') return;
    let active = true;
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      void api.session().then((session) => {
        if (!active) return;
        if (!session.user) setStatus('expired');
        else acceptSession(session);
      }).catch(() => { /* A temporary network failure does not discard an open diagram. */ });
    };
    document.addEventListener('visibilitychange', refresh);
    return () => { active = false; document.removeEventListener('visibilitychange', refresh); };
  }, [acceptSession, status]);

  async function logout() {
    try { await api.logout(); }
    catch (failure) {
      if (!(failure instanceof ApiError && failure.status === 401)) throw failure;
    }
    setUser(null);
    setStatus('anonymous');
    setError('');
  }

  return <AuthContext.Provider value={{ user, status, error, acceptSession, retry, logout }}>
    {children}
  </AuthContext.Provider>;
}

function AuthScreen() {
  const { user, status, acceptSession } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form] = Form.useForm<{ username: string; password: string }>();
  async function submit(values: { username: string; password: string }) {
    if (busy) return;
    setBusy(true);
    setError('');
    try { acceptSession(await api.login(values.username.trim(), values.password)); }
    catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }
  return <main className="auth-page" data-testid="auth-page">
    <section className="auth-story" aria-label="Process Architect AI">
      <div className="auth-brand"><span className="auth-brand-mark"><Icon name="flow" size={25} /></span>
        <span>Process Architect <span className="auth-brand-ai">AI</span></span></div>
      <div className="auth-story-content">
        <div className="auth-eyebrow">РАБОЧЕЕ МЕСТО АНАЛИТИКА</div>
        <h1>От описания —<br />к ясному процессу.</h1>
        <p>Создавайте схемы, проверяйте логику<br className="auth-desktop-break" /> и доводите детали в одном редакторе.</p>
        <div className="auth-flow" aria-hidden="true">
          <span className="auth-flow-event" /><span className="auth-flow-line" />
          <div className="auth-flow-task"><span>01</span>Описание</div><span className="auth-flow-line" />
          <div className="auth-flow-gateway"><span>+</span></div><span className="auth-flow-line" />
          <div className="auth-flow-task auth-flow-task-accent"><span>02</span>Схема процесса</div>
        </div>
      </div>
      <div className="auth-story-footer"><span>BPMN 2.0</span><span>Текст. Логика. Схема.</span></div>
    </section>
    <section className="auth-access" aria-labelledby="auth-title">
      <div className="auth-card">
        <div className="auth-card-kicker">PROCESS ARCHITECT</div>
        <h2 id="auth-title">С возвращением</h2>
        <p className="auth-subtitle">Войдите, чтобы продолжить работу.</p>
        {status === 'expired' && <Alert type="info" showIcon title="Сессия завершилась. Войдите снова."
          description="Открытая схема остаётся в этой вкладке. Войдите в тот же аккаунт, чтобы продолжить." />}
        {error && <Alert type="error" showIcon title={error} role="alert" data-testid="auth-error" />}
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}
          initialValues={{ username: status === 'expired' ? user?.username : '' }}
          disabled={busy} data-testid="auth-form" aria-busy={busy}>
          <Form.Item name="username" label="Логин" rules={[
            { required: true, message: 'Введите логин.' },
            { pattern: /^[a-zA-Z0-9_.-]{3,40}$/, message: 'От 3 до 40 символов: латинские буквы, цифры, точка, дефис или _.' },
          ]}>
            <Input size="large" prefix={<UserOutlined aria-hidden="true" />} autoComplete="username" autoCapitalize="none"
              spellCheck={false} placeholder="Ваш логин" data-testid="auth-username" maxLength={40} autoFocus />
          </Form.Item>
          <Form.Item name="password" label="Пароль" rules={[
            { required: true, message: 'Введите пароль.' },
            { min: 1, max: 128, message: 'Пароль не должен превышать 128 символов.' },
          ]}>
            <Input.Password size="large" prefix={<LockOutlined aria-hidden="true" />} maxLength={128}
              autoComplete="current-password"
              placeholder="Введите пароль" data-testid="auth-password" />
          </Form.Item>
          <Button block size="large" type="primary" htmlType="submit" loading={busy}
            icon={!busy ? <ArrowRightOutlined aria-hidden="true" /> : undefined} iconPlacement="end" data-testid="auth-submit">
            {busy ? 'Входим' : 'Войти'}
          </Button>
        </Form>
        <p className="auth-footer-note"><LockOutlined aria-hidden="true" /> Схемы доступны только в вашем аккаунте</p>
      </div>
    </section>
  </main>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { user, status, error, retry } = useAuth();
  if (status === 'loading') return <div className="auth-state" data-testid="auth-loading" role="status" aria-busy="true">
    <Spin size="large" /><p>Открываем рабочее пространство</p>
  </div>;
  if (status === 'error') return <div className="auth-state">
    <Alert type="error" showIcon title="Не удалось подключиться" description={error} />
    <Button type="primary" onClick={() => void retry()}>Повторить</Button>
  </div>;
  return <>
    {user && <div key={user.id} className="authenticated-workspace" hidden={status !== 'authenticated'}>
      {children}
    </div>}
    {status !== 'authenticated' && <AuthScreen />}
  </>;
}
