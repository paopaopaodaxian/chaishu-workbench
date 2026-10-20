/* 拆书工作台：基础工具、路由、侧栏状态 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const arr = a => Array.isArray(a) ? a : [];
const ENGINE_LABEL = {claude: "Claude Code", codex: "Codex"};
const STATUS_CLASS = {done: "ok", partial: "warn", running: "info", pausing: "info", queued: "info", draft: "",
  paused: "warn", waiting_login: "err", waiting_quota: "err", failed: "err", cancelled: ""};
const STATUS_LABEL = {draft: "草稿", queued: "排队中", running: "执行中", pausing: "暂停中", paused: "已暂停",
  waiting_login: "等待登录", waiting_quota: "等待额度", partial: "部分完成", done: "已完成", failed: "失败", cancelled: "已取消"};

const state = {overview: null, timers: [], importSession: null};

async function api(path, opts = {}) {
  const init = {method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {}};
  if (opts.body !== undefined) {
    if (opts.body instanceof ArrayBuffer || opts.body instanceof Blob) init.body = opts.body;
    else { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch("/api" + path, init);
  let j = null;
  try { j = await r.json(); } catch (e) { j = {error: `服务返回了无法识别的内容（HTTP ${r.status}），请查看终端里的报错`}; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (path, body = {}) => api(path, {body});

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(t._t); t._t = setTimeout(() => t.className = "toast", err ? 4200 : 2200);
}
async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); throw e; }
}

function fmtTime(t) {
  if (!t) return "";
  const d = new Date(t * 1000), p = n => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtDur(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `${Math.floor(s / 3600)} 小时 ${Math.floor(s % 3600 / 60)} 分`;
}
function fmtChars(n) { n = n || 0; return n >= 10000 ? (n / 10000).toFixed(1) + " 万字" : n + " 字"; }
function fmtBytes(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB"; }
function pill(status, label) { return `<span class="pill ${STATUS_CLASS[status] || ""}">${h(label || STATUS_LABEL[status] || status)}</span>`; }
function scopeText(s) {
  if (!s) return "";
  return s.type === "first3" ? "前 3 章" : s.type === "all" ? "全书" + (s.exclude_extra ? "（不含番外）" : "") : `指定 ${arr(s.chapter_ids).length} 章`;
}

/* ---------- 弹窗 ---------- */
function modal(html, {wide = false, onClose} = {}) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal ${wide ? "wide" : ""}">${html}</div>`;
  document.body.appendChild(bg);
  const close = () => { bg.remove(); onClose && onClose(); };
  bg.addEventListener("click", e => { if (e.target === bg) close(); });
  $$("[data-close]", bg).forEach(b => b.onclick = close);
  return {el: $(".modal", bg), close};
}
function confirmBox(title, body, okText = "确定", danger = false) {
  return new Promise(res => {
    const m = modal(`<h2>${h(title)}</h2><div>${body}</div>
      <div class="foot"><button data-close>取消</button><button class="${danger ? "primary danger" : "primary"}" id="cb-ok">${h(okText)}</button></div>`,
      {onClose: () => res(false)});
    $("#cb-ok", m.el).onclick = () => { res(true); m.el.parentNode.remove(); };
  });
}
function promptBox(title, label, value = "") {
  return new Promise(res => {
    const m = modal(`<h2>${h(title)}</h2><div class="field"><label>${h(label)}</label><input type="text" id="pb-in" value="${h(value)}"></div>
      <div class="foot"><button data-close>取消</button><button class="primary" id="pb-ok">确定</button></div>`, {onClose: () => res(null)});
    const inp = $("#pb-in", m.el); inp.focus(); inp.select();
    const ok = () => { const v = inp.value.trim(); m.el.parentNode.remove(); res(v || null); };
    $("#pb-ok", m.el).onclick = ok;
    inp.onkeydown = e => { if (e.key === "Enter") ok(); };
  });
}

/* ---------- 路由 ---------- */
const ROUTES = [];
function routeAdd(pattern, fn, nav) { ROUTES.push({rx: new RegExp("^" + pattern + "$"), fn, nav}); }

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/library";
  const [path, qs] = raw.split("?");
  return {path, query: Object.fromEntries(new URLSearchParams(qs || ""))};
}
function go(hash) { location.hash = hash; }

