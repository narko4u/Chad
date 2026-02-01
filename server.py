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

    # Model call (OpenRouter if key set, else local Ollama)
    try:
        reply = (llm_chat(history, temperature=temperature, max_tokens=800) or "").strip()
        if not reply:
            reply = "Understood. What outcome are you aiming for?"
    except Exception as e:
        # If OpenRouter is enabled, this is an upstream LLM error.
        # If OpenRouter is not enabled, this likely means Ollama is unreachable.
        jlog("llm_error", ip=ip, session=session_id, error=str(e))
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")
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
    <div class="tag">Gateway: /api/chat</div>

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
    return DEMO_HTML
