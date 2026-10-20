/* P04 新建分析 · P05 任务中心与任务详情 · P06 分析阅读器 */
"use strict";

/* ================================================================ P04 新建分析 */
routeAdd("/book/(\\d+)/new", async (main, bid, q) => {
  const [b, tpls] = await Promise.all([api(`/books/${bid}`), api("/templates")]);
  if (b.version_status !== "confirmed") { go(`#/book/${bid}/directory`); return; }
  const V = await api(`/versions/${b.current_version}`);
  let draft = null;
  if (q.draft) draft = (await api(`/jobs/${q.draft}`)).job;
  const F = {
    scope: draft ? draft.scope : q.scope === "chapters" ? {type: "chapters", chapter_ids: (q.ids || "").split(",").filter(Boolean).map(Number)} : null,
    tv: draft ? draft.template_version_id : tpls[0].latest,
    engine: draft ? draft.engine : null, model: draft ? draft.model : "",
    options: draft ? draft.options : {detail: "standard", context: 3, retries: 2},
  };
  if (!F.scope) {
    const t = tpls.find(t => t.latest === F.tv);
    F.scope = {type: t && t.default_scope === "chapters" ? "chapters" : t && t.default_scope === "all" ? "all" : "first3", chapter_ids: []};
  }
  main.innerHTML = `<div class="page" style="max-width:980px">
    <div class="crumb"><a href="#/library">书库</a> / <a href="#/book/${bid}">${h(b.title)}</a> / 新建分析</div>
    <div class="head"><div><h1>新建分析</h1><div class="meta">《${h(b.title)}》 · 正文版本 v${b.vno} · ${b.n_chapters} 章 · ${fmtChars(b.n_chars)}</div></div></div>
    <div class="card"><div class="step"><div class="n">1</div><div class="body"><h3>分析范围</h3>
      <div class="radio-cards" id="na-scope">
        <div class="radio-card" data-s="first3"><div class="t">前三章（默认）</div><div class="d">研究开篇：故事承诺、人物目标、首次冲突、章尾悬念</div></div>
        <div class="radio-card" data-s="chapters"><div class="t">指定章节</div><div class="d">勾选要分析的章节</div></div>
        <div class="radio-card" data-s="all"><div class="t">全书</div><div class="d">逐章分析后生成全书结构汇总</div></div>
      </div>
      <div id="na-scope-detail" style="margin-top:12px"></div></div></div></div>
    <div class="card"><div class="step"><div class="n">2</div><div class="body"><h3>拆解方法</h3>
      <div class="radio-cards" id="na-tpl">${tpls.map(t => `<div class="radio-card" data-tv="${t.latest}"><div class="t">${h(t.name)}
        <span class="small faint">v${t.version}${t.builtin ? " · 内置" : ""}</span></div><div class="d">${h(t.scene)}</div></div>`).join("")}</div>
      <div id="na-tpl-q" style="margin-top:12px"></div></div></div></div>
    <div class="card"><div class="step"><div class="n">3</div><div class="body"><h3>模型连接</h3>
      <div id="na-eng" class="radio-cards"><div class="sub small">正在读取连接状态…</div></div>
      <div class="row" style="margin-top:12px"><div class="field grow" style="margin:0"><label>模型（留空使用 CLI 默认模型）</label>
        <input type="text" id="na-model" list="na-models" value="${h(F.model)}"><datalist id="na-models"></datalist></div></div>
      <div class="small faint" style="margin-top:6px">额度信息暂不可用：是否有可用额度以服务方返回为准。调用量只能估算，不保证订阅额度足够。</div>
      </div></div></div>
    <div class="card"><div class="step"><div class="n">4</div><div class="body"><h3>执行预览</h3><div id="na-prev" class="sub small">计算中…</div>
      <details style="margin-top:12px"><summary>高级选项</summary>
        <div class="grid3">
          <div class="field"><label>报告详略</label><select id="na-detail"><option value="brief">简洁</option><option value="standard">标准</option><option value="detailed">详细</option></select></div>
          <div class="field"><label>上下文衔接</label><select id="na-ctx"><option value="3">附前 3 章结构摘要</option><option value="1">附前 1 章结构摘要</option><option value="0">不使用前文摘要</option></select></div>
          <div class="field"><label>失败重试（网络 / 格式错误）</label><select id="na-retry"><option value="2">最多 2 次</option><option value="1">最多 1 次</option><option value="0">不重试</option></select></div>
        </div>
        <div class="small faint">第一版按章节顺序执行，并发固定为 1。登录失效、额度用尽、模型无权限时不会自动重试。</div>
      </details></div></div></div>
    <div class="banner info">选中的正文将发送给所选模型服务进行分析，报告保存在本机。</div>
    <div class="row" style="justify-content:flex-end"><button id="na-draft">保存草稿</button><button class="primary" id="na-go">开始分析</button></div>
  </div>`;
  $("#na-detail").value = F.options.detail || "standard";
  $("#na-ctx").value = String(F.options.context ?? 3);
  $("#na-retry").value = String(F.options.retries ?? 2);

  const body = V.chapters.filter(c => !c.excluded);
  const drawScope = () => {
    $$("#na-scope .radio-card").forEach(c => c.classList.toggle("on", c.dataset.s === F.scope.type));
    const d = $("#na-scope-detail");
    if (F.scope.type === "chapters") {
      const ids = new Set(F.scope.chapter_ids || []);
      d.innerHTML = `<div class="row" style="margin-bottom:6px"><input type="search" id="na-chq" placeholder="筛选章节" style="max-width:220px">
        <button class="sm" id="na-all">全选</button><button class="sm" id="na-none">清空</button>
        <span class="small sub" id="na-chn"></span></div>
        <div class="checklist">${body.map(c => `<label data-t="${h(c.title)}"><input type="checkbox" value="${c.id}" ${ids.has(c.id) ? "checked" : ""}>
          <span class="faint" style="width:28px">${c.seq}</span><span class="grow">${h(c.title)}</span>
          ${c.kind === "extra" ? `<span class="tag">番外</span>` : ""}<span class="faint">${c.n_chars} 字</span></label>`).join("")}</div>`;
      const sync = () => { F.scope.chapter_ids = $$("#na-scope-detail input[type=checkbox]:checked").map(x => +x.value); $("#na-chn").textContent = `已选 ${F.scope.chapter_ids.length} 章`; updPreview(); };
      $$("#na-scope-detail input[type=checkbox]").forEach(x => x.onchange = sync);
      $("#na-chq").oninput = e => $$("#na-scope-detail label").forEach(l => l.style.display = l.dataset.t.includes(e.target.value) ? "" : "none");
      $("#na-all").onclick = () => { $$("#na-scope-detail label").forEach(l => { if (l.style.display !== "none") $("input", l).checked = true; }); sync(); };
      $("#na-none").onclick = () => { $$("#na-scope-detail input").forEach(x => x.checked = false); sync(); };
      $("#na-chn").textContent = `已选 ${ids.size} 章`;
    } else if (F.scope.type === "all") {
      const extras = body.filter(c => c.kind === "extra").length;
      d.innerHTML = `<label class="small"><input type="checkbox" id="na-noextra" ${F.scope.exclude_extra ? "checked" : ""} ${extras ? "" : "disabled"}> 排除番外（${extras} 章）</label>`;
      $("#na-noextra").onchange = e => { F.scope.exclude_extra = e.target.checked; updPreview(); };
    } else d.innerHTML = "";
    updPreview();
  };
  $$("#na-scope .radio-card").forEach(c => c.onclick = () => { F.scope = {type: c.dataset.s, chapter_ids: F.scope.chapter_ids || []}; drawScope(); });

  const drawTpl = async () => {
    $$("#na-tpl .radio-card").forEach(c => c.classList.toggle("on", +c.dataset.tv === F.tv));
    const tv = await api(`/template_versions/${F.tv}`);
    $("#na-tpl-q").innerHTML = `<div class="small sub" style="margin-bottom:4px">这个方法会回答：</div>
      <ul style="margin:0;padding-left:20px" class="small">${tv.sections.map(s => `<li><b>${h(s.label)}</b>：${h(s.question)}</li>`).join("")}</ul>
      <div class="small faint" style="margin-top:6px">一次任务只使用一个组合模板，所有维度在同一次章节分析中回答，不会把正文重复提交多次。</div>`;
  };
  $$("#na-tpl .radio-card").forEach(c => c.onclick = () => { F.tv = +c.dataset.tv; drawTpl(); });

  let previewData = null;
  const updPreview = async () => {
    const p = $("#na-prev");
    if (F.scope.type === "chapters" && !arr(F.scope.chapter_ids).length) { p.innerHTML = "请先选择章节"; previewData = null; updGo(); return; }
    try {
      previewData = await post(`/books/${bid}/preview`, {scope: F.scope});
      const d = previewData;
      p.innerHTML = `<div style="color:var(--ink)">将处理 <b>${d.n_chapters}</b> 章、<b>${fmtChars(d.n_chars)}</b>，预计分 <b>${d.n_calls_total}</b> 批调用
        （章节 ${d.n_calls_chapter}${d.n_calls_segment ? ` + 分段汇总 ${d.n_calls_segment}` : ""} + 范围汇总 1）。</div>
        <div>将生成 ${d.n_chapters} 份章节报告，以及一份「${h(d.title)}」。${d.split_chapters ? `其中 ${d.split_chapters} 章较长，会拆成子单元分析后合并。` : ""}</div>
        <div class="small" style="margin-top:6px">${d.chapters.slice(0, 6).map(c => `第 ${c.seq} 章 ${h(c.title)}（${c.n_chars} 字）`).join("；")}${d.chapters.length > 6 ? ` … 共 ${d.chapters.length} 章` : ""}</div>`;
    } catch (e) { p.innerHTML = `<span style="color:var(--err)">${h(e.message)}</span>`; previewData = null; }
    updGo();
  };

  let engs = {};
  const drawEng = () => {
    $("#na-eng").innerHTML = engineCards(engs, F.engine);
    $$("#na-eng .radio-card").forEach(c => c.onclick = () => {
      if (c.classList.contains("disabled")) return;
      F.engine = c.dataset.engine; drawEng(); updGo();
    });
    const e = engs[F.engine];
    $("#na-models").innerHTML = e ? [...arr(e.model_hints), e.default_model].filter(Boolean).map(m => `<option value="${h(m)}">`).join("") : "";
    if (e && e.default_model && !$("#na-model").value) $("#na-model").placeholder = `CLI 默认：${e.default_model}`;
  };
  const updGo = () => {
    const e = engs[F.engine];
    const ready = previewData && e && e.installed && e.logged_in;
    $("#na-go").disabled = !ready;
    $("#na-go").title = !e ? "请选择模型连接" : !e.logged_in ? "未连接，可以先保存草稿" : "";
    $("#na-draft").disabled = !previewData || !F.engine;
  };
  const submit = async (asDraft) => {
    const r = await guard(() => post("/jobs", {book_id: +bid, template_version_id: F.tv, engine: F.engine, model: $("#na-model").value.trim(),
      scope: F.scope, options: {detail: $("#na-detail").value, context: +$("#na-ctx").value, retries: +$("#na-retry").value},
      draft: asDraft, replace_draft: draft ? draft.id : null}));
    toast(asDraft ? "草稿已保存" : "已提交");
    go(`#/task/${r.id}`);
  };
  $("#na-go").onclick = () => submit(false);
  $("#na-draft").onclick = () => submit(true);
  drawScope(); drawTpl();
  engs = await api("/engines");
  if (!F.engine) F.engine = Object.keys(engs).find(k => engs[k].logged_in) || Object.keys(engs).find(k => engs[k].installed) || null;
  drawEng(); updGo();
}, "library");

