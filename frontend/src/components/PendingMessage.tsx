import { AlertTriangle, RotateCcw, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { PendingTurn } from "../hooks/useChat";
import { Markdown } from "./Markdown";

interface Props {
  turn: PendingTurn;
  localModel: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}

/** The in-flight assistant turn: progress stages, found sources, live text. */
export function PendingMessage({ turn, localModel, onRetry, onDismiss }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (turn.error) return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [turn.error]);

  const elapsed = Math.max(0, Math.round((now - turn.startedAt) / 1000));
  const guests = [...new Set(turn.sources.map((s) => s.guest))];
  const waitingForFirstToken = !turn.draft && !turn.error;

  return (
    <article className="msg-assistant" aria-busy={!turn.error} aria-label="Assistant is answering">
      <div className="avatar" aria-hidden>
        L
      </div>
      <div className="msg-body">
        {turn.error ? (
          <div className="callout danger" role="alert">
            <AlertTriangle size={16} aria-hidden />
            <div style={{ flex: 1 }}>
              <p>
                <strong>Couldn't answer.</strong> {turn.error.message}
              </p>
              {turn.error.hint && <p className="hint">{turn.error.hint}</p>}
              {turn.error.request_id && <p className="hint">Request id: {turn.error.request_id}</p>}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button type="button" className="btn" onClick={onRetry}>
                  <RotateCcw size={14} aria-hidden /> Try again
                </button>
                <button type="button" className="btn btn-ghost" onClick={onDismiss}>
                  <X size={14} aria-hidden /> Dismiss
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="status-line" role="status" aria-live="polite">
            <span className="spinner" aria-hidden />
            <span>{turn.label}…</span>
            {/* The ticking timer is visual only: screen readers hear stage changes, not every second. */}
            <span className="elapsed" aria-hidden="true">
              {elapsed}s
            </span>
          </div>
        )}

        {turn.fallbackNote && (
          <div className="callout" role="note">
            <AlertTriangle size={16} aria-hidden />
            <p>{turn.fallbackNote}</p>
          </div>
        )}

        {guests.length > 0 && !turn.draft && !turn.error && (
          <div className="found-sources" aria-label="Sources found">
            <span className="chip ok">
              {turn.sources.length} passages found
            </span>
            {guests.slice(0, 5).map((g) => (
              <span key={g} className="chip">
                {g}
              </span>
            ))}
          </div>
        )}

        {turn.draft ? (
          <div className="caret">
            <Markdown text={turn.draft} sources={turn.sources} />
          </div>
        ) : (
          waitingForFirstToken && (
            <>
              <div className="skeleton" aria-hidden>
                <span style={{ width: "92%" }} />
                <span style={{ width: "78%" }} />
                <span style={{ width: "64%" }} />
              </div>
              {localModel && elapsed >= 8 && turn.stage === "generating" && (
                <p className="hint" style={{ fontSize: 12.5, color: "var(--text-3)", marginTop: 10 }}>
                  Running on a local model: on a CPU-only laptop the first words take ~20–40 s while it reads the
                  sources; essays take a few minutes.
                </p>
              )}
            </>
          )
        )}
      </div>
    </article>
  );
}
