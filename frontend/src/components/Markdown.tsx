import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Source } from "../api/types";
import { citationId, linkCitations } from "../lib/citations";

interface Props {
  text: string;
  sources?: Source[];
  activeCitation?: string | null;
  onCitation?: (id: string) => void;
  className?: string;
}

/**
 * Safe Markdown: react-markdown never renders raw HTML (no rehype-raw), so
 * model output cannot inject markup. Remote images are not loaded (privacy /
 * tracking pixels); links open in a new tab without referrer.
 */
function MarkdownImpl({ text, sources = [], activeCitation, onCitation, className = "prose" }: Props) {
  const byId = new Map(sources.map((s) => [s.id, s]));

  const components: Components = {
    a({ href, children }) {
      const id = citationId(href);
      if (id) {
        const source = byId.get(id);
        const label = source ? `${source.guest}: ${source.title}${source.timestamp ? ` at ${source.timestamp}` : ""}` : id;
        return (
          <button
            type="button"
            className={`cite${activeCitation === id ? " active" : ""}${source ? "" : " missing"}`}
            title={label}
            aria-label={`Source ${id}: ${label}`}
            onClick={() => onCitation?.(id)}
          >
            {id.slice(1)}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow">
          {children}
        </a>
      );
    },
    img({ alt }) {
      return <span className="img-blocked">image not loaded{alt ? `: ${alt}` : ""}</span>;
    },
  };

  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
