import { Database, Loader2, Plus, Trash2 } from "lucide-react";
import type { AppConfig, SessionSummary } from "../api/types";

interface Props {
  open: boolean;
  sessions: SessionSummary[];
  activeId: string | null;
  config: AppConfig | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

function groupLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const days = Math.floor((today.setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  return "Older";
}

export function Sidebar({ open, sessions, activeId, config, onNew, onSelect, onDelete }: Props) {
  const groups: [string, SessionSummary[]][] = [];
  for (const s of sessions) {
    const label = groupLabel(s.updated_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(s);
    else groups.push([label, [s]]);
  }
  const kb = config?.knowledge_base;

  return (
    <nav className={`sidebar${open ? " open" : ""}`} aria-label="Chats">
      <div className="brand">
        <div className="brand-mark" aria-hidden>
          L
        </div>
        <div>
          <div className="brand-name">Lenny Growth Assistant</div>
          <div className="brand-sub">Grounded in Lenny's Podcast</div>
        </div>
      </div>
      <div className="sidebar-actions">
        <button type="button" className="btn" onClick={onNew}>
          <Plus size={15} aria-hidden /> New chat
        </button>
      </div>

      <ul className="session-list">
        {sessions.length === 0 && (
          <li className="session-group" style={{ textTransform: "none", fontWeight: 400 }}>
            Your chats will appear here.
          </li>
        )}
        {groups.map(([label, items]) => (
          <li key={label}>
            <div className="session-group">{label}</div>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {items.map((s) => (
                <li key={s.id} className={`session-item${s.id === activeId ? " active" : ""}`}>
                  <button
                    type="button"
                    onClick={() => onSelect(s.id)}
                    aria-current={s.id === activeId ? "page" : undefined}
                    title={s.preview ?? s.title}
                  >
                    <span className="session-title">{s.title}</span>
                  </button>
                  <button
                    type="button"
                    className="icon-btn session-delete"
                    aria-label={`Delete chat "${s.title}"`}
                    onClick={() => {
                      if (window.confirm(`Delete "${s.title}"? This removes its messages and artifacts.`)) onDelete(s.id);
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        {kb ? (
          <span className="kb-line">
            {kb.ingesting ? <Loader2 size={13} className="spin" aria-hidden /> : <Database size={13} aria-hidden />}
            {kb.ingesting && kb.chunks === 0
              ? "Indexing transcripts…"
              : `${kb.episodes} episodes · ${kb.chunks.toLocaleString()} passages`}
          </span>
        ) : (
          <span>Connecting…</span>
        )}
        {kb?.last_run?.source_ref && (
          <span title={kb.last_run.source_ref}>Source @ {kb.last_run.source_ref.slice(0, 7)}</span>
        )}
        {config && <span>v{config.app_version}</span>}
      </div>
    </nav>
  );
}
