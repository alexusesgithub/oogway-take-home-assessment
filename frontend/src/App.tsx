import { AlertTriangle, Loader2, Menu, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api/client";
import type { AppConfig, Artifact, Mode, ProviderName, SessionSummary } from "./api/types";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Composer, type ComposerHandle } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { MessageItem } from "./components/MessageItem";
import { PendingMessage } from "./components/PendingMessage";
import { ProviderSwitch } from "./components/ProviderSwitch";
import { Sidebar } from "./components/Sidebar";
import { useChat } from "./hooks/useChat";

const PROVIDER_KEY = "lga.provider";

function sessionFromHash(): string | null {
  const match = window.location.hash.match(/^#\/c\/([0-9a-f-]{36})$/);
  return match ? match[1] : null;
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(sessionFromHash);
  const [provider, setProvider] = useState<ProviderName | null>(() => {
    try {
      return (localStorage.getItem(PROVIDER_KEY) as ProviderName | null) ?? null;
    } catch {
      return null;
    }
  });
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const lastSendRef = useRef<{ text: string; mode: Mode } | null>(null);

  const notify = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast(null), 5000);
  }, []);

  // ---------------------------------------------------------------- data
  const refreshConfig = useCallback(async () => {
    try {
      setConfig(await api.config());
      setConfigError(null);
    } catch (err) {
      setConfigError((err as Error).message);
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.sessions());
    } catch {
      /* surfaced through the config banner */
    }
  }, []);

  useEffect(() => {
    void refreshConfig();
    void refreshSessions();
  }, [refreshConfig, refreshSessions]);

  // Poll faster while something is still warming up (model download, indexing).
  const warming =
    !config || config.knowledge_base.ingesting || config.providers.some((p) => p.state === "provisioning");
  useEffect(() => {
    const timer = window.setInterval(refreshConfig, warming ? 4000 : 20000);
    return () => window.clearInterval(timer);
  }, [refreshConfig, warming]);

  useEffect(() => {
    const onHash = () => setSessionId(sessionFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const selectSession = useCallback((id: string | null) => {
    window.location.hash = id ? `/c/${id}` : "";
    setSessionId(id);
    setOpenArtifact(null);
    setSidebarOpen(false);
  }, []);

  const chat = useChat({
    sessionId,
    onSessionCreated: (id) => {
      window.history.replaceState(null, "", `#/c/${id}`);
      setSessionId(id);
      void refreshSessions();
    },
    onTurnFinished: refreshSessions,
    onArtifact: (artifact: Artifact) => setOpenArtifact(artifact.id),
    notify,
  });

  // ---------------------------------------------------------------- provider
  const effectiveProvider: ProviderName = provider ?? config?.default_provider ?? "ollama";
  const providerStatus = config?.providers.find((p) => p.name === effectiveProvider);
  const chooseProvider = (name: ProviderName) => {
    setProvider(name);
    try {
      localStorage.setItem(PROVIDER_KEY, name);
    } catch {
      /* ignore */
    }
  };

  // ---------------------------------------------------------------- sending
  const send = (text: string, mode: Mode) => {
    lastSendRef.current = { text, mode };
    void chat.send(text, mode, provider);
  };

  const retry = () => {
    const last = lastSendRef.current;
    chat.dismissError();
    if (last) window.setTimeout(() => void chat.send(last.text, last.mode, provider), 0);
  };

  // Keep the newest content in view while streaming, unless the user scrolled up.
  const pendingDraftLength = chat.pending?.draft.length ?? 0;
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length, pendingDraftLength, chat.pending?.stage]);

  // Global shortcuts: Ctrl/Cmd+K new chat, "/" focuses the composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        selectSession(null);
        composerRef.current?.focus();
      } else if (e.key === "/" && !(e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement)) {
        e.preventDefault();
        composerRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectSession]);

  const artifactByMessage = useMemo(() => {
    const map = new Map<string, Artifact>();
    for (const a of chat.artifacts) if (a.message_id) map.set(a.message_id, a);
    return map;
  }, [chat.artifacts]);

  const panelSources = useMemo(() => {
    const art = chat.artifacts.find((a) => a.id === openArtifact);
    return chat.messages.find((m) => m.id === art?.message_id)?.sources ?? [];
  }, [chat.artifacts, chat.messages, openArtifact]);

  const title = sessions.find((s) => s.id === sessionId)?.title ?? (sessionId ? "Chat" : "New chat");
  const isEmpty = !chat.messages.length && !chat.pending && !chat.loading;
  const kbEmpty = config && config.knowledge_base.chunks === 0;

  return (
    <div className="app">
      <a href="#composer-input" className="skip-link">
        Skip to message box
      </a>
      <Sidebar
        open={sidebarOpen}
        sessions={sessions}
        activeId={sessionId}
        config={config}
        onNew={() => {
          selectSession(null);
          composerRef.current?.focus();
        }}
        onSelect={selectSession}
        onDelete={async (id) => {
          await api.deleteSession(id).catch(() => notify("Couldn't delete the chat."));
          if (id === sessionId) selectSession(null);
          void refreshSessions();
        }}
      />
      <div className={`scrim${sidebarOpen ? " open" : ""}`} onClick={() => setSidebarOpen(false)} aria-hidden />

      <main className="main" id="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn menu-btn"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open chat list"
          >
            <Menu size={18} />
          </button>
          <h2 className="topbar-title">{title}</h2>
          {chat.artifacts.length > 0 && !openArtifact && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setOpenArtifact(chat.artifacts[chat.artifacts.length - 1].id)}
            >
              <PanelRightOpen size={15} aria-hidden /> Artifacts ({chat.artifacts.length})
            </button>
          )}
          <ProviderSwitch config={config} selected={effectiveProvider} onSelect={chooseProvider} />
        </header>

        {configError && (
          <div className="banner" role="alert">
            <AlertTriangle size={16} aria-hidden />
            <span>Can't reach the API ({configError}). Check that the backend is running.</span>
          </div>
        )}
        {!configError && kbEmpty && (
          <div className="banner info" role="status">
            <Loader2 size={16} aria-hidden />
            <span>
              Building the knowledge base from the transcript repository (first start takes about a minute)…
            </span>
          </div>
        )}
        {!configError && providerStatus && !providerStatus.available && (
          <div className="banner" role="status">
            <AlertTriangle size={16} aria-hidden />
            <span>
              <strong>{providerStatus.label}</strong>: {providerStatus.detail} {providerStatus.hint}
              {config?.fallback_provider
                ? ` Answers will fall back to ${config.fallback_provider}.`
                : " You'll get evidence-only answers until it's available."}
            </span>
          </div>
        )}

        <div className="scroller" ref={scrollerRef}>
          {isEmpty ? (
            chat.loadError ? (
              <div className="empty">
                <div className="callout danger" role="alert">
                  <AlertTriangle size={16} aria-hidden />
                  <p>
                    {chat.loadError.message} <button className="link-btn" onClick={() => selectSession(null)}>Start a new chat</button>
                  </p>
                </div>
              </div>
            ) : (
              <EmptyState
                episodes={config?.knowledge_base.episodes ?? null}
                localModel={providerStatus?.kind === "local"}
                onPick={(text, mode) => composerRef.current?.fill(text, mode)}
              />
            )
          ) : (
            <div className="thread" aria-live="off">
              {chat.loading && (
                <div className="skeleton" aria-label="Loading chat">
                  <span style={{ width: "40%", marginLeft: "auto" }} />
                  <span style={{ width: "90%" }} />
                  <span style={{ width: "75%" }} />
                </div>
              )}
              {chat.messages.map((m) => (
                <MessageItem
                  key={m.id}
                  message={m}
                  artifact={artifactByMessage.get(m.id)}
                  onOpenArtifact={setOpenArtifact}
                  onFeedback={chat.setFeedback}
                />
              ))}
              {chat.pending && !chat.pending.userPersisted && <div className="msg-user">{chat.pending.userText}</div>}
              {chat.pending && (
                <PendingMessage
                  turn={chat.pending}
                  localModel={providerStatus?.kind === "local"}
                  onRetry={retry}
                  onDismiss={chat.dismissError}
                />
              )}
            </div>
          )}
        </div>

        <Composer
          ref={composerRef}
          busy={!!chat.pending && !chat.pending.error}
          onSend={send}
          onStop={chat.stop}
        />
      </main>

      {openArtifact && chat.artifacts.length > 0 && (
        <ArtifactPanel
          artifacts={chat.artifacts}
          activeId={openArtifact}
          sources={panelSources}
          onSelect={setOpenArtifact}
          onClose={() => setOpenArtifact(null)}
        />
      )}

      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}
