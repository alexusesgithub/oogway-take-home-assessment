/**
 * Turn `[S1]` citation markers into Markdown links with a `#cite-S1` href, so
 * the Markdown renderer can draw them as interactive chips. Pure.
 * Markers inside code spans/blocks are left alone.
 */
export function linkCitations(markdown: string): string {
  const parts = markdown.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, i) => (i % 2 === 1 ? part : part.replace(/\[(S\d+)\](?!\()/g, "[$1](#cite-$1)")))
    .join("");
}

export function citationId(href: string | undefined): string | null {
  const match = href?.match(/^#cite-(S\d+)$/);
  return match ? match[1] : null;
}

/** "53:12" -> "53:12", 3192 -> "53:12" (fallback when only seconds are known). */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}
