"""Operator CLI.

python -m app.cli ingest            # clone/refresh transcripts and (re)index
python -m app.cli embed [--limit N] # back-fill embeddings (needs EMBEDDINGS_ENABLED)
python -m app.cli search "how do I find product-market fit?"
python -m app.cli status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.observability import configure_logging
from app.retrieval.embeddings import OllamaEmbedder
from app.retrieval.ingest import embed_pending, ingest, kb_status
from app.retrieval.search import Retriever


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, "console")
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        if args.command == "ingest":
            stats = await ingest(factory, settings, trigger="cli", full=args.full)
            print(json.dumps(stats.as_dict(), indent=2))
        elif args.command == "embed":
            n = await embed_pending(factory, settings, limit=args.limit)
            print(f"embedded {n} chunks")
        elif args.command == "status":
            async with factory() as db:
                print(json.dumps(await kb_status(db), indent=2, default=str))
        elif args.command == "search":
            retriever = Retriever(OllamaEmbedder(settings), settings.retrieval_candidate_pool)
            async with factory() as db:
                result = await retriever.search(
                    db, args.query, top_k=args.k, max_per_episode=settings.retrieval_max_per_episode
                )
            print(f"mode={result.mode} latency={result.latency_ms}ms candidates={result.candidates}")
            for i, c in enumerate(result.chunks, 1):
                print(f"\n[S{i}] {c.guest} | {c.title[:70]} @ {c.start_seconds}s  score={c.score:.3f}")
                print("    " + c.text[:300].replace("\n", " ") + " ...")
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("--full", action="store_true", help="re-index every episode")
    sub.add_parser("status")
    embed = sub.add_parser("embed")
    embed.add_argument("--limit", type=int, default=None)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=6)
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
