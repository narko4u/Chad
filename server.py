import os
import json
import time
import sqlite3
from typing import List, Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Optional: Chroma (RAG). If not installed or DB missing, we degrade gracefully.
try:
    import chromadb
except Exception:
    chromadb = None  # type: ignore

# -----------------------------
# Config
# -----------------------------
APP_TITLE = "Chad API"

# Local Ollama (dev only)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
MODEL = os.getenv("MODEL", "qwen2.5:7b-instruct").strip()
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.35"))
CHAT_NUM_CTX = int(os.getenv("CHAT_NUM_CTX", "4096"))
MAX_SESSION_MSGS = int(os.getenv("MAX_SESSION_MSGS", "30"))

# OpenRouter (cloud)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1").strip()
OPENROUTER_SITE = os.getenv("OPENROUTER_SITE", "https://empirelabs.com.au").strip()
OPENROUTER_APP = os.getenv("OPENROUTER_APP", "empirelabs-chad").strip()

# Security (optional)
API_KEY = os.getenv("API_KEY", "").strip()
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()

# RAG
RAG_ENABLED = os.getenv("RAG_ENABLED", "1").strip() not in ("0", "false", "False", "")
RAG_DB_PATH = os.getenv("RAG_DB_PATH", "/data/rag_db").strip()
RAG_COLLECTION = os.getenv("RAG_COLLECTION", "empirelabs_kb").strip()
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))

# Sessions
SESSIONS_DB_PATH = os.getenv("SESSIONS_DB_PATH", "/data/sessions.db").strip()

# CORS
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if not CORS_ORIGINS:
    CORS_ORIGINS = ["*"]  # safe for internal demo; lock down in production

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are Chad — Empire Labs’ AI operator. Be concise, practical, and action-oriented."
).strip()

# -----------------------------
# Helpers
# -----------------------------
def _tcp_probe_http(url: str, timeout: float = 1.5) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False

def ollama_ok() -> bool:
    return _tcp_probe_http(f"{OLLAMA_BASE_URL}/api/tags")

def _call_ollama_chat(messages: List[Dict[str, str]], temperature: float, num_ctx: int) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": float(temperature),
            "num_ctx": int(num_ctx),
        },
    }
    try:
        r = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()
        return (data.get("message") or {}).get("content", "") or ""
    except Exception as e:
        raise RuntimeError(f"Ollama error: {e}")

