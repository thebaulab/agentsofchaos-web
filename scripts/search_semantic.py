#!/usr/bin/env python3
"""
Semantic search over Discord messages and OpenClaw sessions using FAISS.

Build the indexes first:
    python3 scripts/build_embeddings.py

Usage:
    python3 scripts/search_semantic.py "query text" [options]

Options:
    --source discord|openclaw|all   Which index to search (default: all)
    --top-k N                       Number of results to return (default: 10)
    --min-score F                   Minimum cosine similarity threshold 0..1 (default: 0.3)
    --role user|assistant|toolResult  Filter OpenClaw results by role
    --channel NAME                  Filter Discord results by channel name (substring)
    --date-from YYYY-MM-DD          Filter by date
    --date-to   YYYY-MM-DD

Examples:
    python3 scripts/search_semantic.py "agent deletes files to protect a secret"
    python3 scripts/search_semantic.py "identity verification discord user ID" --source discord
    python3 scripts/search_semantic.py "antisemitic threat Moltbook" --source openclaw --top-k 20
"""
import argparse
import json
import pathlib
import sys

ROOT    = pathlib.Path(__file__).resolve().parent.parent
EMB_DIR = ROOT / "logs" / "embeddings"
MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def load_index(name):
    import faiss
    idx_path  = EMB_DIR / f"{name}.index"
    meta_path = EMB_DIR / f"{name}_meta.json"
    if not idx_path.exists():
        print(f"[!] No index found for '{name}'. "
              f"Run: python3 scripts/build_embeddings.py --source {name}",
              file=sys.stderr)
        return None, None
    index = faiss.read_index(str(idx_path))
    metas = json.loads(meta_path.read_text(encoding="utf-8"))
    return index, metas


def search(query, source="all", top_k=10, min_score=0.3,
           role=None, channel=None, date_from=None, date_to=None):
    import numpy as np

    model = get_model()
    q_emb = model.encode([query], convert_to_numpy=True).astype("float32")

    # faiss.normalize_L2 requires a writable array
    q_emb = np.ascontiguousarray(q_emb)
    import faiss
    faiss.normalize_L2(q_emb)

    # We may need more raw hits to survive post-filtering
    fetch_k = max(top_k * 10, 100)

    sources = ["discord", "openclaw"] if source == "all" else [source]
    results = []

    for src in sources:
        index, metas = load_index(src)
        if index is None:
            continue

        k = min(fetch_k, index.ntotal)
        scores, idxs = index.search(q_emb, k)

        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0 or float(score) < min_score:
                continue
            meta = metas[idx]

            # Post-filters
            ts = meta.get("timestamp", "")
            if date_from and ts[:10] < date_from:
                continue
            if date_to and ts[:10] > date_to:
                continue
            if src == "discord" and channel:
                if channel.lower() not in meta.get("channel", "").lower():
                    continue
            if src == "openclaw" and role:
                if meta.get("role", "") != role:
                    continue

            results.append((float(score), meta))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


def print_results(results, query):
    if not results:
        print("No results found.")
        return

    print(f"\n{'─'*72}")
    print(f"Query: {query!r}  ({len(results)} result{'s' if len(results)!=1 else ''})")
    print(f"{'─'*72}")

    for i, (score, meta) in enumerate(results):
        src = meta["source"]
        ts  = (meta.get("timestamp") or "")[:16]
        bar = "█" * int(score * 20)

        if src == "discord":
            header = (f"[{i+1:2d}] score={score:.3f} {bar}\n"
                      f"     discord  #{meta['channel']}  "
                      f"{meta['author']}  {ts}")
            anchor = f"  → website/logs.html#msg-{meta['message_id']}"
        else:
            sid   = meta.get("session_id", "")[:8]
            tidx  = meta.get("turn_idx", "")
            header = (f"[{i+1:2d}] score={score:.3f} {bar}\n"
                      f"     openclaw  sess={sid}  turn={tidx}  "
                      f"role={meta.get('role','')}  {ts}")
            anchor = (f"  → website/sessions.html"
                      f"#sess-{meta['session_id']}  (turn {tidx})")

        print(f"\n{header}")
        print(anchor)

        # Print text with indent, trimmed
        text = (meta.get("text") or "").strip()
        lines = text.splitlines()
        for line in lines[:12]:
            print(f"    {line}")
        if len(lines) > 12:
            print(f"    … ({len(lines)-12} more lines)")

    print(f"\n{'─'*72}")


def main():
    ap = argparse.ArgumentParser(
        description="Semantic search over Discord and OpenClaw logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else ""
    )
    ap.add_argument("query", help="Natural-language query")
    ap.add_argument("--source", choices=["discord", "openclaw", "all"],
                    default="all")
    ap.add_argument("--top-k",     type=int,   default=10,
                    metavar="N",   help="Number of results (default 10)")
    ap.add_argument("--min-score", type=float, default=0.3,
                    metavar="F",   help="Min cosine similarity 0..1 (default 0.3)")
    ap.add_argument("--role",      metavar="ROLE",
                    help="Filter OpenClaw by role (user|assistant|toolResult)")
    ap.add_argument("--channel",   metavar="NAME",
                    help="Filter Discord by channel name (substring)")
    ap.add_argument("--date-from", metavar="YYYY-MM-DD")
    ap.add_argument("--date-to",   metavar="YYYY-MM-DD")
    args = ap.parse_args()

    results = search(
        args.query,
        source     = args.source,
        top_k      = args.top_k,
        min_score  = args.min_score,
        role       = args.role,
        channel    = args.channel,
        date_from  = args.date_from,
        date_to    = args.date_to,
    )
    print_results(results, args.query)


if __name__ == "__main__":
    main()