/* ================================================================ P05 任务中心 */
routeAdd("/tasks", async (main, q) => {
  const f = q.f || "active";
  const draw = async () => {
    const jobs = await api(`/jobs?filter=${f === "all" ? "" : f}`);
    main.innerHTML = `<div class="page wide" style="max-width:1300px">
      <div class="head"><h1>任务</h1></div>
      <div class="tabs">${[["active", "进行中"], ["waiting", "等待处理"], ["ended", "已结束"], ["all", "全部"]]
        .map(([k, t]) => `<a href="#/tasks?f=${k}" class="${f === k ? "on" : ""}">${t}</a>`).join("")}</div>
      ${jobs.length ? `<div class="card tablecard"><table><thead><tr><th>书名</th><th>范围</th><th>阶段</th><th>进度</th><th>模型</th><th>状态</th><th>开始时间</th><th></th></tr></thead><tbody>
        ${jobs.map(j => `<tr class="click" onclick="go('#/task/${j.id}')"><td><b>${h(j.book_title)}</b><div class="small sub">${h(j.template)}</div></td>
          <td>${h(j.title)}</td><td class="small">${h(stageText(j))}</td>
          <td class="num">${j.kind === "comparison" ? `${j.progress.done}/${j.progress.total}` : `${j.progress.ch_done}/${j.progress.ch_total} 章`}</td>
          <td class="num">${ENGINE_LABEL[j.engine]}<div class="small">${h(j.model || "默认模型")}</div></td>
          <td>${pill(j.status)}${j.status_reason ? `<div class="small sub">${h(j.status_reason)}</div>` : ""}</td>
          <td class="num">${fmtTime(j.started || j.created)}</td><td><a href="#/task/${j.id}">详情</a></td></tr>`).join("")}</tbody></table></div>` :
        `<div class="card empty">${f === "active" ? "没有进行中的任务" : "没有任务"}</div>`}</div>`;
  };
  await draw();
  if (f === "active" || f === "all") every(4000, draw);
}, "tasks");