def _call_openrouter(messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    url = f"{OPENROUTER_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Recommended by OpenRouter:
        "HTTP-Referer": OPENROUTER_SITE,
        "X-Title": OPENROUTER_APP,
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
    except Exception as e:
        raise RuntimeError(f"OpenRouter error: {e}")

def llm_chat(messages: List[Dict[str, str]], temperature: float, max_tokens: int = 800, num_ctx: int = 4096) -> str:
    """
    Provider selection:
    - If OPENROUTER_API_KEY is set -> use OpenRouter (Railway-safe).
    - Else -> use Ollama (local dev).
    """
    if OPENROUTER_API_KEY:
        return _call_openrouter(messages, temperature=temperature, max_tokens=max_tokens).strip()
    return _call_ollama_chat(messages, temperature=temperature, num_ctx=num_ctx).strip()

# -----------------------------
# Sessions store
# -----------------------------
def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(SESSIONS_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(SESSIONS_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, messages_json TEXT NOT NULL, updated_at INTEGER NOT NULL)"
    )
    return conn

def load_session(session_id: str) -> List[Dict[str, str]]:
    if not session_id:
        return []
    conn = _db()
    try:
        row = conn.execute("SELECT messages_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return []
        return json.loads(row[0])
    finally:
        conn.close()

def save_session(session_id: str, messages: List[Dict[str, str]]) -> None:
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, messages_json, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages_json=excluded.messages_json, updated_at=excluded.updated_at",
            (session_id, json.dumps(messages), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

def new_session_id() -> str:
    # lightweight random id without extra deps
    return f"s_{int(time.time()*1000)}_{os.urandom(3).hex()}"

# -----------------------------
# RAG (best-effort)
# -----------------------------
def rag_db_ok() -> bool:
    if not (RAG_ENABLED and chromadb):
        return False
    try:
        if not os.path.isdir(RAG_DB_PATH):
            return False
        client = chromadb.PersistentClient(path=RAG_DB_PATH)
        _ = client.get_or_create_collection(RAG_COLLECTION)
        return True
    except Exception:
        return False

def try_get_rag_context(query: str) -> str:
    """
    Best-effort RAG:
    - If Chroma DB is present AND Ollama embeddings are reachable -> embed query + retrieve.
    - Otherwise returns "" (never blocks chat).
    """
    if not (RAG_ENABLED and chromadb):
        return ""
    if not query.strip():
        return ""

    # Query embeddings via Ollama only (local). In Railway, Ollama isn't available.
    if not ollama_ok():
        return ""

    embed_model = os.getenv("EMBED_MODEL", "nomic-embed-text").strip()
    try:
        er = requests.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": embed_model, "prompt": query},
            timeout=60,
        )
        er.raise_for_status()
        embedding = (er.json() or {}).get("embedding")
        if not embedding:
            return ""
        client = chromadb.PersistentClient(path=RAG_DB_PATH)
        col = client.get_or_create_collection(RAG_COLLECTION)
        res = col.query(query_embeddings=[embedding], n_results=RAG_TOP_K, include=["documents", "metadatas"])
        docs = (res.get("documents") or [[]])[0]
        if not docs:
            return ""
        # Keep it compact
        chunks = []
        for d in docs[:RAG_TOP_K]:
            d = (d or "").strip()
            if d:
                chunks.append(d[:1200])
        if not chunks:
            return ""
        return "\n\n---\n\n".join(chunks)
    except Exception:
        return ""

# -----------------------------
# API
# -----------------------------
class ChatIn(BaseModel):
    message: str
    session_id: Optional[str] = ""

class ChatOut(BaseModel):
    session_id: str
    reply: str

app = FastAPI(title=APP_TITLE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "provider": ("openrouter" if OPENROUTER_API_KEY else "ollama"),
        "openrouter_ok": bool(OPENROUTER_API_KEY),
        "ollama_ok": ollama_ok(),
        "model": (OPENROUTER_MODEL if OPENROUTER_API_KEY else MODEL),
        "embed_model": os.getenv("EMBED_MODEL", "nomic-embed-text").strip(),
        "rag_enabled": RAG_ENABLED,
        "rag_db_ok": rag_db_ok(),
        "rag_collection": RAG_COLLECTION,
        "time": int(time.time()),
    }

def _check_api_key(headers: Dict[str, str]) -> None:
    if not API_KEY:
        return
    got = (headers.get("x-api-key") or "").strip()
    if got != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/api/chat", response_model=ChatOut)
def api_chat(payload: ChatIn):
    # Note: FastAPI doesn't pass headers to dependency-free function easily; keep open by default.
    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Missing 'message'")

    sid = (payload.session_id or "").strip()
    if not sid:
        sid = new_session_id()

    history = load_session(sid)
    # Keep only last N (excluding system)
    history = [m for m in history if m.get("role") in ("user", "assistant")]
    history = history[-MAX_SESSION_MSGS:]

    # RAG context (best-effort)
    ctx = try_get_rag_context(msg)
    sys = SYSTEM_PROMPT
    if ctx:
        sys = f"{SYSTEM_PROMPT}\n\nUse the following context if relevant:\n\n{ctx}"

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys}] + history + [{"role": "user", "content": msg}]

    try:
        reply = llm_chat(messages, temperature=CHAT_TEMPERATURE, max_tokens=800, num_ctx=CHAT_NUM_CTX)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # Persist
    new_hist = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
    new_hist = new_hist[-MAX_SESSION_MSGS:]
    save_session(sid, new_hist)

    return ChatOut(session_id=sid, reply=reply)

@app.get("/demo", response_class=HTMLResponse)
def demo():
    # Important: use relative gateway so Railway works
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Empire Labs — Chad Demo</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto; background:#0b1220; color:#e5e7eb; margin:0; }}
    .wrap {{ max-width: 920px; margin: 32px auto; padding: 0 16px; }}
    .card {{ background:#0f172a; border:1px solid #1f2937; border-radius:16px; padding:20px; }}
    h1 {{ margin: 0 0 12px; font-size: 28px; }}
    .muted {{ color:#9ca3af; font-size: 13px; }}
    textarea {{ width:100%; min-height:120px; padding:14px; background:#0b1220; color:#e5e7eb; border:1px solid #1f2937; border-radius:12px; resize: vertical; }}
    button {{ background:#22c55e; border:0; padding:10px 16px; border-radius:12px; cursor:pointer; font-weight:600; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Empire Labs — Chat Demo</h1>
    <div class="muted">Gateway: <span id="gw">/api/chat</span></div>
    <div style="height:12px"></div>
    <div class="card">
      <pre id="log"></pre>
      <textarea id="msg" placeholder="Ask Chad about Empire Labs (services, automation, dashboards, grants/R&D)..."></textarea>
      <div style="height:10px"></div>
      <button onclick="send()">Send</button>
    </div>
  </div>
<script>
let session_id = "";
const log = document.getElementById("log");
function line(s){{ log.textContent += s + "\\n\\n"; }}
async function send(){{
  const message = document.getElementById("msg").value.trim();
  if(!message) return;
  line("You: " + message);
  document.getElementById("msg").value="";
  try {{
    const res = await fetch("/api/chat", {{
      method:"POST",
      headers:{{"Content-Type":"application/json"}},
      body: JSON.stringify({{message, session_id}})
    }});
    const data = await res.json();
    if(!res.ok) {{
      line("Error: " + (data.detail || res.status));
      return;
    }}
    session_id = data.session_id || session_id;
    line("Chad: " + data.reply);
  }} catch(e) {{
    line("Error: " + e);
  }}
}}
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)

# Railway / Procfile typically runs: uvicorn server:app --host 0.0.0.0 --port $PORT
