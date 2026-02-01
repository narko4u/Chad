import os

# --- LLM Provider (Ollama local OR OpenRouter in cloud) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
OPENROUTER_BASE    = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1").strip()
OPENROUTER_SITE    = os.getenv("OPENROUTER_SITE", "https://empirelabs.com.au").strip()
OPENROUTER_APP     = os.getenv("OPENROUTER_APP", "empirelabs-chad").strip()

def _call_openrouter(messages, temperature=0.2, max_tokens=800):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    url = f"{OPENROUTER_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional but recommended by OpenRouter:
        "HTTP-Referer": OPENROUTER_SITE,
        "X-Title": OPENROUTER_APP,
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
import time
import uuid
import json
import sqlite3
import logging
from typing import Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

# Optional deps for RAG
try:
    import chromadb
except Exception:
    chromadb = None

APP_NAME = "Empire Labs — Chad Gateway"

# ----------- ENV (safe names, no Windows collisions) -----------
OLLAMA_BASE = (os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434") or "").rstrip("/")
MODEL = os.getenv("MODEL", "qwen2.5:7b-instruct")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# Avoid Windows env collisions: DO NOT use TEMP/PORT generic names in-app logic
CHAD_TEMP = os.getenv("CHAD_TEMP", "0.4")
CHAD_NUM_CTX = os.getenv("CHAD_NUM_CTX", "4096")
CHAD_MAX_SESSION_MSGS = os.getenv("CHAD_MAX_SESSION_MSGS", "30")

# Request hardening
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "12000"))  # ~12KB
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))     # per IP per minute

# Sessions
SESSIONS_DB = os.getenv("SESSIONS_DB_PATH", "sessions.db")

# RAG persistence
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
RAG_DB_PATH = os.getenv("RAG_DB_PATH", "rag_db")
RAG_COLLECTION = os.getenv("RAG_COLLECTION", "empirelabs_kb")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))

# Security
API_KEY = (os.getenv("API_KEY", "") or "").strip()          # for /api/chat
ADMIN_KEY = (os.getenv("ADMIN_KEY", "") or "").strip()      # for debug sources toggle

# CORS
origins = [o.strip() for o in (os.getenv("CORS_ORIGINS", "*") or "*").split(",") if o.strip()]

# Logging (structured-ish JSON)
LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(message)s")
log = logging.getLogger("chad")

def jlog(event: str, **fields):
    payload = {"ts": int(time.time()), "event": event, **fields}
    log.info(json.dumps(payload, ensure_ascii=False))

SYSTEM_PROMPT = (os.getenv("SYSTEM_PROMPT", """
You are Chad — Empire Labs’ AI operator.

Non-negotiables (follow exactly)
- Your name is Chad.
- If asked “Who are you?” or “What is your name?” you MUST reply exactly:
  “I’m Chad — Empire Labs’ AI operator.”
- Never call yourself “Assistant”, “ChatGPT”, “an AI model”, or any other name.

Identity
- You represent Empire Labs Pty Ltd (Australia).
- Voice: senior technical operator; calm authority; concise; high trust.

What Empire Labs delivers
1) Autonomous operations systems (agents + orchestration)
2) Ops dashboards & control planes (local-first, cloud-ready)
3) Compliance-ready workflows (audit trails, evidence packs)
4) Grant-aligned R&D support (scope, validation, documentation)

Operating rules
- Be crisp and structured. Avoid hype.
- Ask at most ONE clarifying question when needed.
- Never invent case studies, guarantees, pricing, legal advice, or credentials.
- When helpful, offer a next step:
  “Book a 30-minute build-plan call” or “Email contact@empirelabs.com.au”.
""") or "").strip()

# ------------------- FastAPI App -------------------

