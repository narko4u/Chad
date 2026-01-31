import os, glob, hashlib
from pathlib import Path
from typing import List, Tuple

import chromadb
from chromadb.config import Settings
import requests

ROOT = Path(__file__).resolve().parent
KB_DIR = Path(os.getenv("KB_DIR", str(ROOT / "kb"))).resolve()
RAG_DB_DIR = Path(os.getenv("RAG_DB_DIR", str(ROOT / "rag_db"))).resolve()
COLLECTION = os.getenv("RAG_COLLECTION", "empirelabs_kb")

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

def embed(text: str) -> List[float]:
    r = requests.post(f"{OLLAMA_BASE}/api/embeddings", json={"model": EMBED_MODEL, "prompt": text}, timeout=120)
    r.raise_for_status()
    return r.json()["embedding"]

def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i:i+max_chars]
        chunks.append(chunk)
        i += max_chars - overlap
    return chunks

def read_files() -> List[Tuple[str,str]]:
    files = []
    for p in glob.glob(str(KB_DIR / "**" / "*.md"), recursive=True):
        files.append(p)
    for p in glob.glob(str(KB_DIR / "**" / "*.txt"), recursive=True):
        files.append(p)

    out = []
    for f in files:
        try:
            t = Path(f).read_text(encoding="utf-8", errors="ignore")
            out.append((f, t))
        except Exception:
            pass
    return out

def client():
    return chromadb.PersistentClient(
        path=str(RAG_DB_DIR),
        settings=Settings(anonymized_telemetry=False)
    )

def main():
    KB_DIR.mkdir(parents=True, exist_ok=True)
    RAG_DB_DIR.mkdir(parents=True, exist_ok=True)

    c = client()
    col = c.get_or_create_collection(name=COLLECTION)

    docs = read_files()
    if not docs:
        print(f"No kb files found in {KB_DIR}. Add .md/.txt then rerun.")
        return

    to_add_ids = []
    to_add_docs = []
    to_add_metas = []
    to_add_embs = []

    for src, text in docs:
        for idx, chunk in enumerate(chunk_text(text)):
            hid = hashlib.sha1((src + str(idx) + chunk[:50]).encode("utf-8", errors="ignore")).hexdigest()
            # skip if exists
            try:
                got = col.get(ids=[hid])
                if got and got.get("ids"):
                    continue
            except Exception:
                pass

            to_add_ids.append(hid)
            to_add_docs.append(chunk)
            to_add_metas.append({"source": src})
            to_add_embs.append(embed(chunk))

    if not to_add_ids:
        print("Nothing new to add. Index already up to date.")
        return

    col.add(ids=to_add_ids, documents=to_add_docs, metadatas=to_add_metas, embeddings=to_add_embs)
    print(f"Added {len(to_add_ids)} chunks to {COLLECTION}. DB: {RAG_DB_DIR}")

if __name__ == "__main__":
    main()
