import os
from typing import List, Dict

import chromadb
from chromadb.config import Settings
import requests

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
RAG_DB_DIR  = os.getenv("RAG_DB_DIR", os.path.join(os.path.dirname(__file__), "..", "rag_db"))
COLLECTION  = os.getenv("RAG_COLLECTION", "empirelabs_kb")

def _embed(texts: List[str]) -> List[List[float]]:
    # Ollama embeddings endpoint
    out = []
    for t in texts:
        r = requests.post(f"{OLLAMA_BASE}/api/embeddings", json={"model": EMBED_MODEL, "prompt": t}, timeout=120)
        r.raise_for_status()
        out.append(r.json()["embedding"])
    return out

def _client():
    return chromadb.PersistentClient(
        path=os.path.abspath(RAG_DB_DIR),
        settings=Settings(anonymized_telemetry=False)
    )

def retrieve(query: str, k: int = 6) -> List[Dict]:
    """
    Returns list of chunks: {id, text, source}
    """
    c = _client()
    col = c.get_or_create_collection(name=COLLECTION)
    q_emb = _embed([query])[0]
    res = col.query(query_embeddings=[q_emb], n_results=k, include=["documents","metadatas","ids","distances"])
    items = []
    ids = res.get("ids",[[]])[0]
    docs = res.get("documents",[[]])[0]
    metas = res.get("metadatas",[[]])[0]
    for i in range(len(ids)):
        items.append({
            "id": ids[i],
            "text": docs[i],
            "source": (metas[i] or {}).get("source","kb")
        })
    return items

def format_context_pack(items: List[Dict]) -> str:
    if not items:
        return "No relevant internal knowledge found."
    lines = []
    for it in items:
        lines.append(f"[source: {it.get('source','kb')}]")
        lines.append(it["text"].strip())
        lines.append("")
    return "\n".join(lines).strip()