async function render() {
  state.timers.forEach(t => clearInterval(t)); state.timers = [];
  const {path, query} = parseHash();
  const main = $("#main");
  for (const r of ROUTES) {
    const m = path.match(r.rx);
    if (!m) continue;
    $$("#nav a, .side .nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === r.nav));
    try {
      await r.fn(main, ...m.slice(1).map(decodeURIComponent), query);
    } catch (e) {
      main.innerHTML = `<div class="page"><div class="banner err">${h(e.message)}</div></div>`;
      console.error(e);
    }
    return;
  }
  go("#/library");
}
function every(ms, fn) { const t = setInterval(fn, ms); state.timers.push(t); return t; }

/* ---------- 侧栏连接状态 ---------- */
async function refreshOverview() {
  try {
    const o = await api("/overview");
    state.overview = o;
    const n = $("#nav-running");
    n.textContent = o.running; n.classList.toggle("hidden", !o.running);
    $("#conn").innerHTML = Object.entries(o.engines).map(([k, e]) => {
      const cls = !e.installed ? "off" : e.logged_in ? (o.blocked[k] ? "warn" : "on") : "off";
      const txt = !e.installed ? "未安装" : o.blocked[k] ? "已暂停队列" : e.logged_in ? "已连接" : "未连接";
      return `<a href="#/settings"><span class="dot ${cls}"></span>${ENGINE_LABEL[k]} · ${txt}</a>`;
    }).join("") + (o.running ? `<a href="#/tasks?f=active"><span class="dot on"></span>${o.running} 个任务执行中</a>` : "");
  } catch (e) { $("#conn").innerHTML = `<span class="sub">服务未连接</span>`; }
}

async function quitApp() {
  const n = state.overview ? state.overview.running : 0;
  const ok = await confirmBox("关闭工作台", (n ? `<p>有 ${n} 个任务正在执行或排队。关闭后会中断当前请求，下次打开时任务显示为「已暂停：应用中断」，可以继续。</p>` : "") +
    "<p>关闭后台服务。下次使用时双击「拆书工作台」即可重新打开，数据都会保留。</p>", "关闭", !!n);
  if (!ok) return;
  try { await post("/shutdown"); } catch (e) {}
  document.body.innerHTML = `<div style="max-width:520px;margin:18vh auto;text-align:center;font:15px/1.8 -apple-system,'PingFang SC',sans-serif;color:#26231f">
    <h2 style="font-weight:600">拆书工作台已关闭</h2><p style="color:#756d64">可以关闭这个浏览器标签页。<br>下次使用时双击「拆书工作台」重新打开。</p></div>`;
}

function boot() {
  $("#quit-app").onclick = quitApp;
  window.addEventListener("hashchange", render);
  refreshOverview();
  setInterval(refreshOverview, 8000);
  render();
}

/* ---------- 通用片段 ---------- */
function engineCards(engs, selected, {onPick, showWarn = true} = {}) {
  return Object.entries(engs).map(([k, e]) => {
    const ok = e.installed && e.logged_in;
    const acct = e.account || {};
    return `<div class="radio-card ${selected === k ? "on" : ""} ${e.installed ? "" : "disabled"}" data-engine="${k}">
      <div class="row"><span class="dot ${ok ? "on" : "off"}"></span><span class="t">${ENGINE_LABEL[k]}</span>
        <span class="grow"></span><span class="pill ${ok ? "ok" : ""}">${!e.installed ? "未安装" : ok ? "已登录" : "未登录"}</span></div>
      ${e.installed ? `<div class="d">${h(acct.email || "账户未知")}${acct.org ? " · " + h(acct.org) : ""}<br>
        身份：<b style="color:${acct.kind === "请核对工作区" ? "var(--warn)" : "inherit"}">${h(acct.kind || "请核对工作区")}</b></div>` :
        `<div class="d">未检测到 ${k} 命令</div>`}
      ${showWarn ? arr(e.warnings).map(w => `<div class="d" style="color:var(--warn)">${h(w)}</div>`).join("") : ""}
      ${e.env_findings && e.env_findings.length && !e.allow_env ? `<div class="d">已屏蔽环境变量：${h(e.env_findings.join("、"))}</div>` : ""}
    </div>`;
  }).join("");
}
