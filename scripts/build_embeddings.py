#!/usr/bin/env python3
"""
Pre-compute FAISS semantic search indexes for Discord messages and OpenClaw turns.

Usage:
    python3 scripts/build_embeddings.py [--source discord|openclaw|all]

Output:
    logs/embeddings/discord.index      FAISS index (cosine similarity)
    logs/embeddings/discord_meta.json  Metadata for each indexed entry
    logs/embeddings/openclaw.index
    logs/embeddings/openclaw_meta.json

Re-run whenever logs change. Incremental updates not supported yet.
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def sanitize(s):
    """Strip surrogate characters that tokenizers can't handle."""
    if not isinstance(s, str):
        return ""
    return s.encode("utf-8", errors="replace").decode("utf-8")
DISCORD_DIR   = ROOT / "logs" / "discord"
SESSIONS_DIR  = ROOT / "website" / "data" / "sessions"
SESSIONS_IDX  = ROOT / "website" / "data" / "sessions_index.json"
EMB_DIR       = ROOT / "logs" / "embeddings"

# Default model — multi-qa variant is tuned for retrieval (query→passage matching)
# Use --model all-mpnet-base-v2 for higher quality (768-dim, 2-3x slower)
DEFAULT_MODEL = "multi-qa-MiniLM-L6-cos-v1"   # 384-dim, fast, retrieval-tuned

# Sliding context window for Discord: embed N consecutive messages together
# (better retrieval for short messages, metadata still points to the key message)
DISCORD_WINDOW = 3   # embed prev 2 + current; use 1 to disable

# Max chars to embed per entry (model token limit ~256-512; 2000 chars is safe)
MAX_EMBED_CHARS = 2000
# Max chars to store in metadata for display
MAX_META_CHARS  = 800


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_discord_docs(window=DISCORD_WINDOW):
    """Load Discord messages with a sliding context window.

    For each message, the embedded text includes up to (window-1) preceding
    messages from the same channel as context.  The metadata still points to
    the key (most recent) message, so search results link to the right place.
    """
    docs = []
    for jf in sorted(DISCORD_DIR.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            print(f"  skip {jf.name}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue  # skip _summary.json etc.
        channel = data.get("channel_name", jf.stem)
        messages = data.get("messages", [])

        for i, msg in enumerate(messages):
            content = (msg.get("content") or "").strip()
            if not content:
                continue

            # Build context window: preceding messages + current
            ctx_parts = []
            for j in range(max(0, i - window + 1), i):
                prev = messages[j]
                prev_text = (prev.get("content") or "").strip()
                prev_auth = (prev.get("author") or {}).get("name", "")
                if prev_text:
                    ctx_parts.append(f"[{prev_auth}]: {prev_text}")
            cur_auth = (msg.get("author") or {}).get("name", "")
            ctx_parts.append(f"[{cur_auth}]: {content}")
            embed_text = "\n".join(ctx_parts)

            meta = {
                "source":     "discord",
                "channel":    channel,
                "message_id": msg.get("id", ""),
                "author":     cur_auth,
                "timestamp":  msg.get("timestamp", ""),
                "text":       content[:MAX_META_CHARS],   # display only key message
            }
            docs.append((sanitize(embed_text)[:MAX_EMBED_CHARS], meta))
    return docs


def load_openclaw_docs():
    docs = []
    if not SESSIONS_IDX.exists():
        print("sessions_index.json not found — run process_openclaw.py first",
              file=sys.stderr)
        return docs

    for sf in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(sf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        sid = data.get("id", sf.stem)
        sess_ts = data.get("timestamp", "")

        for i, turn in enumerate(data.get("turns", [])):
            role = turn.get("role", "")
            parts = []

            t = (turn.get("text") or "").strip()
            if t:
                parts.append(t)

            th = (turn.get("thinking") or "").strip()
            if th:
                parts.append("[thinking] " + th)

            for tc in turn.get("tool_calls", []):
                name = tc.get("name", "")
                args = tc.get("args") or {}
                arg_str = json.dumps(args)[:400] if args else ""
                if arg_str:
                    parts.append(f"[tool:{name}] {arg_str}")

            for tr in turn.get("tool_results", []):
                out = (tr.get("output") or "").strip()
                if out:
                    parts.append(f"[result:{tr.get('tool','')}] {out[:400]}")

            text = "\n".join(parts).strip()
            if not text:
                continue

            meta = {
                "source":     "openclaw",
                "session_id": sid,
                "turn_idx":   i,
                "role":       role,
                "timestamp":  turn.get("ts", sess_ts),
                "text":       text[:MAX_META_CHARS],
            }
            docs.append((sanitize(text)[:MAX_EMBED_CHARS], meta))

    return docs


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_index(docs, name, model_name=None):
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    model_name = model_name or DEFAULT_MODEL
    model = SentenceTransformer(model_name)
    print(f"  Model: {model_name}")
    texts = [d[0] for d in docs]
    metas = [d[1] for d in docs]

    # Ensure every text is a non-empty string (guard against None or other types)
    filtered = [(t, m) for t, m in zip(texts, metas)
                if isinstance(t, str) and t.strip()]
    texts = [t for t, _ in filtered]
    metas = [m for _, m in filtered]

    print(f"Encoding {len(texts):,} {name} entries …", flush=True)
    t0 = time.time()
    batch_size = 512
    all_embs = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        embs = model.encode(chunk, show_progress_bar=False, convert_to_numpy=True)
        all_embs.append(embs)
        pct = min(100, int(100 * (i + len(chunk)) / len(texts)))
        elapsed = time.time() - t0
        print(f"  {pct:3d}%  {i+len(chunk):,}/{len(texts):,}  {elapsed:.0f}s",
              flush=True)

    embs_np = np.vstack(all_embs).astype("float32")

    # Normalize → inner product == cosine similarity
    faiss.normalize_L2(embs_np)
    dim = embs_np.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embs_np)

    EMB_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(EMB_DIR / f"{name}.index"))
    # Sanitize all text fields in metadata before writing
    for m in metas:
        if "text" in m:
            m["text"] = sanitize(m["text"])
    (EMB_DIR / f"{name}_meta.json").write_text(
        json.dumps(metas, ensure_ascii=True), encoding="utf-8"
    )

    elapsed = time.time() - t0
    size_mb = (EMB_DIR / f"{name}.index").stat().st_size / 1e6
    print(f"  → saved {name}: {len(texts):,} entries, dim={dim}, "
          f"index={size_mb:.1f} MB, total {elapsed:.1f}s\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Build semantic search FAISS indexes")
    ap.add_argument("--source", choices=["discord", "openclaw", "all"],
                    default="all", help="Which logs to index (default: all)")
    ap.add_argument("--model", default=None,
                    help=f"Sentence-transformer model name (default: {DEFAULT_MODEL}; "
                         f"try all-mpnet-base-v2 for higher quality)")
    ap.add_argument("--window", type=int, default=DISCORD_WINDOW,
                    help=f"Discord context window size (default: {DISCORD_WINDOW}; 1=no context)")
    args = ap.parse_args()

    if args.source in ("discord", "all"):
        docs = load_discord_docs(window=args.window)
        print(f"Loaded {len(docs):,} Discord messages (window={args.window})")
        if docs:
            build_index(docs, "discord", model_name=args.model)

    if args.source in ("openclaw", "all"):
        docs = load_openclaw_docs()
        print(f"Loaded {len(docs):,} OpenClaw turns")
        if docs:
            build_index(docs, "openclaw", model_name=args.model)


if __name__ == "__main__":
    main()
