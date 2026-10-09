// Mirrors backend/app/schemas.py. Keep in sync with the OpenAPI spec at /docs.

export type ProviderName = "ollama" | "anthropic";
export type Mode = "auto" | "answer" | "essay" | "artifact";
export type MessageStatus = "complete" | "degraded" | "error";

export interface Source {
  id: string;
  chunk_id: number;
  episode_slug: string;
  title: string;
  guest: string;
  publish_date?: string | null;
  timestamp?: string | null;
  start_seconds?: number | null;
  youtube_url?: string | null;
  source_url?: string | null;
  speakers?: string | null;
  excerpt: string;
  score?: number | null;
  coverage?: number | null;
}

export interface EssayCheck {
  id: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface Quality {
  answer_type?: "grounded" | "uncited" | "not_covered" | "degraded";
  cited_sources?: number;
  citation_coverage?: number;
  invalid_citations?: string[];
  inferred_citations?: number;
  unsupported_blocks?: number;
  supported_ratio?: number | null;
  unverified_quotes?: string[];
  artifact?: string;
  essay?: { word_count: number; score: number; checks: EssayCheck[] };
  [key: string]: unknown;
}

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  route?: string | null;
  route_reason?: string | null;
  provider?: string | null;
  model?: string | null;
  sources: Source[];
  citations: string[];
  quality: Quality;
  metrics: Record<string, number | string | null | Record<string, number>>;
  error?: { code: string; message: string; hint?: string | null; retryable?: boolean } | null;
  feedback?: number | null;
  artifact_id?: string | null;
  created_at: string;
}

export interface Artifact {
  id: string;
  session_id: string;
  message_id: string | null;
  kind: "markdown" | "html";
  title: string;
  content: string;
  version: number;
  created_at: string;
  rendered_html?: string | null;
  sanitizer_report: Record<string, number>;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  preview?: string | null;
}

export interface SessionDetail extends SessionSummary {
  messages: Message[];
  artifacts: Artifact[];
}

export interface ProviderStatus {
  name: ProviderName;
  label: string;
  kind: "local" | "cloud";
  model: string;
  available: boolean;
  state: "ready" | "provisioning" | "unavailable" | "not_configured";
  detail?: string | null;
  hint?: string | null;
}

export interface AppConfig {
  app_version: string;
  default_provider: ProviderName;
  fallback_provider: ProviderName | null;
  providers: ProviderStatus[];
  agent_tools: string;
  embeddings_enabled: boolean;
  knowledge_base: {
    episodes: number;
    chunks: number;
    embedded_chunks: number;
    ingesting: boolean;
    repo_url: string;
    last_run: null | { status: string; source_ref: string | null; finished_at: string | null; error: string | null };
  };
  skills: { name: string; route: string; version: string; description: string }[];
}

export interface ApiErrorBody {
  code: string;
  message: string;
  hint?: string | null;
  retryable?: boolean;
  request_id?: string | null;
}

/** Events streamed by POST /api/sessions/{id}/messages (text/event-stream). */
export type StreamEvent =
  | { event: "user_message"; data: Message }
  | { event: "status"; data: { stage: string; label: string; provider?: string } }
  | {
      event: "meta";
      data: {
        route: string;
        route_reason: string;
        skill: string;
        provider: string;
        model: string | null;
        fallback_used: boolean;
        fallback_note: string | null;
        sources: Source[];
        retrieval: Record<string, unknown>;
      };
    }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "delta"; data: { text: string } }
  | { event: "reset"; data: Record<string, never> }
  | { event: "done"; data: { assistant_message: Message; artifact: Artifact | null } }
  | { event: "error"; data: ApiErrorBody };