function stageText(j) {
  if (j.kind === "comparison") return j.status === "done" ? "已生成对照" : j.current || "等待生成";
  if (["done"].includes(j.status)) return "已完成";
  if (j.current) return j.current.startsWith("范围汇总") || j.current.startsWith("分段汇总") ? "汇总：" + j.current : "章节分析：" + j.current;
  if (j.progress.ch_done === j.progress.ch_total && j.progress.ch_total) return "汇总阶段";
  return "章节分析";
}

/* ================================================================ 任务详情 */
routeAdd("/task/(\\d+)", async (main, jid) => {
  const draw = async () => {
    const d = await api(`/jobs/${jid}`);
    const j = d.job, s = j.status;
    const chUnits = d.units.filter(u => u.kind === "chapter");
    const chDone = chUnits.filter(u => u.status === "done").length;
    const pct = d.units.length ? Math.round(100 * chDone / Math.max(1, chUnits.length)) : 0;
    const btn = [];
    if (s === "draft") btn.push(`<button class="primary" data-act="start">开始分析</button>`, `<button onclick="go('#/book/${j.book_id}/new?draft=${j.id}')">修改</button>`);
    if (["running", "queued"].includes(s)) btn.push(`<button data-act="pause">${s === "running" ? "暂停（完成当前章节后）" : "暂停"}</button>`);
    if (["running", "queued", "pausing", "paused", "waiting_login", "waiting_quota", "draft", "partial", "failed"].includes(s)) btn.push(`<button class="danger" data-act="cancel">取消</button>`);
    if (["paused", "waiting_login", "waiting_quota"].includes(s)) btn.push(`<button class="primary" data-act="resume">继续</button>`);
    if (["partial", "failed"].includes(s) && d.units.some(u => u.status === "failed" || u.status === "skipped" || u.status === "pending")) btn.push(`<button class="primary" data-act="retry">重试未完成部分</button>`);
    if (s === "partial" && d.units.some(u => ["summary", "segment"].includes(u.kind) && u.status !== "done")) btn.push(`<button data-act="gap_summary">生成带缺口的汇总</button>`);
    if (j.kind === "analysis" && chDone) btn.push(`<button onclick="go('#/reader/${j.id}')">查看结果</button>`);
    if (j.kind === "comparison" && s === "done") btn.push(`<button class="primary" onclick="go('#/compare/${j.comparison_id}')">查看比较</button>`);
    if (["done", "partial"].includes(s) && j.kind === "analysis") btn.push(`<button onclick="go('#/book/${j.book_id}/new')">创建新版任务</button>`);
    if (!["running", "queued", "pausing"].includes(s)) btn.push(`<button class="ghost danger" data-act="delete">删除</button>`);
    const ICON = {done: "✓", running: "◌", pending: "○", failed: "✕", uncertain: "?", skipped: "–"};
    const summaryWaiting = u => u.status === "pending" && chUnits.some(x => x.status !== "done");
    const last = d.events[0];
    main.innerHTML = `<div class="page" style="max-width:980px">
      <div class="crumb"><a href="#/tasks">任务</a> / #${j.id}</div>
      <div class="head"><div><h1>${h(j.book_title)} · ${h(j.title)}</h1>
        <div class="meta">${h(j.template)} · ${ENGINE_LABEL[j.engine]} / ${h(j.model || "默认模型")} · 创建于 ${fmtTime(j.created)}</div></div>
        <div class="actions">${btn.join("")}</div></div>
      ${d.engine_blocked && ["waiting_login", "waiting_quota"].includes(s) ? "" : ""}
      ${s === "waiting_login" ? `<div class="banner err"><span class="grow"><b>需要重新登录</b>：${h(ENGINE_LABEL[j.engine])} 的登录已失效或身份需要确认。重新连接后点「继续」。</span><button class="sm" onclick="go('#/settings')">去重新连接</button></div>` : ""}
      ${s === "waiting_quota" ? `<div class="banner err"><span class="grow"><b>额度已用完或触发频率限制</b>。${h(j.status_reason)}。请在服务方的重置时间之后手动点「继续」；本工具不会在后台自动唤醒或购买额度。</span></div>` : ""}
      ${s === "paused" && j.status_reason === "应用中断" ? `<div class="banner warn">应用中断后任务已暂停。${j.progress.uncertain ? `有 ${j.progress.uncertain} 个单元「结果待核实」：中断时请求可能已在远端完成并消耗额度。` : ""}继续前会请你确认。</div>` : ""}
      ${s === "partial" ? `<div class="banner warn">部分完成：已有结果可以查看和导出。${h(j.status_reason)}。不会显示为「全书已完成」。</div>` : ""}
      ${s === "failed" ? `<div class="banner err">${h(j.status_reason || "没有获得有效结果")}</div>` : ""}
      <div class="card">
        <div class="row"><b>${pill(s)}</b><span class="sub small">${h(j.status_reason && !["partial", "failed", "waiting_quota"].includes(s) ? j.status_reason : "")}</span>
          <span class="grow"></span><span class="small sub">已用时间 ${fmtDur(j.elapsed_now)}</span></div>
        ${j.kind === "analysis" ? `<div class="row" style="margin-top:10px"><span class="small">阶段：${h(stageText(j))}</span><span class="grow"></span>
          <span class="small">章节已完成 ${chDone}/${chUnits.length}</span></div>
          <div class="bar" style="margin-top:6px"><div style="width:${pct}%"></div></div>
          <div class="small faint" style="margin-top:4px">进度按完成的工作单元计算；章节完成后另有汇总阶段。没有可靠依据时不显示预计剩余时间。</div>` : ""}
      </div>
      <div class="card units">${d.units.map(u => `<div class="u"><span class="ico">${ICON[u.status] || "○"}</span>
        <span class="grow">${h(u.label)}${u.attempts > 1 ? ` <span class="small faint">（尝试 ${u.attempts} 次）</span>` : ""}
          ${u.status === "failed" || u.status === "uncertain" ? `<div class="err">${h((u.error || "").slice(0, 200))}</div>` : ""}</span>
        <span class="small sub">${u.status === "done" ? "已保存" : u.status === "running" ? "分析中" : u.status === "failed" ? "失败" :
          u.status === "uncertain" ? "结果待核实" : u.status === "skipped" ? h(u.error || "未生成") :
          (u.kind !== "chapter" && summaryWaiting(u)) ? "等待章节完成" : "等待处理"}</span>
        ${u.status === "done" && u.kind === "chapter" && j.kind === "analysis" ? `<a class="small" href="#/reader/${j.id}?ch=${u.chapter_id}">查看结果</a>` : ""}
        ${u.status === "done" && u.kind === "summary" ? `<a class="small" href="#/reader/${j.id}">查看汇总</a>` : ""}</div>`).join("")}</div>
      ${last ? `<div class="small sub" style="margin-bottom:10px">最近事件：${h(last.msg)}</div>` : ""}
      <details class="card"><summary>技术诊断日志（${d.events.length}）</summary>
        <div class="loglist">${d.events.map(e => `${fmtTime(e.ts)}  [${e.level}] ${h(e.msg)}`).join("\n")}</div></details>
    </div>`;
    $$("[data-act]").forEach(b => b.onclick = () => jobAction(j.id, b.dataset.act));
    return s;
  };
  const s = await draw();
  every(2500, async () => {
    const st = await draw().catch(() => null);
    if (st && !["running", "queued", "pausing"].includes(st)) { state.timers.forEach(clearInterval); state.timers = []; refreshOverview(); }
  });
  if (!["running", "queued", "pausing"].includes(s)) { state.timers.forEach(clearInterval); state.timers = []; }
}, "tasks");

