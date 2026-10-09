import { ChevronDown, Cloud, Laptop } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AppConfig, ProviderName } from "../api/types";

interface Props {
  config: AppConfig | null;
  selected: ProviderName;
  onSelect: (name: ProviderName) => void;
}

const STATE_TEXT: Record<string, string> = {
  ready: "Ready",
  provisioning: "Preparing model…",
  unavailable: "Unavailable",
  not_configured: "Not configured",
};

/** Visible model toggle (requirement 3.2): which provider answers this chat. */
export function ProviderSwitch({ config, selected, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const providers = config?.providers ?? [];
  const current = providers.find((p) => p.name === selected);
  const Icon = current?.kind === "cloud" ? Cloud : Laptop;

  return (
    <div className="provider-switch" ref={rootRef}>
      <button
        type="button"
        className="provider-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Model provider: ${current?.label ?? selected}, ${STATE_TEXT[current?.state ?? ""] ?? ""}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`dot ${current?.state ?? ""}`} aria-hidden />
        <Icon size={14} aria-hidden />
        <span>{current?.kind === "cloud" ? "Claude" : "Ollama"}</span>
        <span className="provider-model" style={{ color: "var(--text-3)" }}>
          {current?.model}
        </span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <div className="provider-menu" role="menu" aria-label="Choose model provider">
          {providers.map((p) => (
            <button
              key={p.name}
              type="button"
              role="menuitemradio"
              aria-checked={p.name === selected}
              className="provider-option"
              disabled={p.state === "not_configured"}
              onClick={() => {
                onSelect(p.name);
                setOpen(false);
              }}
            >
              <span className={`dot ${p.state}`} aria-hidden />
              <span className="provider-option-name">
                {p.label}
                {p.name === config?.default_provider ? " · default" : ""}
              </span>
              <span className="provider-option-detail">
                {p.model} · {STATE_TEXT[p.state]}
                {p.detail ? `: ${p.detail}` : ""}
                {p.hint && p.state !== "ready" ? ` ${p.hint}` : ""}
              </span>
            </button>
          ))}
          <div className="provider-menu-note">
            Default comes from <code>LLM_PROVIDER</code>.{" "}
            {config?.fallback_provider
              ? `If the chosen provider fails, answers fall back to ${config.fallback_provider}.`
              : "No fallback configured: if the model is down you get evidence-only answers."}
          </div>
        </div>
      )}
    </div>
  );
}
