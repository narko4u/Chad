(function(){
  const CFG = {
    apiBase: (window.EMPIRELABS_CHAT_API || "http://127.0.0.1:8787").replace(/\/$/,""),
    apiKey: (window.EMPIRELABS_CHAT_KEY || ""),
    title: "Empire Labs Assistant"
  };

  let session_id = localStorage.getItem("empirelabs_chat_session") || "";

  function el(tag, attrs={}, children=[]){
    const n = document.createElement(tag);
    Object.entries(attrs).forEach(([k,v])=>{
      if(k==="class") n.className=v;
      else if(k==="style") n.style.cssText=v;
      else n.setAttribute(k,v);
    });
    children.forEach(c=> n.appendChild(typeof c==="string" ? document.createTextNode(c) : c));
    return n;
  }

  const launcher = el("div",{id:"empirelabs-chat-launcher", title:"Chat"},["AI"]);
  const panel = el("div",{id:"empirelabs-chat-panel"});
  const header = el("div",{id:"empirelabs-chat-header"},[
    el("div",{},[
      el("div",{},[CFG.title]),
      el("div",{class:"empire-tag"},["Ask about services, automation, dashboards, grants/R&D."])
    ]),
    el("div",{style:"display:flex;gap:8px;align-items:center;"},[
      el("button",{id:"empirelabs-chat-close", style:"border:0;background:transparent;color:#e8eefc;font-weight:900;cursor:pointer;"},["✕"])
    ])
  ]);
  const body = el("div",{id:"empirelabs-chat-body"});
  const inputWrap = el("div",{id:"empirelabs-chat-input"},[
    el("input",{id:"empirelabs-chat-text", type:"text", placeholder:"Type your message…"}),
    el("button",{id:"empirelabs-chat-send"},["Send"])
  ]);

  panel.appendChild(header);
  panel.appendChild(body);
  panel.appendChild(inputWrap);
  document.body.appendChild(launcher);
  document.body.appendChild(panel);

  function append(role, text){
    const div = el("div",{class:"empire-msg " + (role==="You" ? "empire-user" : "empire-bot")});
    div.textContent = role + ": " + text;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }

  function toggle(open){
    panel.style.display = open ? "block" : "none";
  }

  launcher.addEventListener("click", ()=> toggle(panel.style.display!=="block"));
  document.getElementById("empirelabs-chat-close").addEventListener("click", ()=> toggle(false));

  const sendBtn = document.getElementById("empirelabs-chat-send");
  const textBox = document.getElementById("empirelabs-chat-text");

  async function send(){
    const msg = (textBox.value || "").trim();
    if(!msg) return;
    textBox.value = "";
    sendBtn.disabled = true;
    append("You", msg);

    try{
      const res = await fetch(CFG.apiBase + "/api/chat", {
        method:"POST",
        headers: Object.assign(
          {"Content-Type":"application/json"},
          CFG.apiKey ? {"x-api-key": CFG.apiKey} : {}
        ),
        body: JSON.stringify({message: msg, session_id})
      });
      const data = await res.json();
      if(!res.ok) throw new Error(data.detail || "Request failed");
      session_id = data.session_id;
      localStorage.setItem("empirelabs_chat_session", session_id);
      append("Bot", data.reply);
    }catch(e){
      append("Error", e.message);
    }finally{
      sendBtn.disabled = false;
      textBox.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  textBox.addEventListener("keydown", (e)=>{ if(e.key==="Enter"){ e.preventDefault(); send(); }});

  append("Bot","Hi — I’m the Empire Labs assistant. What are you trying to build or automate?");
})();