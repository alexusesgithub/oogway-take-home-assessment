import { ArrowUp, FileCode2, MessageSquareText, PenLine, Sparkles, Square } from "lucide-react";
import { type KeyboardEvent, forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { Mode } from "../api/types";

const MODES: { id: Mode; label: string; icon: typeof Sparkles; help: string }[] = [
  { id: "auto", label: "Auto", icon: Sparkles, help: "Let the assistant pick: answer, essay or artifact" },
  { id: "answer", label: "Ask", icon: MessageSquareText, help: "Grounded answer with citations" },
  { id: "essay", label: "Essay", icon: PenLine, help: "~1,250-word Ship 30 for 30 essay" },
  { id: "artifact", label: "Artifact", icon: FileCode2, help: "Markdown doc or HTML page in the viewer" },
];

export interface ComposerHandle {
  focus: () => void;
  fill: (text: string, mode?: Mode) => void;
}

interface Props {
  busy: boolean;
  disabled?: boolean;
  onSend: (text: string, mode: Mode) => void;
  onStop: () => void;
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ busy, disabled, onSend, onStop }, ref) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("auto");
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({
    focus: () => areaRef.current?.focus(),
    fill: (value, nextMode) => {
      setText(value);
      if (nextMode) setMode(nextMode);
      requestAnimationFrame(() => areaRef.current?.focus());
    },
  }));

  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  const submit = () => {
    if (!text.trim() || busy || disabled) return;
    onSend(text, mode);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="composer-input" className="sr-only">
          Ask about product management or growth
        </label>
        <textarea
          id="composer-input"
          ref={areaRef}
          rows={1}
          value={text}
          maxLength={4000}
          placeholder={
            mode === "essay"
              ? "What should the essay be about? (or: “turn the answer above into an essay”)"
              : mode === "artifact"
                ? "Describe the doc or page, e.g. “an HTML one-pager of these onboarding tactics”"
                : "Ask about product, growth, PMF, onboarding, pricing…"
          }
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
        />
        <div className="composer-bar">
          <div className="modes" role="radiogroup" aria-label="Response type">
            {MODES.map(({ id, label, icon: Icon, help }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={mode === id}
                className="mode"
                title={help}
                onClick={() => setMode(id)}
              >
                <Icon size={14} aria-hidden />
                <span className="mode-label">{label}</span>
              </button>
            ))}
          </div>
          <span className="meta-spacer" />
          {busy ? (
            <button type="button" className="btn" onClick={onStop} aria-label="Stop streaming">
              <Square size={13} aria-hidden /> Stop
            </button>
          ) : (
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!text.trim() || disabled}
              aria-label="Send message"
            >
              <ArrowUp size={16} aria-hidden />
            </button>
          )}
        </div>
      </form>
      <p className="composer-hint">
        Answers come only from Lenny's Podcast transcripts, with sources. Enter to send · Shift+Enter for a new line
      </p>
    </div>
  );
});
