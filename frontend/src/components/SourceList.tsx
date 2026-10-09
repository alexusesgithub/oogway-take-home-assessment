import { ChevronDown, ChevronRight, FileText, PlayCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Source } from "../api/types";

interface Props {
  sources: Source[];
  cited: string[];
  highlight: string | null;
  open: boolean;
  onToggle: () => void;
}

export function SourceList({ sources, cited, highlight, open, onToggle }: Props) {
  if (!sources.length) return null;
  const citedSet = new Set(cited);
  const ordered = [...sources].sort((a, b) => Number(citedSet.has(b.id)) - Number(citedSet.has(a.id)));
  const guests = [...new Set(ordered.map((s) => s.guest))].slice(0, 3).join(", ");

  return (
    <div className="sources">
      <button type="button" className="sources-toggle" aria-expanded={open} onClick={onToggle}>
        {open ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />}
        {sources.length} transcript source{sources.length === 1 ? "" : "s"}
        <span className="sources-guests">· {guests}</span>
      </button>
      {open && (
        <ul className="source-list">
          {ordered.map((s) => (
            <SourceCard key={s.id} source={s} cited={citedSet.has(s.id)} highlighted={highlight === s.id} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SourceCard({ source, cited, highlighted }: { source: Source; cited: boolean; highlighted: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const ref = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);

  const meta = [source.publish_date, source.timestamp && `at ${source.timestamp}`].filter(Boolean).join(" · ");
  return (
    <li
      ref={ref}
      id={`source-${source.chunk_id}`}
      className={`source-card${cited ? "" : " uncited"}${highlighted ? " highlight" : ""}`}
    >
      <div className="source-head">
        <span className="source-id">{source.id}</span>
        <span className="source-guest">{source.guest}</span>
        {!cited && <span className="chip">retrieved, not cited</span>}
      </div>
      <div className="source-episode">
        {source.title}
        {meta && ` · ${meta}`}
      </div>
      <p className={`source-excerpt${expanded ? " expanded" : ""}`}>{source.excerpt}</p>
      <div className="source-links">
        <button type="button" className="link-btn" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
          {expanded ? "Show less" : "Show full excerpt"}
        </button>
        {source.youtube_url && (
          <a href={source.youtube_url} target="_blank" rel="noopener noreferrer">
            <PlayCircle size={14} aria-hidden /> Watch{source.timestamp ? ` at ${source.timestamp}` : ""}
          </a>
        )}
        {source.source_url && (
          <a href={source.source_url} target="_blank" rel="noopener noreferrer">
            <FileText size={14} aria-hidden /> Transcript
          </a>
        )}
      </div>
    </li>
  );
}
