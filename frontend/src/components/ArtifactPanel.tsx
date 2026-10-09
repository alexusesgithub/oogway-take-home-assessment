import { Check, Copy, Download, Maximize2, Minimize2, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { Artifact, Source } from "../api/types";
import { Markdown } from "./Markdown";

interface Props {
  artifacts: Artifact[];
  activeId: string;
  sources: Source[];
  onSelect: (id: string) => void;
  onClose: () => void;
}

const REPORT_LABELS: Record<string, string> = {
  scripts: "script",
  event_handlers: "event handler",
  embedded_frames: "embedded frame",
  forms: "form control",
  svg: "SVG block",
  meta_refresh_or_base: "meta/base tag",
  unsafe_urls: "unsafe URL",
  external_stylesheets: "external stylesheet",
  external_resources: "external resource",
  css_expressions: "CSS expression",
};

/**
 * Artifact Viewer. Security model (see docs/architecture.md#artifact-security):
 *  - HTML is rendered from server-sanitised `rendered_html` inside an iframe with
 *    `sandbox=""`: no scripts, opaque origin (no cookies/storage/DOM access),
 *    no navigation, popups or forms. The document also carries a CSP that
 *    blocks every network request.
 *  - Markdown is rendered by react-markdown with raw HTML disabled.
 */
export function ArtifactPanel({ artifacts, activeId, sources, onSelect, onClose }: Props) {
  const artifact = artifacts.find((a) => a.id === activeId) ?? artifacts[artifacts.length - 1];
  const [tab, setTab] = useState<"preview" | "source">("preview");
  const [full, setFull] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") (full ? setFull(false) : onClose());
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [full, onClose]);

  if (!artifact) return null;

  const blocked = Object.entries(artifact.sanitizer_report ?? {}).filter(([, n]) => n > 0);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  const download = () => {
    const isHtml = artifact.kind === "html";
    const body = isHtml ? (artifact.rendered_html ?? artifact.content) : artifact.content;
    const blob = new Blob([body], { type: isHtml ? "text/html" : "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const slug = artifact.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
    a.href = url;
    a.download = `${slug || "artifact"}-v${artifact.version}.${isHtml ? "html" : "md"}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside className={`panel${full ? " full" : ""}`} aria-label="Artifact viewer">
      <div className="panel-head">
        <div className="panel-title">
          <strong>{artifact.title}</strong>
          {artifacts.length > 1 ? (
            <select
              aria-label="Choose artifact or version"
              value={artifact.id}
              onChange={(e) => onSelect(e.target.value)}
            >
              {artifacts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title} · v{a.version} · {a.kind === "html" ? "HTML" : "Markdown"}
                </option>
              ))}
            </select>
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              {artifact.kind === "html" ? "HTML" : "Markdown"} · v{artifact.version}
            </span>
          )}
        </div>
        <button type="button" className="icon-btn" onClick={copy} aria-label="Copy source" title="Copy source">
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
        <button type="button" className="icon-btn" onClick={download} aria-label="Download" title="Download">
          <Download size={16} />
        </button>
        <button
          type="button"
          className="icon-btn"
          onClick={() => setFull((v) => !v)}
          aria-label={full ? "Exit full screen" : "Full screen"}
          title={full ? "Exit full screen" : "Full screen"}
        >
          {full ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close viewer" title="Close (Esc)">
          <X size={16} />
        </button>
      </div>

      <div className="panel-tabs" role="tablist" aria-label="View">
        {(["preview", "source"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            aria-controls="artifact-view"
            className="panel-tab"
            onClick={() => setTab(t)}
          >
            {t === "preview" ? "Preview" : artifact.kind === "html" ? "HTML" : "Markdown"}
          </button>
        ))}
      </div>

      <div className="panel-body" id="artifact-view" role="tabpanel">
        {tab === "source" ? (
          <pre className="code-view">
            <code>{artifact.content}</code>
          </pre>
        ) : artifact.kind === "html" ? (
          <iframe
            key={artifact.id}
            title={`Preview: ${artifact.title}`}
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={artifact.rendered_html ?? ""}
          />
        ) : (
          <div className="doc">
            <Markdown text={artifact.content} sources={sources} />
          </div>
        )}
      </div>

      <div className="panel-foot">
        <ShieldCheck size={14} aria-hidden />
        <span>
          {artifact.kind === "html"
            ? "Sandboxed preview: scripts, network requests, forms and external images are blocked."
            : "Rendered as Markdown; raw HTML and remote images are not loaded."}
          {blocked.length > 0 && (
            <>
              {" "}
              Removed:{" "}
              {blocked
                .map(([k, n]) => `${n} ${REPORT_LABELS[k] ?? k}${n > 1 ? "s" : ""}`)
                .join(", ")}
              .
            </>
          )}
        </span>
      </div>
    </aside>
  );
}