def llm_chat(messages, temperature=0.2, max_tokens=800):
    """
    Uses OpenRouter if OPENROUTER_API_KEY is set; otherwise uses local Ollama.
    """
    if OPENROUTER_API_KEY:
        return _call_openrouter(messages, temperature=temperature, max_tokens=max_tokens)
    # Fallback: keep your existing Ollama path
    return ollama_chat(messages, temperature=temperature, max_tokens=max_tokens)

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------- Rate Limiter -------------------
_rate: Dict[str, List[float]] = {}

def rate_limit(ip: str):
    now = time.time()
    window = 60.0
    bucket = _rate.get(ip, [])
    bucket = [t for t in bucket if (now - t) <= window]
    if len(bucket) >= RATE_LIMIT_RPM:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    bucket.append(now)
    _rate[ip] = bucket

# ------------------- Auth -------------------
def require_api_key(req: Request):
    if not API_KEY:
        return
    got = (req.headers.get("x-api-key", "") or "").strip()
    if got != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

def is_admin_debug(req: Request) -> bool:
    # Debug sources only if ADMIN_KEY set AND provided correctly
    if not ADMIN_KEY:
        return False
    got = (req.headers.get("x-admin-key", "") or "").strip()
    if got != ADMIN_KEY:
        return False
    flag = (req.headers.get("x-debug-sources", "") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")

# ------------------- SQLite Sessions -------------------
def db_init():
    con = sqlite3.connect(SESSIONS_DB)
    try:
        con.execute("""
          CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at INTEGER NOT NULL
          )
        """)
        con.commit()
    finally:
        con.close()

def db_get(session_id: str) -> Optional[List[dict]]:
    con = sqlite3.connect(SESSIONS_DB)
    try:
        cur = con.execute("SELECT data FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row[0])
    finally:
        con.close()

def db_set(session_id: str, history: List[dict]):
    con = sqlite3.connect(SESSIONS_DB)
    try:
        con.execute(
            "INSERT INTO sessions(session_id, data, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (session_id, json.dumps(history, ensure_ascii=False), int(time.time()))
        )
        con.commit()
    finally:
        con.close()

db_init()

# ------------------- Identity Guard -------------------
def _identity_trigger(user_text: str) -> bool:
    t = (user_text or "").strip().lower()
    triggers = [
        "your name", "what is your name", "whats your name", "who are you",
        "what are you called", "what's your name", "introduce yourself",
        "what is ur name", "what's ur name"
    ]
    return any(x in t for x in triggers)

CHAD_IDENTITY_EXACT = "I’m Chad — Empire Labs’ AI operator."

# ------------------- RAG -------------------
_rag_client = None
_rag_collection = None

def rag_init() -> Tuple[bool, str]:
    global _rag_client, _rag_collection
    if not RAG_ENABLED:
        return False, "disabled"
    if chromadb is None:
        return False, "chromadb not installed"
    try:
        _rag_client = chromadb.PersistentClient(path=RAG_DB_PATH)
        _rag_collection = _rag_client.get_or_create_collection(name=RAG_COLLECTION)
        return True, "ok"
    except Exception as e:
        _rag_client = None
        _rag_collection = None
        return False, f"init failed: {e}"

RAG_OK, RAG_STATUS = rag_init()

def ollama_embed(text: str) -> List[float]:
    r = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    r.raise_for_status()
    data = r.json()
    emb = data.get("embedding")
    if not emb:
        raise RuntimeError("No embedding returned")
    return emb

def rag_retrieve(query: str) -> Tuple[str, List[str]]:
    """
    Returns (context_text, sources)
    sources are strings suitable for debug display.
    """
    if not (RAG_ENABLED and _rag_collection):
        return "", []
    try:
        qemb = ollama_embed(query)
        res = _rag_collection.query(
            query_embeddings=[qemb],
            n_results=RAG_TOP_K,
            include=["documents", "metadatas", "ids"]
        )
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]

        context_parts = []
        sources = []

        for i, doc in enumerate(docs):
            if not doc:
                continue
            meta = metas[i] if i < len(metas) else {}
            sid = ids[i] if i < len(ids) else "chunk"
            src = meta.get("source") or meta.get("path") or meta.get("url") or "kb"
            sources.append(f"[KB:{src}]#{sid}")
            context_parts.append(doc.strip())

        context = "\n\n---\n\n".join(context_parts).strip()
        return context, sources
    except Exception as e:
        jlog("rag_error", error=str(e))
        return "", []

# ------------------- Routes -------------------
@app.get("/")
def root():
    return RedirectResponse(url="/demo")

@app.get("/health")
def health():
    ollama_ok = False
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        ollama_ok = (r.status_code == 200)
    except Exception:
        ollama_ok = False

    rag_db_ok = False
    if RAG_ENABLED and os.path.isdir(RAG_DB_PATH):
        rag_db_ok = True

    return {
        "ok": True,
        "ollama_ok": ollama_ok,
        "model": MODEL,
        "embed_model": EMBED_MODEL,
        "rag_enabled": RAG_ENABLED,
        "rag_db_ok": rag_db_ok,
        "rag_collection": RAG_COLLECTION,
        "time": int(time.time()),
    }

@app.post("/api/chat")
async def chat(req: Request):
    require_api_key(req)

    ip = (req.client.host if req.client else "unknown")
    rate_limit(ip)

    # body size cap
    raw = await req.body()
    if raw is None:
        raise HTTPException(status_code=400, detail="Missing body")
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large")

    try:
        body = json.loads(raw.decode("utf-8", errors="strict"))
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be valid JSON")

    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing 'message'")

    session_id = (body.get("session_id") or "").strip() or str(uuid.uuid4())
    debug_sources = is_admin_debug(req)

    # Load session history
    history = db_get(session_id)
    if not history:
        history = [{"role": "system", "content": SYSTEM_PROMPT}]

    # If user asks identity/name: hard override reply, no model call required
    if _identity_trigger(message):
        reply = CHAD_IDENTITY_EXACT
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        max_msgs = int(CHAD_MAX_SESSION_MSGS)
        trimmed = [history[0]] + history[-max_msgs:]
        db_set(session_id, trimmed)
        jlog("chat_identity", ip=ip, session=session_id)
        return {"session_id": session_id, "reply": reply}

    # RAG context
    rag_context = ""
    rag_sources: List[str] = []
    if RAG_ENABLED:
        rag_context, rag_sources = rag_retrieve(message)

    if rag_context:
        history.append({"role": "system", "content": "Context (Empire Labs knowledge base):\n" + rag_context})

    history.append({"role": "user", "content": message})

    # Model call
    try:
        temperature = float(CHAD_TEMP)
    except Exception:
        temperature = 0.4

    try:
        num_ctx = int(CHAD_NUM_CTX)
    except Exception:
        num_ctx = 4096

    payload = {
        "model": MODEL,
        "messages": history,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx}
    }

    try:
        r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        reply = ((data.get("message") or {}).get("content") or "").strip()
        if not reply:
            reply = "Understood. What outcome are you aiming for?"
    except requests.RequestException as e:
        jlog("ollama_error", ip=ip, session=session_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"Ollama error: {str(e)}")
    except Exception as e:
        jlog("server_error", ip=ip, session=session_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

    history.append({"role": "assistant", "content": reply})

    max_msgs = int(CHAD_MAX_SESSION_MSGS)
    trimmed = [history[0]] + history[-max_msgs:]
    db_set(session_id, trimmed)

    resp = {"session_id": session_id, "reply": reply}
    if debug_sources and rag_sources:
        resp["sources"] = rag_sources
    return resp

@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    jlog("unhandled_exception", error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error", "error": str(exc)})

# ---- DEMO HTML (NOT an f-string; braces are safe) ----
DEMO_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Empire Labs Chat Demo</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#0b1220; color:#e8eefc; margin:0; }
    .wrap { max-width:980px; margin:0 auto; padding:24px; }
    .card { background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:16px; padding:16px; }
    textarea { width:100%; height:90px; border-radius:12px; border:1px solid rgba(255,255,255,0.15); background:rgba(0,0,0,0.35); color:#e8eefc; padding:12px; }
    button { padding:10px 14px; border-radius:12px; border:0; background:#2dd4bf; color:#052018; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.6; cursor:not-allowed; }
    .row { display:flex; gap:12px; align-items:center; margin-top:12px; flex-wrap:wrap; }
    .log { white-space:pre-wrap; line-height:1.35; }
    .tag { font-size:12px; opacity:.8; }
    .pill { border:1px solid rgba(255,255,255,0.15); border-radius:999px; padding:6px 10px; font-size:12px; opacity:.9; }
    .hidden { display:none; }
    input[type="password"] { border-radius:10px; border:1px solid rgba(255,255,255,0.15); background:rgba(0,0,0,0.35); color:#e8eefc; padding:8px 10px; }
    label { user-select:none; }
  </style>
</head>
<body>
  <div class="wrap">
    <h2>Empire Labs – Local Chat Demo</h2>
    <div class="tag">Gateway: http://__HOST__:__PORT__/api/chat</div>

    <div class="row hidden" id="adminRow">
      <span class="pill">Admin</span>
      <label class="tag"><input id="dbg" type="checkbox"/> Debug sources</label>
      <input id="adminkey" type="password" placeholder="ADMIN_KEY (hidden)" size="28"/>
      <span class="tag">Enable admin UI via DevTools: <code>localStorage.setItem('chad_admin_enabled','1')</code></span>
    </div>

    <div class="card">
      <div id="out" class="log"></div>
      <div class="row">
        <textarea id="msg" placeholder="Ask Chad about Empire Labs (services, automation, dashboards, grants/R&D)…"></textarea>
      </div>
      <div class="row">
        <button id="send">Send</button>
        <span id="status" class="tag"></span>
      </div>
    </div>
  </div>

<script>
let session_id = localStorage.getItem("empirelabs_session") || "";
const out = document.getElementById("out");
const msg = document.getElementById("msg");
const send = document.getElementById("send");
const status = document.getElementById("status");

const adminRow = document.getElementById("adminRow");
const dbg = document.getElementById("dbg");
const adminkey = document.getElementById("adminkey");

// Admin-only UI visibility toggle
if(localStorage.getItem("chad_admin_enabled") === "1"){
  adminRow.classList.remove("hidden");
}

function append(role, text){
  out.textContent += "\n\n" + role + ": " + text;
  out.scrollTop = out.scrollHeight;
}

async function readJsonSafe(res){
  const txt = await res.text();
  try { return JSON.parse(txt); }
  catch(e){ return { detail: txt || ("HTTP " + res.status) }; }
}

send.onclick = async () => {
  const text = (msg.value || "").trim();
  if(!text) return;

  msg.value = "";
  send.disabled = true;
  status.textContent = "Thinking…";
  append("You", text);

  try {
    const headers = {"Content-Type":"application/json"};
    if(dbg && dbg.checked){
      headers["x-debug-sources"] = "1";
      if(adminkey && adminkey.value) headers["x-admin-key"] = adminkey.value;
    }

    const res = await fetch("/api/chat", {
      method:"POST",
      headers,
      body: JSON.stringify({ message: text, session_id })
    });

    const data = await readJsonSafe(res);
    if(!res.ok) throw new Error(data.detail || "Request failed");

    session_id = data.session_id;
    localStorage.setItem("empirelabs_session", session_id);

    append("Chad", data.reply);

    // Only show sources if admin debug is enabled AND server returns them
    if(data.sources && Array.isArray(data.sources) && data.sources.length){
      append("Sources", data.sources.join("\n"));
    }

  } catch(e) {
    append("Error", e.message);
  } finally {
    status.textContent = "";
    send.disabled = false;
  }
};
</script>
</body>
</html>
"""

@app.get("/demo", response_class=HTMLResponse)
def demo():
    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", "8787")
    html = DEMO_HTML.replace("__HOST__", host).replace("__PORT__", str(port))
    return html

