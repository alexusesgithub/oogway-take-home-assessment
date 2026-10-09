import { FileCode2, MessageSquareText, PenLine, Quote, ShieldCheck, Timer } from "lucide-react";
import type { Mode } from "../api/types";

const STARTERS: { kind: string; mode: Mode; icon: typeof PenLine; text: string }[] = [
  { kind: "Ask", mode: "answer", icon: MessageSquareText, text: "How does Superhuman measure product-market fit?" },
  {
    kind: "Ask",
    mode: "answer",
    icon: MessageSquareText,
    text: "What do guests recommend for improving onboarding and activation?",
  },
  { kind: "Ship 30 essay", mode: "essay", icon: PenLine, text: "Write an essay on how to build a growth team" },
  {
    kind: "HTML artifact",
    mode: "artifact",
    icon: FileCode2,
    text: "Make an HTML one-pager comparing product-led and sales-led growth",
  },
];

interface Props {
  episodes: number | null;
  localModel: boolean;
  onPick: (text: string, mode: Mode) => void;
}

export function EmptyState({ episodes, localModel, onPick }: Props) {
  return (
    <section className="empty" aria-labelledby="empty-title">
      <h1 id="empty-title">What do you want to learn from Lenny's guests?</h1>
      <p className="empty-lead">
        Ask a product or growth question and get an answer built only from{" "}
        {episodes ? `${episodes} ` : ""}Lenny's Podcast transcripts, with every claim linked to the episode and
        moment it came from. Turn answers into a Ship 30 essay or a polished one-pager.
      </p>
      <div className="starters">
        {STARTERS.map(({ kind, mode, icon: Icon, text }) => (
          <button key={text} type="button" className="starter" onClick={() => onPick(text, mode)}>
            <span className="starter-kind">
              <Icon size={13} aria-hidden /> {kind}
            </span>
            <span className="starter-text">{text}</span>
          </button>
        ))}
      </div>
      <div className="empty-notes">
        <span>
          <Quote size={13} aria-hidden /> Cites guest, episode and timestamp
        </span>
        <span>
          <ShieldCheck size={13} aria-hidden /> Says so when the transcripts don't cover it
        </span>
        {localModel && (
          <span>
            <Timer size={13} aria-hidden /> Local model: expect ~30–90 s per answer on CPU
          </span>
        )}
      </div>
    </section>
  );
}
