"use strict";
// Agent Relay website: conversations between Claude (claude), Codex and the operator, plus a composer
// that sends the operator's message into a chosen open chat. Polls /api/state; no external assets.

const NAMES = { claude: "Claude", codex: "Codex", human: "You", kimi: "Kimi", all: "Everyone" };
const INITIAL = { claude: "C", codex: "X", human: "S", kimi: "K", all: "*" };
const RECEIPTS_SINCE = 0; // fresh installation
const $ = (id) => document.getElementById(id);
const S = { msgs: new Map(), dels: new Map(), chats: [], threads: [], sel: null, filter: "all",
            q: "", now: Date.now() / 1000, ok: false, lastOk: 0, stick: true, seen: loadSeen() };

function loadSeen() { try { return JSON.parse(localStorage.getItem("ar-seen") || "{}"); } catch (e) { return {}; } }
function saveSeen() { try { localStorage.setItem("ar-seen", JSON.stringify(S.seen)); } catch (e) { /* private mode */ } }
const name = (a) => NAMES[a] || a;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtBody(text) {
  const parts = String(text).split(/```/);
  return parts.map((p, i) => i % 2
    ? "<pre>" + esc(p.replace(/^[a-z]*\n/, "")) + "</pre>"
    : esc(p).replace(/`([^`\n]+)`/g, "<code>$1</code>")).join("");
}
function ago(ts) {
  const s = Math.max(0, S.now - ts);
  if (s < 60) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}
const clock = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

// ---- data -------------------------------------------------------------------------------
function ingest(state) {
  S.now = state.now;
  for (const m of state.messages) S.msgs.set(m.id, m);
  S.dels = new Map();
  for (const d of state.deliveries) {
    if (!S.dels.has(d.msg_id)) S.dels.set(d.msg_id, []);
    S.dels.get(d.msg_id).push(d);
  }
  S.chats = state.chats;
  buildThreads();
}
function rootKey(m) {
  let cur = m, guard = 0;
  while (cur.thread_id && S.msgs.has(cur.thread_id) && guard++ < 60) cur = S.msgs.get(cur.thread_id);
  return cur.thread_id && !S.msgs.has(cur.thread_id) ? cur.thread_id : cur.id;
}
function buildThreads() {
  const groups = new Map();
  for (const m of [...S.msgs.values()].sort((a, b) => a.id - b.id)) {
    const k = rootKey(m);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  S.threads = [...groups.entries()].map(([key, msgs]) => {
    const people = new Set();
    msgs.forEach((m) => { people.add(m.sender); if (m.target !== "all") people.add(m.target); });
    const last = msgs[msgs.length - 1];
    return { key, msgs, people, last, title: msgs[0].subject.replace(/^(re:\s*)+/i, "") };
  }).sort((a, b) => b.last.id - a.last.id);
}

// ---- rendering --------------------------------------------------------------------------
function dots(people) {
  return [...people].filter((p) => INITIAL[p]).map((p) =>
    `<span class="dot ${esc(p)}" title="${esc(name(p))}">${INITIAL[p]}</span>`).join("");
}
function renderThreads() {
  const q = S.q.toLowerCase();
  const list = S.threads.filter((t) => (S.filter === "all" || t.people.has(S.filter)) &&
    (!q || t.msgs.some((m) => (m.subject + " " + m.body).toLowerCase().includes(q))));
  $("threads").innerHTML = list.slice(0, 300).map((t) => {
    const unread = (S.seen[t.key] || 0) < t.last.id && t.last.sender !== "human" && S.sel !== t.key;
    return `<button type="button" class="thread${S.sel === t.key ? " sel" : ""}${unread ? " unread" : ""}" data-key="${t.key}">
      <span class="top"><span class="t">${esc(t.title)}</span><span class="when">${ago(t.last.ts)}</span></span>
      <span class="top"><span class="snip">${esc(name(t.last.sender))}: ${esc(t.last.body.slice(0, 90))}</span><span class="who">${dots(t.people)}</span></span>
    </button>`;
  }).join("") || `<p class="sub">No conversations match.</p>`;
}
function renderChats() {
  $("chats").innerHTML = S.chats.length ? S.chats.map((c) =>
    `<li><span class="live" aria-hidden="true"></span><span class="dot ${esc(c.agent)}">${INITIAL[c.agent]}</span><span class="lbl" title="${esc(c.label)}">${esc(c.label)}</span></li>`).join("")
    : `<li class="none">No chats open right now. Messages can wait in the relay queue for up to 24 hours.</li>`;
}
function deliveryLine(m) {
  if (!(m.target in { claude: 1, codex: 1, all: 1 })) return "";
  const ds = S.dels.get(m.id) || [];
  const bits = ds.map((d) => `<span class="ok">→ in ${esc(name(d.agent))} chat “${esc(d.label)}” · ${Math.max(0, d.ts - m.ts).toFixed(1)} s</span>`);
  if (!ds.length && m.ts >= RECEIPTS_SINCE && m.sender !== m.target) {
    const age = S.now - m.ts;
    bits.push(age < 20 ? `<span>→ delivering…</span>`
      : age < 86400 ? `<span class="wait">→ waiting for an open ${esc(name(m.target))} chat</span>`
      : `<span class="wait">→ no delivery receipt; check relay health</span>`);
  }
  if ((m.kind === "directive" || m.kind === "question") && (m.seen_by || []).includes(m.target)) {
    bits.push(`<span class="ok">✓ completed</span>`);
  }
  return bits.length ? `<div class="meta">${bits.join("")}</div>` : "";
}
function renderMessages() {
  const box = $("messages");
  const t = S.threads.find((x) => x.key === S.sel);
  if (!t) {
    $("title").textContent = "New message";
    $("subtitle").textContent = "Pick a conversation, or write a new message below.";
    box.innerHTML = `<div class="empty"><p>Messages between Claude, Codex and you appear here as they happen.</p>
      <p>Choose who to send to below. “Any open chat” goes to the most recently active one.</p></div>`;
    return;
  }
  $("title").textContent = t.title;
  $("subtitle").textContent = [...t.people].map(name).join(" · ") + ` · ${t.msgs.length} message${t.msgs.length > 1 ? "s" : ""}`;
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.innerHTML = t.msgs.map((m, i) => `
    <article class="msg">
      <div class="avatar ${esc(m.sender)}" aria-hidden="true">${INITIAL[m.sender] || "?"}</div>
      <div>
        <div class="head"><span class="name">${esc(name(m.sender))}</span>
          <span class="arrow">→ ${esc(name(m.target))}</span>
          <span class="pill ${esc(m.kind)}">${esc(m.kind)}</span>
          <span class="time" title="${esc(clock(m.ts))}">#${m.id} · ${esc(clock(m.ts))}</span></div>
        ${i === 0 || !/^re[: ]/i.test(m.subject) ? `<div class="subject">${esc(m.subject)}</div>` : ""}
        <div class="body">${fmtBody(m.body)}</div>
        ${deliveryLine(m)}
      </div>
    </article>`).join("");
  if (near || S.stick) { box.scrollTop = box.scrollHeight; S.stick = false; }
  S.seen[t.key] = t.last.id; saveSeen();
}
function renderTo() {
  const sel = $("to"), keep = sel.value;
  const opt = (v, label) => `<option value="${esc(v)}">${esc(label)}</option>`;
  let html = "";
  for (const agent of ["codex", "claude"]) {
    html += `<optgroup label="${name(agent)}">` + opt(agent + "|", `Any open ${name(agent)} chat`) +
      S.chats.filter((c) => c.agent === agent).map((c) => opt(agent + "|" + c.skey, c.label)).join("") + "</optgroup>";
  }
  sel.innerHTML = html;
  const values = [...sel.options].map((o) => o.value);
  if (values.includes(keep)) sel.value = keep;
}
function defaultTarget() {
  // replying inside a thread: the agent you are talking to, in the chat that last got its mail
  const t = S.threads.find((x) => x.key === S.sel);
  if (!t) return null;
  const agentMsg = [...t.msgs].reverse().find((m) => m.sender === "claude" || m.sender === "codex");
  const agent = agentMsg ? agentMsg.sender : [...t.people].find((p) => p === "claude" || p === "codex");
  if (!agent) return null;
  for (const m of [...t.msgs].reverse()) {
    for (const d of S.dels.get(m.id) || []) {
      if (d.agent === agent && S.chats.some((c) => c.skey === d.skey)) return agent + "|" + d.skey;
    }
  }
  return agent + "|";
}
function render() {
  renderThreads(); renderChats(); renderTo(); renderMessages();
  const st = $("status");
  st.textContent = S.ok ? `Live · ${S.chats.length} open chat${S.chats.length === 1 ? "" : "s"}` : "Bus unreachable · retrying";
  st.className = "status" + (S.ok ? "" : " bad");
}
function select(key) {
  S.sel = key; S.stick = true;
  history.replaceState(null, "", key === null ? location.pathname : "#t=" + key);
  $("app").classList.remove("drawer");
  render();
  const target = defaultTarget();
  if (target) $("to").value = target;
  $("subject").hidden = key !== null;
}

// ---- network ------------------------------------------------------------------------------
const maxId = () => S.msgs.size ? Math.max(...S.msgs.keys()) : 0;
async function poll() {
  try {
    const r = await fetch("/api/state?since=" + maxId(), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    ingest(await r.json());
    S.ok = true; S.lastOk = Date.now();
  } catch (e) { S.ok = false; }
  const link = /^#t=(\d+)$/.exec(location.hash);
  if (!S.linked && link && S.ok) {  // open a bookmarked conversation once the data is in
    S.linked = true;
    const m = S.msgs.get(Number(link[1]));
    select(m ? rootKey(m) : Number(link[1]));
  }
  render();
  setTimeout(poll, document.hidden ? 15000 : 3000);
}
async function send(ev) {
  ev.preventDefault();
  const body = $("body").value.trim();
  if (!body) return;
  const [to, session] = $("to").value.split("|");
  const t = S.threads.find((x) => x.key === S.sel);
  const payload = { to, session, kind: $("kind").value, body,
                    subject: t ? "Re: " + t.title : $("subject").value.trim() };
  if (t) payload.thread_id = t.last.id;
  const msg = $("formMsg"), btn = $("send");
  btn.disabled = true; msg.className = "formmsg"; msg.textContent = "Sending…";
  try {
    const r = await fetch("/api/send", { method: "POST", body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json", "X-Agent-Relay": "send" } });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) throw new Error(data.error || "HTTP " + r.status);
    $("body").value = ""; $("subject").value = "";
    msg.textContent = `Sent as #${data.id}. Delivery depends on an available relay and open chat.`;
    if (!t) { S.pendingSel = data.id; }
    S.stick = true;
    setTimeout(async () => {
      try { ingest(await (await fetch("/api/state?since=" + maxId(), { cache: "no-store" })).json()); } catch (e) { /* next poll */ }
      if (S.pendingSel) { select(rootKeyById(S.pendingSel)); S.pendingSel = null; } else render();
    }, 700);
  } catch (e) {
    msg.className = "formmsg err"; msg.textContent = "Not sent: " + e.message;
  } finally { btn.disabled = false; }
}
function rootKeyById(id) { const m = S.msgs.get(id); return m ? rootKey(m) : null; }

// ---- wiring -------------------------------------------------------------------------------
$("threads").addEventListener("click", (e) => { const b = e.target.closest(".thread"); if (b) select(Number(b.dataset.key)); });
$("newConv").addEventListener("click", () => { select(null); $("body").focus(); });
$("menu").addEventListener("click", () => $("app").classList.toggle("drawer"));
$("search").addEventListener("input", (e) => { S.q = e.target.value; renderThreads(); });
document.querySelector(".filters").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.filter = b.dataset.filter;
  document.querySelectorAll(".filters button").forEach((x) => x.classList.toggle("on", x === b));
  renderThreads();
});
$("composer").addEventListener("submit", send);
$("body").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(e); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { /* next poll picks up */ } });
poll();
