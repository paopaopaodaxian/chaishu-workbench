/* P07 多书比较 · P08 拆解方法 · P09 设置 */
"use strict";

/* ================================================================ P07 多书比较 */
routeAdd("/compare", async (main) => {
  const list = await api("/comparisons");
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>多书比较</h1><div class="meta">选 2–6 本书，按「维度为行、作品为列」逐项对照，每个结论都能追溯到书籍与章节。</div></div>
      <div class="actions"><button class="primary" onclick="go('#/compare/new')">创建比较</button></div></div>
    ${list.length ? `<div class="card tablecard"><table><thead><tr><th>比较</th><th>书籍</th><th>范围</th><th>状态</th><th>更新时间</th></tr></thead><tbody>
      ${list.map(c => `<tr class="click" onclick="go('#/compare/${c.id}')"><td><b>${h(c.title)}</b></td><td class="small">${c.books.map(h).join("、")}</td>
        <td class="num">${c.scope === "first3" ? "各书前三章" : "全书"}</td><td>${pill(c.status, c.status_label)}</td><td class="num">${fmtTime(c.updated)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="card empty"><h2>还没有比较</h2><p>默认比较各书前三章；每本书需要先有分析报告。缺少报告的书也可以先放进比较草稿。</p>
      <p><button class="primary" onclick="go('#/compare/new')">创建比较</button></p></div>`}</div>`;
}, "compare");

routeAdd("/compare/new", async (main, q) => compareEditor(main, null, q), "compare");

async function compareEditor(main, existing, q = {}) {
  const books = await api("/books");
  const F = existing ? {
    id: existing.comparison.id, title: existing.comparison.title, scope: existing.comparison.scope,
    picks: arr(existing.comparison.items.books).map(b => ({book_id: b.book_id, job_id: b.job_id})),
    dims: existing.comparison.flags.dims,
  } : {title: "", scope: "first3", picks: (q.books || "").split(",").filter(Boolean).map(id => ({book_id: +id, job_id: null})), dims: null};
  const tpl = await api("/templates/1");
  const allDims = [...new Set([...tpl.compare_dim_options, ...arr(F.dims)])];
  if (!F.dims) F.dims = [...tpl.compare_dim_options];
  let cands = [], check = null, engs = null, engine = null;

  main.innerHTML = `<div class="page" style="max-width:1080px">
    <div class="crumb"><a href="#/compare">多书比较</a> / ${existing ? "比较草稿" : "创建比较"}</div>
    <div class="head"><h1>${existing ? h(F.title) : "创建比较"}</h1>${existing ? `<div class="actions"><button class="ghost danger" id="ce-del">删除草稿</button></div>` : ""}</div>
    <div class="card"><div class="step"><div class="n">1</div><div class="body"><h3>选择书籍（2–6 本）</h3>
      <div class="checklist" id="ce-books">${books.map(b => `<label><input type="checkbox" value="${b.id}" ${F.picks.some(p => p.book_id === b.id) ? "checked" : ""}>
        <span class="grow"><b>${h(b.title)}</b> <span class="faint">${h(b.grp || "未分组")}</span></span>
        <span class="small ${b.reports ? "sub" : "faint"}">${b.reports ? `${b.reports} 份报告` : "无报告"}</span></label>`).join("")}</div></div></div></div>
    <div class="card"><div class="step"><div class="n">2</div><div class="body"><h3>选择报告版本与比较范围</h3>
      <div class="seg" id="ce-scope" style="margin-bottom:12px"><button data-s="first3">各书前三章（默认）</button><button data-s="all">全书</button></div>
      <div class="small faint" style="margin-bottom:10px">全书比较要求每本书都有完整覆盖的全书报告。前三章比较也会展示各书字数差异，不假设三章长度相同。</div>
      <div id="ce-reports"></div></div></div></div>
    <div class="card"><div class="step"><div class="n">3</div><div class="body"><h3>比较维度</h3>
      <div class="row wrap" id="ce-dims">${allDims.map(d => `<label class="small" style="margin-right:12px"><input type="checkbox" value="${h(d)}" ${F.dims.includes(d) ? "checked" : ""}> ${h(d)}</label>`).join("")}
        <button class="sm ghost" id="ce-adddim">＋ 自定义维度</button></div></div></div></div>
    <div class="card"><div class="step"><div class="n">4</div><div class="body"><h3>检查可比性</h3><div id="ce-check" class="sub small">选好书和报告后自动检查</div></div></div></div>
    <div class="card"><div class="step"><div class="n">5</div><div class="body"><h3>生成对照</h3>
      <div class="field"><label>比较名称</label><input type="text" id="ce-title" value="${h(F.title)}" placeholder="留空则用书名自动命名"></div>
      <div class="radio-cards" id="ce-eng"><div class="small sub">读取模型连接…</div></div>
      <div class="field" style="margin-top:10px"><label>模型（留空使用 CLI 默认）</label><input type="text" id="ce-model"></div>
      <label class="small hidden" id="ce-ack-wrap"><input type="checkbox" id="ce-ack"> 我已了解上述差异，结果中将保留差异标记</label>
      <div class="row" style="justify-content:flex-end;margin-top:10px"><button id="ce-save">保存比较草稿</button><button class="primary" id="ce-gen" disabled>生成对照</button></div>
      <div class="small faint" style="margin-top:6px">比较只发送各书的结构化分析结果（不是正文）给模型。缺少报告时可以先保存草稿，补充分析后再生成。</div>
    </div></div></div></div>`;

  const picked = () => $$("#ce-books input:checked").map(x => +x.value);
  const loadCands = async () => {
    const ids = picked();
    F.picks = ids.map(id => F.picks.find(p => p.book_id === id) || {book_id: id, job_id: null});
    cands = ids.length ? await api(`/comparisons/candidates?books=${ids.join(",")}`) : [];
    for (const p of F.picks) {
      const c = cands.find(x => x.id === p.book_id);
      if (c && !p.job_id && c.reports.length) {
        const pref = c.reports.find(r => F.scope === "all" ? r.scope.type === "all" && r.status === "done" : true);
        p.job_id = (pref || c.reports[0]).id;
      }
    }
    drawReports(); runCheck();
  };
  const drawReports = () => {
    $$("#ce-scope button").forEach(b => b.classList.toggle("on", b.dataset.s === F.scope));
    $("#ce-reports").innerHTML = F.picks.length ? `<table><tbody>${F.picks.map(p => {
      const c = cands.find(x => x.id === p.book_id) || {title: "…", reports: []};
      return `<tr><td style="width:30%"><b>${h(c.title)}</b></td><td>${c.reports.length ? `<select data-b="${p.book_id}">${c.reports.map(r =>
        `<option value="${r.id}" ${r.id === p.job_id ? "selected" : ""}>${h(r.title)} · ${h(r.template)} · ${ENGINE_LABEL[r.engine]} · ${r.status_label} · ${fmtTime(r.created)}</option>`).join("")}</select>` :
        `<span class="pill warn">缺少分析报告</span> <button class="sm" onclick="go('#/book/${p.book_id}/new')">去补充分析</button>`}</td></tr>`;
    }).join("")}</tbody></table>` : `<div class="sub small">先在上面选择书籍</div>`;
    $$("#ce-reports select").forEach(s => s.onchange = () => { F.picks.find(p => p.book_id === +s.dataset.b).job_id = +s.value; runCheck(); });
  };
  const runCheck = async () => {
    const box = $("#ce-check");
    if (F.picks.length < 2) { box.innerHTML = "至少选择 2 本书"; check = null; updGen(); return; }
    if (F.picks.length > 6) { box.innerHTML = `<span style="color:var(--err)">最多 6 本</span>`; check = null; updGen(); return; }
    check = await post("/comparisons/check", {books: F.picks, scope: F.scope});
    box.innerHTML = `<table><thead><tr><th>书</th><th>报告</th><th>范围内章节</th><th>问题</th></tr></thead><tbody>${check.rows.map(r => `<tr>
      <td><b>${h(r.title)}</b></td><td class="small">${r.job_title ? `${h(r.job_title)}<div class="sub">${h(r.template)} · ${ENGINE_LABEL[r.engine] || ""}</div>` : "—"}</td>
      <td class="small">${arr(r.chapters).map(c => `${c.done ? "" : "✕ "}第${c.seq}章 ${c.n_chars}字`).join("<br>") || "—"}</td>
      <td>${r.issues.length ? r.issues.map(i => `<div class="small" style="color:${r.ok ? "var(--warn)" : "var(--err)"}">${h(i)}</div>`).join("") : `<span class="pill ok">可比较</span>`}</td></tr>`).join("")}</tbody></table>
      ${check.differences.map(d => `<div class="banner warn" style="margin-top:8px">差异：${h(d)}</div>`).join("")}`;
    updGen();
  };
  const updGen = () => {
    const ok = check && check.rows.filter(r => r.ok).length >= 2;
    const needAck = check && (check.differences.length || !check.ready);
    $("#ce-ack-wrap").classList.toggle("hidden", !needAck);
    const e = engs && engs[engine];
    $("#ce-gen").disabled = !(ok && e && e.logged_in && (!needAck || $("#ce-ack").checked));
    $("#ce-save").disabled = F.picks.length < 2;
  };
  const save = async () => {
    F.dims = $$("#ce-dims input:checked").map(x => x.value);
    const r = await guard(() => post("/comparisons", {id: F.id, title: $("#ce-title").value, books: F.picks, scope: F.scope, dims: F.dims}));
    F.id = r.id; return r.id;
  };
  $("#ce-books").onchange = loadCands;
  $$("#ce-scope button").forEach(b => b.onclick = () => { F.scope = b.dataset.s; F.picks.forEach(p => p.job_id = null); loadCands(); });
  $("#ce-ack").onchange = updGen;
  $("#ce-adddim").onclick = async () => {
    const d = await promptBox("自定义维度", "维度名称，例如「男主出场方式」");
    if (d) $("#ce-adddim").insertAdjacentHTML("beforebegin", `<label class="small" style="margin-right:12px"><input type="checkbox" value="${h(d)}" checked> ${h(d)}</label>`);
  };
  $("#ce-save").onclick = async () => { const id = await save(); toast("草稿已保存"); go(`#/compare/${id}`); };
  $("#ce-gen").onclick = async () => {
    const id = await save();
    const r = await guard(() => post(`/comparisons/${id}/generate`, {engine, model: $("#ce-model").value.trim(), ack: $("#ce-ack").checked}));
    toast("比较已提交"); go(`#/compare/${id}`);
  };
  $("#ce-del") && ($("#ce-del").onclick = async () => {
    if (!await confirmBox("删除比较草稿", "删除后不可恢复。", "删除", true)) return;
    await guard(() => post(`/comparisons/${F.id}/delete`)); go("#/compare");
  });
  await loadCands();
  engs = await api("/engines");
  engine = Object.keys(engs).find(k => engs[k].logged_in) || null;
  const drawEng = () => {
    $("#ce-eng").innerHTML = engineCards(engs, engine, {showWarn: false});
    $$("#ce-eng .radio-card").forEach(c => c.onclick = () => { if (!c.classList.contains("disabled")) { engine = c.dataset.engine; drawEng(); updGen(); } });
  };
  drawEng(); updGen();
}

routeAdd("/compare/(\\d+)", async (main, cid) => {
  const v = await api(`/comparisons/${cid}`);
  const c = v.comparison;
  if (!c.job_id) return compareEditor(main, v);
  const job = v.job, snap = v.snapshot, r = v.result;
  const ver = {};
  const srcChip = s => {
    const [bk, id] = s.split(":");
    return `<span class="chip" data-src="${h(s)}">${h(bk)} ${h(id)}</span>`;
  };
  main.innerHTML = `<div class="page wide" style="max-width:1400px">
    <div class="crumb"><a href="#/compare">多书比较</a> / #${c.id}</div>
    <div class="head"><div><h1>${h(c.title)}</h1>
      <div class="meta">${c.scope === "first3" ? "各书前三章" : "各书全书报告"} · ${ENGINE_LABEL[job.engine]} / ${h(job.model || "默认模型")} · ${pill(job.status, job.status_label)}
        ${job.finished ? " · " + fmtTime(job.finished) : ""}</div></div>
      <div class="actions">${r ? `<a href="/api/comparisons/${c.id}/export.md"><button class="sm">导出 Markdown</button></a>
        <a href="/api/comparisons/${c.id}/export.json"><button class="sm">导出 JSON</button></a>` : ""}
        <button class="sm" onclick="go('#/task/${job.id}')">任务详情</button>
        <button class="sm ghost danger" id="cv-del">删除</button></div></div>
    ${!r ? `<div class="banner ${["failed", "waiting_login", "waiting_quota", "paused"].includes(job.status) ? "err" : "info"}"><span class="grow">${["running", "queued"].includes(job.status) ? "正在生成对照…" : `比较未完成：${h(job.status_label)} ${h(job.status_reason || "")}`}</span><a href="#/task/${job.id}">查看任务</a></div>` : ""}
    <div class="card"><h3>参与比较</h3><table><tbody>${snap.map(b => `<tr><td style="width:50px"><b>${b.key}</b></td>
      <td><b>${h(b.title)}</b>${b.deleted ? ` <span class="pill err">来源已删除</span>` : ""}<div class="small sub">${h(b.grp || "未分组")}</div></td>
      <td class="small">报告「${h(b.job_title)}」<br>${h(b.template)} · ${ENGINE_LABEL[b.engine]} ${h(b.model || "")}</td>
      <td class="small">${b.chapters.map(x => `第${x.seq}章 ${x.n_chars}字`).join("、")}${b.missing.length ? `<div style="color:var(--warn)">缺少第 ${b.missing.join("、")} 章分析</div>` : ""}</td></tr>`).join("")}</tbody></table>
      ${arr(c.flags.differences).map(d => `<div class="banner warn" style="margin:8px 0 0">差异标记：${h(d)}</div>`).join("")}</div>
    ${r ? `<div class="card scrollx" style="padding:0"><table class="cmp-table"><thead><tr><th>比较维度</th>${snap.map(b => `<th>${b.key}《${h(b.title)}》</th>`).join("")}</tr></thead><tbody>
      ${r.rows.map(row => `<tr><td>${h(row.dimension)}</td>${snap.map(b => {
        const cell = row.cells[b.key] || {};
        return `<td><div>${h(cell.claim)}</div><div class="cell-src">${arr(cell.sources).map(srcChip).join("")}
          <span class="tag ${cell.sufficiency === "不足" ? "err" : cell.sufficiency === "有限" ? "warn" : ""}">证据${h(cell.sufficiency)}</span></div></td>`;
      }).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="grid2">${[["common", "共性"], ["differences", "差异"], ["hypotheses", "待验证假设"], ["principles", "可用于原创的原则"]].map(([k, t]) =>
      `<div class="card"><h3>${t}</h3>${arr(r[k]).map(p => `<div style="margin-bottom:10px">${h(p.text)}<div class="cell-src" style="margin-top:4px">
        ${p.sources.map(srcChip).join("")}${arr(p.flags).map(f => `<span class="tag err">${h(f)}</span>`).join("")}</div></div>`).join("") || `<div class="sub small">无</div>`}</div>`).join("")}</div>
    <div class="small faint">来源格式：书代号 章节-段落。点击来源查看对应正文。结论基于各书的章节分析结果，不包含市场数据，不代表商业表现。</div>` : ""}
  </div>`;
  $("#cv-del").onclick = async () => {
    if (!await confirmBox("删除比较", "删除这个比较及其结果。", "删除", true)) return;
    await guard(() => post(`/comparisons/${cid}/delete`)); go("#/compare");
  };
  $$("[data-src]").forEach(ch => ch.onclick = async () => {
    const [bk, id] = ch.dataset.src.split(":");
    const b = snap.find(x => x.key === bk);
    if (!b || b.deleted) { toast("来源已删除，只保留比较快照", true); return; }
    const [cs, ps] = id.split("-").map(Number);
    const d = await guard(() => api(`/versions/${b.version_id}/seq/${cs}`));
    const m = modal(`<h2>${h(b.key)}《${h(b.title)}》第 ${cs} 章 · 第 ${ps} 段</h2><div class="small sub" style="margin-bottom:8px">${h(d.chapter.title)}</div>
      <div style="max-height:60vh;overflow:auto">${d.paragraphs.map(p => `<div class="para ${p.pseq === ps ? "hl" : ""}" id="cm-${p.pseq}"><span class="lb">${p.label}</span><span class="tx">${h(p.text)}</span></div>`).join("")}</div>
      <div class="foot"><button data-close>关闭</button></div>`, {wide: true});
    const t = $(`#cm-${ps}`, m.el); t && t.scrollIntoView({block: "center"});
  });
  if (["running", "queued"].includes(job.status)) every(4000, async () => {
    const n = await api(`/comparisons/${cid}`).catch(() => null);
    if (n && n.job && n.job.status !== job.status) render();
  });
}, "compare");

/* ================================================================ P08 拆解方法 */
routeAdd("/templates", async (main) => {
  const list = await api("/templates");
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>拆解方法</h1><div class="meta">内置模板只读，可以复制成个人模板再修改。修改模板会生成新版本，旧任务继续使用创建时的版本。</div></div></div>
    <div class="card tablecard"><table><thead><tr><th>名称</th><th>适用目标</th><th>输出维度</th><th>版本</th><th>使用次数</th><th></th></tr></thead><tbody>
    ${list.map(t => `<tr class="click" data-id="${t.id}"><td><b>${h(t.name)}</b> ${t.builtin ? `<span class="pill">内置</span>` : `<span class="pill accent">个人</span>`}</td>
      <td class="small">${h(t.scene)}</td><td class="small">${t.sections.map(s => h(s.label)).join("、")}</td>
      <td class="num">v${t.version}</td><td class="num">${t.uses}</td>
      <td style="text-align:right"><button class="sm" data-copy="${t.id}">复制</button></td></tr>`).join("")}</tbody></table></div></div>`;
  $$("tr[data-id]").forEach(tr => tr.onclick = e => { if (!e.target.closest("button")) go(`#/template/${tr.dataset.id}`); });
  $$("[data-copy]").forEach(b => b.onclick = async () => { const r = await guard(() => post(`/templates/${b.dataset.copy}/copy`)); toast("已复制为个人模板"); go(`#/template/${r.id}`); });
}, "templates");

routeAdd("/template/(\\d+)", async (main, tid) => {
  const t = await api(`/templates/${tid}`);
  const ro = !!t.builtin;
  const D = {name: t.name, scene: t.scene, default_scope: t.default_scope, sections: t.sections.map(s => ({...s})),
    evidence_req: t.evidence_req, compare_dims: [...arr(t.compare_dims)], extra_prompt: t.extra_prompt || ""};
  const SCOPE = {first3: "前三章", chapters: "指定章节", all: "全书"};
  const draw = () => {
    main.innerHTML = `<div class="page" style="max-width:960px">
      <div class="crumb"><a href="#/templates">拆解方法</a> / ${h(t.name)}</div>
      <div class="head"><div><h1>${h(t.name)}</h1><div class="meta">${ro ? "内置模板（只读）" : "个人模板"} · 当前 v${t.version} · 共 ${t.versions.length} 个版本</div></div>
        <div class="actions">${ro ? `<button class="primary" id="tp-copy">复制为个人模板</button>` :
          `<button class="ghost danger" id="tp-del">删除</button><button class="primary" id="tp-save">保存为新版本</button>`}</div></div>
      <div class="card"><h3>要研究什么</h3>
        <div class="grid2"><div class="field"><label>名称</label><input type="text" id="tp-name" value="${h(D.name)}" ${ro ? "disabled" : ""}></div>
          <div class="field"><label>默认分析范围</label><select id="tp-scope" ${ro ? "disabled" : ""}>${Object.entries(SCOPE).map(([k, v]) => `<option value="${k}" ${D.default_scope === k ? "selected" : ""}>${v}</option>`).join("")}</select></div></div>
        <div class="field"><label>适用场景</label><textarea id="tp-scene" rows="2" ${ro ? "disabled" : ""}>${h(D.scene)}</textarea></div>
        <div class="field"><label>必答问题（每个问题对应报告里的一个部分）</label>
          <div id="tp-secs">${D.sections.map((s, i) => `<div class="row" style="align-items:flex-start;margin-bottom:8px">
            <input type="text" data-i="${i}" data-f="label" value="${h(s.label)}" style="width:160px;flex:none" ${ro ? "disabled" : ""}>
            <textarea data-i="${i}" data-f="question" rows="2" style="min-height:40px" ${ro ? "disabled" : ""}>${h(s.question)}</textarea>
            ${ro ? "" : `<div style="display:flex;flex-direction:column;gap:2px"><button class="sm ghost" data-up="${i}" ${i ? "" : "disabled"}>↑</button><button class="sm ghost danger" data-rm="${i}">✕</button></div>`}
          </div>`).join("")}</div>
          ${ro ? "" : `<button class="sm" id="tp-add">＋ 添加问题</button>`}</div></div>
      <div class="card"><h3>需要哪些证据</h3>
        <textarea id="tp-ev" rows="3" ${ro ? "disabled" : ""}>${h(D.evidence_req)}</textarea>
        <div class="small faint" style="margin-top:6px">无论模板怎么写，程序都会校验每条结论引用的段落编号是否真实存在，找不到依据的结论会被标为「无有效依据」。</div></div>
      <div class="card"><h3>报告长什么样</h3>
        <div class="small sub" style="margin-bottom:8px">每章报告：一句话概括 + 下列部分；每条结论附段落依据、是否推断、证据充分 / 有限 / 不足及理由、核对状态。范围汇总在最前面多一个「范围概述」。</div>
        <ol class="small" style="margin:0 0 10px;padding-left:20px">${D.sections.map(s => `<li>${h(s.label)}</li>`).join("")}</ol>
        <div class="field"><label>多书比较时的对照维度</label><textarea id="tp-dims" rows="2" ${ro ? "disabled" : ""} placeholder="每行一个">${h(D.compare_dims.join("\n"))}</textarea></div></div>
      <details class="card"><summary>高级提示词</summary>
        <div class="small faint" style="margin-bottom:6px">追加给模型的额外要求。普通使用不需要填写；证据规则和输出格式由程序保证，这里不能覆盖。</div>
        <textarea id="tp-extra" rows="6" ${ro ? "disabled" : ""}>${h(D.extra_prompt)}</textarea></details>
      <div class="card"><h3>版本</h3>${t.versions.map(v => `<div class="small">v${v.version} · ${fmtTime(v.created)}${v.id === t.latest ? " · 当前" : ""}</div>`).join("")}</div></div>`;
    if (ro) { $("#tp-copy").onclick = async () => { const r = await guard(() => post(`/templates/${tid}/copy`)); go(`#/template/${r.id}`); }; return; }
    const sync = () => {
      D.name = $("#tp-name").value; D.scene = $("#tp-scene").value; D.default_scope = $("#tp-scope").value;
      D.evidence_req = $("#tp-ev").value; D.extra_prompt = $("#tp-extra").value;
      D.compare_dims = $("#tp-dims").value.split("\n").map(s => s.trim()).filter(Boolean);
      $$("#tp-secs [data-i]").forEach(el => D.sections[+el.dataset.i][el.dataset.f] = el.value);
    };
    $("#tp-add").onclick = () => { sync(); D.sections.push({key: "", label: "", question: ""}); draw(); };
    $$("[data-rm]").forEach(b => b.onclick = () => { sync(); D.sections.splice(+b.dataset.rm, 1); draw(); });
    $$("[data-up]").forEach(b => b.onclick = () => { sync(); const i = +b.dataset.up; [D.sections[i - 1], D.sections[i]] = [D.sections[i], D.sections[i - 1]]; draw(); });
    $("#tp-save").onclick = async () => {
      sync();
      await guard(() => post(`/templates/${tid}/save`, D));
      toast("已保存为新版本"); render();
    };
    $("#tp-del").onclick = async () => {
      if (!await confirmBox("删除模板", "删除后不能再用于新任务；已有任务仍保留它们使用的版本。", "删除", true)) return;
      await guard(() => post(`/templates/${tid}/delete`)); go("#/templates");
    };
  };
  draw();
}, "templates");

/* ================================================================ P09 设置 */
routeAdd("/settings", async (main) => {
  main.innerHTML = `<div class="page" style="max-width:980px"><div class="head"><h1>设置</h1></div>
    <h2 style="font-size:16px;margin:0 0 10px">模型连接</h2><div id="st-eng"><div class="card sub">正在读取 CLI 安装与登录状态…</div></div>
    <h2 style="font-size:16px;margin:22px 0 10px">数据与导出</h2><div id="st-data"></div></div>`;
  const INSTALL = {claude: "需要先在电脑上安装 Claude Code 命令行工具，并能在终端里直接运行 claude。", codex: "需要先在电脑上安装 Codex 命令行工具，并能在终端里直接运行 codex。"};
  const drawEng = async () => {
    const engs = await api("/engines");
    $("#st-eng").innerHTML = Object.entries(engs).map(([k, e]) => {
      const a = e.account || {}, lv = e.last_verify;
      return `<div class="card" data-k="${k}">
        <div class="row"><h3 style="margin:0">${ENGINE_LABEL[k]}</h3>
          <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? "已安装" : "未安装"}</span>
          ${e.installed ? `<span class="pill ${e.logged_in ? "ok" : "warn"}">${e.logged_in ? "已登录" : "未登录"}</span>` : ""}
          <span class="grow"></span><span class="small faint">${h(e.version || "")}</span></div>
        ${!e.installed ? `<p class="small sub">${INSTALL[k]}安装后刷新本页。</p>` : `
        <table style="margin-top:10px"><tbody>
          <tr><td class="sub" style="width:140px">当前账户</td><td>${h(a.email || "未识别")}</td></tr>
          <tr><td class="sub">工作区 / 组织</td><td>${h(a.org || "未识别")}</td></tr>
          <tr><td class="sub">登录方式</td><td>${h(a.method || e.status_text || "")}</td></tr>
          <tr><td class="sub">身份类型</td><td>${a.kind === "请核对工作区" ? `<span class="pill warn">请核对工作区</span> <span class="small sub">无法判断是个人还是组织账户，请自行确认</span>` : h(a.kind || "")}</td></tr>
          ${e.default_model ? `<tr><td class="sub">CLI 默认模型</td><td>${h(e.default_model)}</td></tr>` : ""}
          <tr><td class="sub">可用额度</td><td class="sub">${h(e.quota)}</td></tr>
          <tr><td class="sub">连接验证</td><td>${lv ? (lv.ok ? `<span class="pill ok">模型可用</span> <span class="small sub">实际模型 ${h(lv.model)} · ${lv.seconds} 秒 · ${fmtTime(lv.at)}</span>` :
            `<span class="pill err">${h(lv.friendly || "验证失败")}</span> <span class="small sub">${h(lv.error)}</span>`) : `<span class="sub">未验证（登录成功不代表模型可用）</span>`}</td></tr>
        </tbody></table>
        ${arr(e.warnings).map(w => `<div class="banner warn" style="margin:8px 0 0">${h(w)}</div>`).join("")}
        <div class="row wrap" style="margin-top:12px">
          ${e.logged_in ? `<button data-a="logout">退出登录</button>` : `<button class="primary" data-a="login">官方登录</button>`}
          <input type="text" data-model placeholder="验证用模型（留空用默认）" style="max-width:220px">
          <button data-a="verify">验证连接</button>
          <span class="small faint">验证会发送一条很短的测试请求，可能消耗少量额度。失败不会自动切换其他凭证或模型。</span></div>
        <div data-login></div>
        <details style="margin-top:12px"><summary>高级诊断</summary>
          <div class="small">命令路径：<span class="mono">${h(e.path)}</span></div>
          <div class="small" style="margin-top:4px">环境变量中的凭证或网关：${arr(e.env_findings).length ? arr(e.env_findings).map(x => `<span class="mono">${h(x)}</span>`).join("、") : "无"}</div>
          <label class="small" style="display:block;margin-top:6px"><input type="checkbox" data-allow ${e.allow_env ? "checked" : ""}>
            允许调用时使用这些环境变量（默认屏蔽，只用 CLI 官方登录的账户）</label>
          <div class="small faint">只显示变量名，不显示值。本工具不保存任何登录令牌。</div></details>`}
      </div>`;
    }).join("");
    $$("#st-eng [data-k]").forEach(card => {
      const k = card.dataset.k;
      $$("[data-a]", card).forEach(b => b.onclick = async () => {
        const a = b.dataset.a;
        if (a === "logout" && !await confirmBox("退出登录", `退出 ${ENGINE_LABEL[k]} 的登录。这会影响你在终端里使用这个 CLI。`, "退出")) return;
        b.disabled = true; b.textContent = a === "verify" ? "验证中…" : "处理中…";
        try {
          const r = await post(`/engines/${k}/${a}`, a === "verify" ? {model: $("[data-model]", card).value} : {});
          if (a === "login") return pollLogin(k, card);
          if (a === "verify") toast(r.ok ? "连接正常" : (r.friendly || "验证失败"), !r.ok);
        } catch (e) { toast(e.message, true); }
        refreshOverview(); drawEng();
      });
      const al = $("[data-allow]", card);
      al && (al.onchange = async () => {
        if (al.checked && !await confirmBox("允许使用环境变量凭证", "开启后，CLI 可能改用环境变量里的 API Key 或公司网关，而不是你官方登录的账户。确定吗？", "允许")) { al.checked = false; return; }
        await post(`/engines/${k}/allow_env`, {allow: al.checked}); drawEng();
      });
    });
  };
  const pollLogin = (k, card) => {
    const box = $("[data-login]", card);
    const tick = async () => {
      const s = await api(`/engines/${k}/login_state`);
      box.innerHTML = `<div class="banner info" style="margin-top:10px;display:block">
        ${s.running ? `已启动官方登录流程，请在打开的浏览器页面完成登录。${s.urls.length ? `<div class="small" style="margin-top:4px">如果浏览器没有自动打开：${s.urls.map(u => `<a href="${h(u)}" target="_blank" rel="noopener">${h(u.slice(0, 80))}…</a>`).join("<br>")}</div>` : ""}
          <div style="margin-top:6px"><button class="sm" id="lg-x-${k}">取消登录</button></div>` :
          `登录流程已结束（退出码 ${s.exit}）。${s.exit === 0 ? "" : "如果没有成功，可以在终端运行 CLI 的登录命令。"}`}
        ${arr(s.output).length ? `<details style="margin-top:6px"><summary>CLI 输出</summary><div class="loglist">${s.output.map(h).join("\n")}</div></details>` : ""}</div>`;
      const x = $(`#lg-x-${k}`); x && (x.onclick = async () => { await post(`/engines/${k}/login_cancel`); });
      if (!s.running) { clearInterval(t); refreshOverview(); setTimeout(drawEng, 800); }
    };
    const t = every(1500, tick); tick();
  };
  const drawData = async () => {
    const d = await api("/data");
    $("#st-data").innerHTML = `<div class="card"><table><tbody>
      <tr><td class="sub" style="width:140px">存储目录</td><td class="mono">${h(d.dir)}</td></tr>
      <tr><td class="sub">磁盘占用</td><td>${fmtBytes(d.bytes)} · ${d.books} 本书 · ${d.jobs} 个任务 · ${d.comparisons} 个比较</td></tr></tbody></table>
      <div class="row wrap" style="margin-top:12px"><a href="/api/backup"><button>下载完整备份</button></a>
        <button id="st-restore">从备份恢复</button><input type="file" id="st-file" accept=".zip" hidden></div>
      <div class="small sub" style="margin-top:8px">完整备份包含导入的小说副本与全部研究数据（目录、报告、核对、比较），<b>不包含</b>任何登录密钥或令牌；恢复后需要重新连接模型。</div>
      <div class="small sub">单份报告在报告页导出：Markdown 便于阅读，JSON 保留结构化字段与稳定 ID；默认不包含原作全文。</div></div>`;
    $("#st-restore").onclick = () => $("#st-file").click();
    $("#st-file").onchange = async e => {
      const f = e.target.files[0]; if (!f) return;
      if (!await confirmBox("从备份恢复", "恢复会用备份替换当前所有数据（当前数据会先移到旁边的文件夹保留）。需要先暂停所有任务。", "恢复", true)) return;
      const buf = await f.arrayBuffer();
      await guard(() => api("/restore", {body: buf}));
      toast("已恢复"); setTimeout(() => location.reload(), 600);
    };
  };
  drawData(); await drawEng();
}, "settings");
