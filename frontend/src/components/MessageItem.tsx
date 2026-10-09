import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Code2,
  Copy,
  FileText,
  Info,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import { memo, useState } from "react";
import type { Artifact, Message } from "../api/types";
import { formatDuration } from "../lib/citations";
import { Markdown } from "./Markdown";
import { SourceList } from "./SourceList";

const ROUTE_LABEL: Record<string, string> = {
  answer: "Ask",
  essay: "Ship 30 essay",
  artifact: "Artifact",
  chitchat: "Help",
};

interface Props {
  message: Message;
  artifact?: Artifact;
  onOpenArtifact: (id: string) => void;
  onFeedback: (id: string, rating: 1 | -1) => void;
}

function MessageItemImpl({ message, artifact, onOpenArtifact, onFeedback }: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (message.role === "user") {
    return <div className="msg-user">{message.content}</div>;
  }

  const q = message.quality ?? {};
  const totalMs = typeof message.metrics?.total_ms === "number" ? message.metrics.total_ms : null;

  const showCitation = (id: string) => {
    setSourcesOpen(true);
    setHighlight(id);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard can be unavailable on http origins */
    }
  };

  return (
    <article className="msg-assistant" aria-label="Assistant answer">
      <div className="avatar" aria-hidden>
        L
      </div>
      <div className="msg-body">
        {message.status === "degraded" && message.error && (
          <div className="callout" role="note">
            <AlertTriangle size={16} aria-hidden />
            <div>
              <p>
                <strong>Evidence-only mode.</strong> {message.error.message}
              </p>
              {message.error.hint && <p className="hint">{message.error.hint}</p>}
            </div>
          </div>
        )}
        {q.answer_type === "not_covered" && (
          <div className="callout info" role="note">
            <Info size={16} aria-hidden />
            <p>No transcript passage answers this, so the assistant declined instead of guessing.</p>
          </div>
        )}

        <Markdown
          text={message.content}
          sources={message.sources}
          activeCitation={highlight}
          onCitation={showCitation}
        />

        {artifact && (
          <button type="button" className="artifact-card" onClick={() => onOpenArtifact(artifact.id)}>
            <span className="artifact-card-icon" aria-hidden>
              {artifact.kind === "html" ? <Code2 size={18} /> : <FileText size={18} />}
            </span>
            <span>
              <span className="artifact-card-title">{artifact.title}</span>
              <span className="artifact-card-sub">
                {" "}
                {artifact.kind === "html" ? "HTML page" : "Markdown document"} · v{artifact.version} · Open in viewer
              </span>
            </span>
          </button>
        )}

        {q.essay && <EssayChecklist checks={q.essay.checks} />}

        <SourceList
          sources={message.sources}
          cited={message.citations}
          highlight={highlight}
          open={sourcesOpen}
          onToggle={() => setSourcesOpen((v) => !v)}
        />

        <div className="msg-meta">
          {message.route && (
            <span className="chip accent" title={message.route_reason ?? undefined}>
              {ROUTE_LABEL[message.route] ?? message.route}
            </span>
          )}
          <QualityChips message={message} />
          {message.model && (
            <span className="chip" title={`Provider: ${message.provider}`}>
              {message.provider === "ollama" ? "Local" : "Cloud"} · {message.model}
            </span>
          )}
          {totalMs != null && <span>{formatDuration(totalMs)}</span>}
          <span className="msg-actions">
          <button type="button" className="icon-btn" onClick={copy} aria-label="Copy answer" title="Copy answer">
            {copied ? <Check size={15} /> : <Copy size={15} />}
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Helpful"
            title="Helpful"
            aria-pressed={message.feedback === 1}
            onClick={() => onFeedback(message.id, 1)}
          >
            <ThumbsUp size={15} />
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Not helpful"
            title="Not helpful"
            aria-pressed={message.feedback === -1}
            onClick={() => onFeedback(message.id, -1)}
          >
            <ThumbsDown size={15} />
          </button>
          </span>
        </div>
      </div>
    </article>
  );
}

function QualityChips({ message }: { message: Message }) {
  const q = message.quality ?? {};
  const chips = [];
  if (q.answer_type === "grounded" && message.citations.length) {
    chips.push(
      <span key="g" className="chip ok" title="Every claim is tied to a transcript passage">
        <CheckCircle2 size={12} aria-hidden /> {message.citations.length} source{message.citations.length > 1 ? "s" : ""} cited
      </span>,
    );
  }
  if (q.answer_type === "uncited") {
    chips.push(
      <span key="u" className="chip warn" title="The model did not cite sources; treat with care">
        <AlertTriangle size={12} aria-hidden /> No citations
      </span>,
    );
  }
  if (q.inferred_citations) {
    chips.push(
      <span
        key="i"
        className="chip"
        title="The model omitted these citations; they were added by matching the sentence to the passage it paraphrases"
      >
        {q.inferred_citations} inferred citation{q.inferred_citations > 1 ? "s" : ""}
      </span>,
    );
  }
  if (q.unsupported_blocks) {
    chips.push(
      <span key="s" className="chip warn" title="Statements that do not match any retrieved passage: possible hallucination">
        <AlertTriangle size={12} aria-hidden /> {q.unsupported_blocks} unsupported statement
        {q.unsupported_blocks > 1 ? "s" : ""}
      </span>,
    );
  }
  if (q.unverified_quotes?.length) {
    chips.push(
      <span key="q" className="chip warn" title={`Not found verbatim in the sources: ${q.unverified_quotes.join(" | ")}`}>
        <AlertTriangle size={12} aria-hidden /> Unverified quote
      </span>,
    );
  }
  if (q.invalid_citations?.length) {
    chips.push(
      <span key="x" className="chip warn" title={`Removed citations to non-existent sources: ${q.invalid_citations.join(", ")}`}>
        {q.invalid_citations.length} invalid citation{q.invalid_citations.length > 1 ? "s" : ""} removed
      </span>,
    );
  }
  return <>{chips}</>;
}

function EssayChecklist({ checks }: { checks: { id: string; label: string; passed: boolean; detail: string }[] }) {
  const [open, setOpen] = useState(false);
  const passed = checks.filter((c) => c.passed).length;
  return (
    <div className="sources">
      <button type="button" className="sources-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Ship 30 checklist: {passed}/{checks.length} passed
      </button>
      {open && (
        <ul className="checklist">
          {checks.map((c) => (
            <li key={c.id}>
              {c.passed ? (
                <CheckCircle2 size={15} className="pass" aria-label="passed" />
              ) : (
                <XCircle size={15} className="fail" aria-label="not met" />
              )}
              <span>{c.label}</span>
              <span className="detail">{c.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const MessageItem = memo(MessageItemImpl);
