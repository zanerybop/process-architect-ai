import type { AuthSession, ClarificationAnswer, Example, Generation, GenerationSummary, Health, Revision, Validation } from './types';
import type { BpmnSchema } from './BpmnDocumentEditor';

let csrfToken: string | null = null;

export class ApiError extends Error {
  constructor(message: string, readonly code?: string, readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, method = 'GET', payload?: unknown): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), path === '/api/generate' ? 400_000 : 30_000);
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: {
        'X-Requested-With': 'ProcessArchitect',
        ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(method !== 'GET' && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    if (response.status === 204) return undefined as T;
    let result;
    try {
      result = await response.json();
    } catch {
      throw new ApiError('Сервер вернул непонятный ответ. Проверьте подключение к API.');
    }
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith('/api/auth/')) {
        window.dispatchEvent(new Event('auth:expired'));
      }
      throw new ApiError(
        typeof result.detail === 'string' ? result.detail : 'Не удалось выполнить запрос.',
        result.issues?.[0]?.code,
        response.status,
      );
    }
    return result as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('Время ожидания истекло. Проверьте подключение и повторите запрос.');
    }
    throw new ApiError('API недоступен. Проверьте, запущен ли сервер, и повторите запрос.');
  } finally {
    window.clearTimeout(timeout);
  }
}

async function sessionRequest(path: string, payload?: { username: string; password: string }) {
  const session = await request<AuthSession>(path, payload ? 'POST' : 'GET', payload);
  csrfToken = session.csrf_token;
  return session;
}

export const api = {
  session: () => sessionRequest('/api/auth/session'),
  login: (username: string, password: string) => sessionRequest('/api/auth/login', { username, password }),
  logout: async () => {
    await request<void>('/api/auth/logout', 'POST');
    csrfToken = null;
  },
  health: () => request<Health>('/api/health'),
  examples: () => request<{ cases: Example[] }>('/api/examples'),
  bpmnSchema: () => request<BpmnSchema>('/api/bpmn/schema'),
  layout: (xml: string) => request<Validation & { xml: string }>('/api/bpmn/layout', 'POST', { xml }),
  generate: (description: string, clarification_answers: ClarificationAnswer[] = [], request_id?: string) => request<Generation>(
    '/api/generate', 'POST', { description, request_id, ...(clarification_answers.length ? { clarification_answers } : {}) },
  ),
  progress: (id: string) => request<{ phase: string; attempt: number }>(`/api/generation-progress/${encodeURIComponent(id)}`),
  generation: (id: string) => request<Generation>(`/api/generations/${encodeURIComponent(id)}`),
  listGenerations: () => request<{ items: GenerationSummary[] }>('/api/generations'),
  validate: (xml: string) => request<Validation>('/api/validate', 'POST', { xml }),
  review: (xml: string, generation_id?: string) => request<Validation>('/api/review', 'POST', { xml, generation_id }),
  revise: (id: string, baseRevisionId: string, xml: string) => request<Revision>(
    `/api/generations/${encodeURIComponent(id)}/revisions`, 'PUT', { xml, base_revision_id: baseRevisionId },
  ),
};
