import { SseParser } from "../lib/sse";
import type {
  ApiErrorBody,
  AppConfig,
  Message,
  Mode,
  ProviderName,
  SessionDetail,
  SessionSummary,
  StreamEvent,
} from "./types";

const USER_KEY = "lga.userId";

/** Anonymous, per-browser identity (no login in this scope; see PRD). */
export function userId(): string {
  try {
    let id = localStorage.getItem(USER_KEY);
    if (!id) {
      id = `u-${crypto.randomUUID()}`;
      localStorage.setItem(USER_KEY, id);
    }
    return id;
  } catch {
    return "local-evaluator";
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", "X-User-Id": userId(), ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, {
      code: "network_error",
      message: "Can't reach the server.",
      hint: "Check that the API is running (docker compose ps).",
      retryable: true,
    });
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.error ?? { code: "http_error", message: `HTTP ${res.status}` });
  }
  return body as T;
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  sessions: () => request<SessionSummary[]>("/api/sessions"),
  session: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  createSession: () => request<SessionSummary>("/api/sessions", { method: "POST", body: "{}" }),
  renameSession: (id: string, title: string) =>
    request<SessionSummary>(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  feedback: (messageId: string, rating: 1 | -1) =>
    request<Message>(`/api/messages/${messageId}/feedback`, { method: "POST", body: JSON.stringify({ rating }) }),
};

export interface SendOptions {
  sessionId: string;
  content: string;
  mode: Mode;
  provider: ProviderName | null;
  signal: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}

/** POST a message and dispatch streamed events until `done` or `error`. */
export async function streamMessage({ sessionId, content, mode, provider, signal, onEvent }: SendOptions) {
  let res: Response;
  try {
    res = await fetch(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-User-Id": userId() },
      body: JSON.stringify({ content, mode, provider, stream: true }),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, { code: "network_error", message: "Can't reach the server.", retryable: true });
  }
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.error ?? { code: "http_error", message: `HTTP ${res.status}` });
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const raw of parser.push(value)) {
      try {
        onEvent({ event: raw.event, data: JSON.parse(raw.data) } as StreamEvent);
      } catch {
        // A malformed event must not kill the stream; the final `done` carries the truth.
      }
    }
  }
}
