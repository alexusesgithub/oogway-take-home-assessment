import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, streamMessage } from "../api/client";
import type { ApiErrorBody, Artifact, Message, Mode, ProviderName, Source } from "../api/types";

export interface PendingTurn {
  userText: string;
  mode: Mode;
  provider: ProviderName | null;
  stage: string;
  label: string;
  draft: string;
  sources: Source[];
  route?: string;
  model?: string | null;
  fallbackNote?: string | null;
  startedAt: number;
  /** True once the server echoed the persisted user message. */
  userPersisted: boolean;
  error?: ApiErrorBody;
}

interface Options {
  sessionId: string | null;
  onSessionCreated: (id: string) => void;
  onTurnFinished: () => void;
  onArtifact: (artifact: Artifact) => void;
  notify: (text: string) => void;
}

/**
 * Chat state for one session: persisted messages + the in-flight turn.
 * The server is the source of truth; the stream only previews a turn that the
 * backend persists even if this tab disconnects.
 */
export function useChat({ sessionId, onSessionCreated, onTurnFinished, onArtifact, notify }: Options) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<ApiErrorBody | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const creatingRef = useRef<string | null>(null);
  // Refs, not state, guard re-entrancy: callbacks captured in older renders
  // (e.g. a Retry button) must still see the current truth.
  const inFlightRef = useRef(false);
  const silentAbortRef = useRef(false);
  const loadTokenRef = useRef(0);

  const load = useCallback(async (id: string) => {
    const token = ++loadTokenRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const detail = await api.session(id);
      if (token !== loadTokenRef.current) return; // a newer chat was selected meanwhile
      setMessages(detail.messages);
      setArtifacts(detail.artifacts);
    } catch (err) {
      if (token !== loadTokenRef.current) return;
      setLoadError(err instanceof ApiError ? err.body : { code: "unknown", message: String(err) });
      setMessages([]);
      setArtifacts([]);
    } finally {
      if (token === loadTokenRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // A session created by our own send() is already in sync; keep streaming into it.
    if (sessionId && creatingRef.current === sessionId) return;
    // Switching chats: stop showing the old turn (the server still finishes and saves it).
    if (abortRef.current) {
      silentAbortRef.current = true;
      abortRef.current.abort();
    }
    setPending(null);
    if (!sessionId) {
      loadTokenRef.current++;
      setMessages([]);
      setArtifacts([]);
      setLoadError(null);
      setLoading(false);
      return;
    }
    void load(sessionId);
  }, [sessionId, load]);

  const send = useCallback(
    async (content: string, mode: Mode, provider: ProviderName | null) => {
      const text = content.trim();
      if (!text || inFlightRef.current) return;
      inFlightRef.current = true;

      let id = sessionId;
      const turn: PendingTurn = {
        userText: text,
        mode,
        provider,
        stage: "sending",
        label: "Sending",
        draft: "",
        sources: [],
        startedAt: Date.now(),
        userPersisted: false,
      };
      setPending(turn);

      try {
        if (!id) {
          const created = await api.createSession();
          id = created.id;
          creatingRef.current = id;
          onSessionCreated(id);
        }
        const controller = new AbortController();
        abortRef.current = controller;
        let finished = false;

        await streamMessage({
          sessionId: id,
          content: text,
          mode,
          provider,
          signal: controller.signal,
          onEvent: (ev) => {
            switch (ev.event) {
              case "user_message":
                setMessages((prev) => [...prev, ev.data]);
                setPending((p) => p && { ...p, userPersisted: true });
                break;
              case "status":
                setPending((p) => p && { ...p, stage: ev.data.stage, label: ev.data.label });
                break;
              case "meta":
                setPending(
                  (p) =>
                    p && {
                      ...p,
                      sources: ev.data.sources,
                      route: ev.data.route,
                      model: ev.data.model,
                      fallbackNote: ev.data.fallback_note,
                    },
                );
                break;
              case "sources":
                setPending((p) => p && { ...p, sources: [...p.sources, ...ev.data.sources] });
                break;
              case "delta":
                setPending((p) => p && { ...p, stage: "streaming", draft: p.draft + ev.data.text });
                break;
              case "reset":
                setPending((p) => p && { ...p, draft: "" });
                break;
              case "done":
                finished = true;
                setMessages((prev) => [...prev, ev.data.assistant_message]);
                if (ev.data.artifact) {
                  const art = ev.data.artifact;
                  setArtifacts((prev) => [...prev, art]);
                  onArtifact(art);
                }
                setPending(null);
                break;
              case "error":
                finished = true;
                setPending((p) => p && { ...p, stage: "error", error: ev.data });
                break;
            }
          },
        });
        if (!finished) {
          setPending((p) =>
            p && {
              ...p,
              stage: "error",
              error: { code: "stream_interrupted", message: "The connection closed before the answer finished.", retryable: true },
            },
          );
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          setPending(null);
          if (silentAbortRef.current) {
            silentAbortRef.current = false;
            return;
          }
          notify("Stopped. The server still finishes and saves this answer; reopen the chat in a moment to see it.");
        } else {
          const body = err instanceof ApiError ? err.body : { code: "unknown", message: String(err) };
          setPending((p) => p && { ...p, stage: "error", error: body });
        }
      } finally {
        inFlightRef.current = false;
        abortRef.current = null;
        creatingRef.current = null;
        onTurnFinished();
      }
    },
    [sessionId, onSessionCreated, onTurnFinished, onArtifact, notify],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const dismissError = useCallback(() => setPending(null), []);

  const setFeedback = useCallback(async (messageId: string, rating: 1 | -1) => {
    try {
      const updated = await api.feedback(messageId, rating);
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: updated.feedback } : m)));
    } catch {
      /* non-critical */
    }
  }, []);

  return { messages, artifacts, pending, loading, loadError, send, stop, dismissError, setFeedback, reload: load };
}