async function jobAction(id, act, confirm = false) {
  if (act === "cancel" && !await confirmBox("取消任务", "取消后会中断当前请求，已校验的结果保留；取消的任务不能继续，只能创建后续任务。", "取消任务", true)) return;
  if (act === "delete" && !await confirmBox("删除任务", "删除这个任务及其结果、核对记录。", "删除", true)) return;
  const r = await guard(() => post(`/jobs/${id}/action`, {act, confirm}));
  if (r.need_confirm) {
    if (await confirmBox("结果待核实", h(r.message), "确认并重新分析")) return jobAction(id, act, true);
    return;
  }
  if (act === "delete") { go("#/tasks"); return; }
  refreshOverview(); render();
}

/* ================================================================ P06 分析阅读器 */
routeAdd("/reader/(\\d+)", async (main, jid, q) => {
  let R = await api(`/jobs/${jid}/report`);
  let sel = q.ch ? +q.ch : (R.summary ? "summary" : (R.chapters.find(c => c.result) || {}).id || "summary");
  const evCache = {};
  let ev = null;           // {seq, pseq}
  const secsOf = kind => kind === "chapter" ? R.template.sections : [{key: "overview", label: "范围概述", question: "范围整体推进与结构"}, ...R.template.sections];
  const sumTitleOf = () => (R.summary && R.summary.data.title) || R.job.title;

  main.innerHTML = `<div class="reader">
    <div class="r-nav" id="rd-nav"></div>
    <div class="r-main" id="rd-main"></div>
    <div class="r-ev" id="rd-ev"></div></div>`;

  const drawNav = () => {
    const ICON = {done: "✓", failed: "✕", running: "◌", pending: "○", uncertain: "?"};
    $("#rd-nav").innerHTML = `<div class="small" style="padding:0 10px 10px"><a href="#/book/${R.book.id}/reports">← ${h(R.book.title)}</a></div>
      <div class="it ${sel === "summary" ? "on" : ""}" data-sel="summary"><span class="ico">${R.summary ? "▤" : "○"}</span><span>${h(sumTitleOf())}</span></div>
      ${R.segments.map(s => `<div class="it ${sel === "seg" + s.id ? "on" : ""}" data-sel="seg${s.id}"><span class="ico">▤</span><span>${h(s.label)}</span></div>`).join("")}
      <h4>章节</h4>
      ${R.chapters.map(c => `<div class="it ${sel === c.id ? "on" : ""}" data-sel="${c.id}"><span class="ico">${ICON[c.status] || "○"}</span>
        <span>第 ${c.seq} 章 ${h(c.title.replace(/^第.+?[章回节]\s*/, ""))}</span></div>`).join("")}`;
    $$("#rd-nav [data-sel]").forEach(el => el.onclick = () => {
      const v = el.dataset.sel; sel = v === "summary" || v.startsWith("seg") ? v : +v; drawNav(); drawMain();
    });
  };

  const reviewOf = (rid, key) => R.reviews[`${rid}|${key}`] || {status: "待核对", note: "", revision: ""};
  const itemHtml = (rid, key, it, kind) => {
    const rv = reviewOf(rid, key);
    const cls = rv.status === "有疑问" ? "doubt" : rv.status === "已核对" ? "checked" : "";
    return `<div class="item ${cls}" data-rid="${rid}" data-key="${key}">
      <div class="claim ${rv.revision ? "revised" : ""}">${h(rv.revision || it.claim)}</div>
      ${rv.revision ? `<div class="orig">原模型输出：${h(it.claim)}</div>` : ""}
      <div class="meta">
        ${arr(it.evidence).map(e => `<span class="chip ${ev && ev.id === e ? "on" : ""}" data-ev="${e}">${e}</span>`).join("")}
        ${arr(it.invalid_evidence).map(e => `<span class="chip bad" title="段落编号不存在">${h(e)}</span>`).join("")}
        <span class="tag ${it.sufficiency === "不足" ? "err" : it.sufficiency === "有限" ? "warn" : ""}">证据${h(it.sufficiency)}</span>
        ${it.inferred ? `<span class="tag">推断</span>` : ""}
        ${arr(it.flags).map(f => `<span class="tag ${f === "无有效依据" ? "err" : "warn"}">${h(f)}</span>`).join("")}
        ${rv.revision ? `<span class="tag" style="color:var(--info)">已人工修订</span>` : ""}
      </div>
      ${it.reason ? `<div class="reason">${h(it.reason)}</div>` : ""}
      <div class="review">
        <div class="seg">${["待核对", "已核对", "有疑问"].map(s => `<button class="${rv.status === s ? "on" : ""}" data-st="${s}">${s}</button>`).join("")}</div>
        <button class="sm ghost" data-rev>修订</button>
        ${rv.note ? `<span class="small sub">备注：${h(rv.note)}</span>` : ""}
        ${rv.status === "有疑问" ? `<button class="sm ghost" data-note>写备注</button>${kind === "chapter" ? `<button class="sm" data-reana>请求针对性重分析</button>` : ""}` : ""}
      </div></div>`;
  };

  const drawMain = () => {
    const m = $("#rd-main");
    const st = R.job.status;
    const covered = R.chapters.filter(c => c.result);
    const sumTitle = sumTitleOf();
    const banner = st === "partial" ? `<div class="banner warn">部分完成：${covered.length}/${R.chapters.length} 章有结果。${R.summary ? "" : "范围汇总尚未生成。"}</div>` :
      ["running", "queued", "pausing"].includes(st) ? `<div class="banner info"><span class="grow">任务仍在执行：已完成的章节可以先看。</span><a href="#/task/${R.job.id}">查看进度</a></div>` :
      !["done"].includes(st) ? `<div class="banner warn">任务状态：${h(STATUS_LABEL[st] || st)}</div>` : "";
    const header = `<div class="head" style="margin-bottom:10px"><div><div class="crumb">${h(R.book.title)} / 报告 #${R.job.id}</div>
        <h1 style="font-size:19px">${h(sumTitle)}</h1>
        <div class="meta">覆盖：${covered.length ? `第 ${covered[0].seq}–${covered[covered.length - 1].seq} 章（${covered.length}/${R.chapters.length}）` : "暂无"}
          · ${h(R.template.name)} v${R.template.version} · ${ENGINE_LABEL[R.job.engine]} / ${h(R.job.model || "默认模型")}${arr(R.actual_models).length ? `（实际 ${h(R.actual_models.join("、"))}）` : ""} · 正文 v${R.version.vno}</div></div>
      <div class="actions"><a href="/api/jobs/${R.job.id}/export.md"><button class="sm">导出 Markdown</button></a>
        <a href="/api/jobs/${R.job.id}/export.json"><button class="sm">导出 JSON</button></a></div></div>${banner}`;
    let res, kind, title, extra = "";
    if (sel === "summary") {
      kind = "summary"; res = R.summary; title = sumTitle;
      if (!res) {
        const missing = R.chapters.filter(c => !c.result);
        m.innerHTML = header + `<div class="card empty"><h2>${h(title)}</h2>
          ${missing.length ? `<p>还有 ${missing.length} 章没有有效结果，汇总只在选定章节全部完成后生成。</p>
          ${st === "partial" ? `<p>也可以只用已完成章节生成一份<b>明确标注缺口</b>的范围汇总。</p><p><button class="primary" id="rd-gap">生成带缺口的汇总</button></p>` : ""}` :
          `<p>汇总尚未生成。</p>`}</div>`;
        $("#rd-gap") && ($("#rd-gap").onclick = () => jobAction(R.job.id, "gap_summary"));
        return;
      }
      const d = res.data;
      extra = `${d.gap ? `<div class="banner warn">这是带缺口的范围汇总，未覆盖：${arr(d.missing).map(x => `第 ${x.seq} 章`).join("、")}</div>` : ""}
        <div class="small sub" style="margin-bottom:12px">汇总只使用已完成的章节结果：${arr(d.covered).map(x => `第 ${x.seq} 章`).join("、")}。
        ${R.job.scope.type === "first3" ? "这是前三章分析，不代表全书结构。" : ""}</div>`;
    } else if (String(sel).startsWith("seg")) {
      kind = "segment"; res = R.segments.find(s => "seg" + s.id === sel); title = res.label;
    } else {
      const c = R.chapters.find(x => x.id === sel);
      kind = "chapter"; res = c.result; title = `第 ${c.seq} 章　${c.title}`;
      if (!res) { m.innerHTML = header + `<div class="card empty"><h2>${h(title)}</h2><p>${{failed: "这一章分析失败，可以在任务详情里重试。", uncertain: "结果待核实。", running: "正在分析…", pending: "尚未处理。"}[c.status] || "暂无结果"}</p>
        <p><a href="#/task/${R.job.id}">查看任务</a></p></div>`; return; }
      const idx = R.chapters.findIndex(x => x.id === sel);
      extra = `<div class="row small" style="margin-bottom:10px">${idx > 0 ? `<a href="javascript:void 0" data-jump="${R.chapters[idx - 1].id}">← 上一章</a>` : ""}<span class="grow"></span>
        ${idx < R.chapters.length - 1 ? `<a href="javascript:void 0" data-jump="${R.chapters[idx + 1].id}">下一章 →</a>` : ""}</div>`;
    }
    const d = res.data;
    m.innerHTML = header + `${kind === "summary" ? "" : `<h2 style="font-size:17px;margin:4px 0 6px">${h(title)}</h2>`}${extra}
      <div class="card" style="background:#fff">${h(d.one_line)}</div>
      ${secsOf(kind).map(sc => {
        const items = (d.sections || {})[sc.key] || [];
        return `<div class="sec"><h3>${h(sc.label)}</h3>${sc.question ? `<div class="q">${h(sc.question)}</div>` : ""}
          ${items.length ? items.map((it, i) => itemHtml(res.id, `${sc.key}:${i}`, it, kind)).join("") : `<div class="small faint">本节没有结论</div>`}</div>`;
      }).join("")}`;
    bindMain(res, kind);
  };

  const saveReview = async (rid, key, patch) => {
    const cur = reviewOf(rid, key);
    const next = {...cur, ...patch};
    await guard(() => post("/reviews", {result_id: rid, item_key: key, status: next.status, note: next.note, revision: next.revision}));
    R.reviews[`${rid}|${key}`] = {...next, result_id: rid, item_key: key};
    drawMain();
  };
  const bindMain = (res, kind) => {
    $$("#rd-main [data-jump]").forEach(a => a.onclick = () => { sel = +a.dataset.jump; drawNav(); drawMain(); $("#rd-main").parentNode.scrollTop = 0; });
    $$("#rd-main .chip[data-ev]").forEach(c => c.onclick = () => showEv(c.dataset.ev));
    $$("#rd-main .item").forEach(el => {
      const rid = +el.dataset.rid, key = el.dataset.key;
      $$("[data-st]", el).forEach(b => b.onclick = async () => {
        await saveReview(rid, key, {status: b.dataset.st});
        if (b.dataset.st === "有疑问" && !reviewOf(rid, key).note) noteBox(rid, key);
      });
      $("[data-rev]", el).onclick = async () => {
        const rv = reviewOf(rid, key);
        const it = (res.data.sections || {})[key.split(":")[0]][+key.split(":")[1]];
        const m = modal(`<h2>修订结论</h2><div class="small sub" style="margin-bottom:8px">修订保存为人工修订层，原模型输出保留。清空则撤销修订。</div>
          <div class="small faint" style="margin-bottom:6px">原模型输出：${h(it.claim)}</div>
          <textarea id="rv-t" rows="6">${h(rv.revision || it.claim)}</textarea>
          <div class="foot"><button data-close>取消</button><button class="primary" id="rv-ok">保存修订</button></div>`);
        $("#rv-ok", m.el).onclick = async () => {
          const t = $("#rv-t").value.trim();
          m.close(); await saveReview(rid, key, {revision: t === it.claim ? "" : t});
        };
      };
      $("[data-note]", el) && ($("[data-note]", el).onclick = () => noteBox(rid, key));
      $("[data-reana]", el) && ($("[data-reana]", el).onclick = async () => {
        const rv = reviewOf(rid, key);
        if (!await confirmBox("请求针对性重分析", `会为这一章创建一个新的分析任务，把你的疑问「${h(rv.note || "（未写备注）")}」交给模型复核。原结果保留，新结果是新的报告版本。`, "创建任务")) return;
        const r = await guard(() => post("/reviews/reanalyze", {result_id: rid, item_key: key, note: rv.note}));
        toast("已创建重分析任务"); go(`#/task/${r.id}`);
      });
    });
  };
  const noteBox = async (rid, key) => {
    const rv = reviewOf(rid, key);
    const m = modal(`<h2>核对备注</h2><textarea id="nb-t" rows="5" placeholder="哪里有疑问？例如：第 2-18 段并没有体现她主动">${h(rv.note)}</textarea>
      <div class="foot"><button data-close>取消</button><button class="primary" id="nb-ok">保存</button></div>`);
    $("#nb-ok", m.el).onclick = async () => { const t = $("#nb-t").value.trim(); m.close(); await saveReview(rid, key, {note: t}); };
  };

  const showEv = async (id) => {
    const [cs, ps] = id.split("-").map(Number);
    ev = {id, cs, ps};
    const pane = $("#rd-ev");
    pane.classList.add("open");
    if (!evCache[cs]) evCache[cs] = await api(`/versions/${R.version.id}/seq/${cs}`);
    const d = evCache[cs];
    const idx = d.paragraphs.findIndex(p => p.pseq === ps);
    pane.innerHTML = `<div class="evhead"><div class="row"><b class="grow">正文依据</b><button class="sm ghost drawer-close" id="ev-x">关闭</button></div>
      <div class="small sub">第 ${cs} 章《${h(d.chapter.title)}》 · 第 ${ps} 段</div>
      <div class="row" style="margin-top:6px"><button class="sm" id="ev-prev" ${idx <= 0 ? "disabled" : ""}>前一段</button>
        <button class="sm" id="ev-next" ${idx >= d.paragraphs.length - 1 ? "disabled" : ""}>后一段</button>
        ${R.chapters.some(c => c.seq === cs) ? "" : `<span class="tag warn">不在本报告范围内</span>`}</div></div>
      ${idx < 0 ? `<div class="banner warn">这个段落编号在当前正文版本中不存在</div>` : ""}
      ${d.paragraphs.map(p => `<div class="para ${p.pseq === ps ? "hl" : ""}" id="evp-${p.pseq}"><span class="lb">${p.label}</span><span class="tx">${h(p.text)}</span></div>`).join("")}`;
    const t = $(`#evp-${ps}`); t && t.scrollIntoView({block: "center"});
    $("#ev-prev").onclick = () => showEv(`${cs}-${d.paragraphs[idx - 1].pseq}`);
    $("#ev-next").onclick = () => showEv(`${cs}-${d.paragraphs[idx + 1].pseq}`);
    $("#ev-x").onclick = () => pane.classList.remove("open");
    $$("#rd-main .chip[data-ev]").forEach(c => c.classList.toggle("on", c.dataset.ev === id));
  };

  drawNav(); drawMain();
  $("#rd-ev").innerHTML = `<div class="evhead"><b>正文依据</b></div><div class="small sub">点击结论下方的段落编号（如 <span class="chip">2-18</span>），这里会显示对应正文并高亮该段。</div>`;
  if (["running", "queued", "pausing"].includes(R.job.status)) {
    every(6000, async () => {
      const n = await api(`/jobs/${jid}/report`).catch(() => null);
      if (!n) return;
      const changed = n.chapters.filter(c => c.result).length !== R.chapters.filter(c => c.result).length || !!n.summary !== !!R.summary || n.job.status !== R.job.status;
      if (!changed) return;
      const before = sel === "summary" ? !!R.summary : !!(R.chapters.find(c => c.id === sel) || {}).result;
      n.reviews = {...n.reviews, ...R.reviews}; R = n; drawNav();
      const after = sel === "summary" ? !!R.summary : !!(R.chapters.find(c => c.id === sel) || {}).result;
      if (before !== after) drawMain();
    });
  }
}, "library");
