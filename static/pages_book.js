/* P01 书库 · P02 导入与章节确认 · P03 书籍详情 */
"use strict";

/* ================================================================ P01 书库 */
routeAdd("/library", async (main, q) => {
  const [books, groups] = await Promise.all([api("/books"), api("/groups")]);
  const sel = new Set();
  const total = books.length;
  main.innerHTML = `<div class="page">
    <div class="head"><div><h1>我的书库</h1><div class="meta">${total ? `${total} 本书` : ""}</div></div>
      <div class="actions"><button class="primary" onclick="go('#/import')">导入小说</button></div></div>
    ${total ? `<div class="row" style="margin-bottom:12px">
      <input type="search" id="lb-q" placeholder="搜索书名 / 作者" style="max-width:280px">
      <select id="lb-g" style="max-width:180px"><option value="">分组：全部</option>${groups.map(g => `<option>${h(g)}</option>`).join("")}</select>
      <span class="grow"></span><span class="sub small" id="lb-sel"></span>
      <button id="lb-cmp" disabled>加入比较</button></div>
    <div class="card tablecard"><table><thead><tr><th style="width:34px"></th><th>书名</th><th>分组</th><th>字数 / 章节</th><th>分析覆盖</th><th style="width:120px"></th></tr></thead>
      <tbody id="lb-rows"></tbody></table></div>` :
    `<div class="card empty"><h2>书库还是空的</h2>
      <p>导入本地 TXT 小说（支持 UTF-8 / GBK / Big5，可一次选多本）。<br>导入后先检查目录，确认章节切分无误，就可以开始分析。</p>
      <p><button class="primary" onclick="go('#/import')">导入 TXT</button></p>
      <p class="small faint">不需要注册本工具账号；没有连接模型时也可以导入、整理目录和阅读。</p></div>`}
  </div>`;
  if (!total) return;
  const actionBtn = b => ({
    check: `<button class="sm" data-go="#/book/${b.id}/directory">检查目录</button>`,
    analyze: `<button class="sm primary" data-go="#/book/${b.id}/new">开始分析</button>`,
    report: `<button class="sm" data-go="#/book/${b.id}/reports">查看报告</button>`,
    tasks: `<button class="sm" data-go="#/book/${b.id}/jobs">查看任务</button>`,
  })[b.action];
  const draw = () => {
    const kw = $("#lb-q").value.trim(), g = $("#lb-g").value;
    const rows = books.filter(b => (!kw || (b.title + b.author).includes(kw)) && (!g || b.grp === g));
    $("#lb-rows").innerHTML = rows.map(b => `<tr class="click" data-id="${b.id}">
      <td><input type="checkbox" data-sel="${b.id}" ${sel.has(b.id) ? "checked" : ""}></td>
      <td><b>${h(b.title)}</b>${b.author ? `<div class="small sub">${h(b.author)}</div>` : ""}</td>
      <td>${b.grp ? `<span class="pill accent">${h(b.grp)}</span>` : `<span class="faint">未分组</span>`}</td>
      <td class="num">${b.version_status === "confirmed" ? `${fmtChars(b.n_chars)} / ${b.n_chapters} 章` : `<span class="pill warn">待确认目录</span>`}</td>
      <td>${b.version_status !== "confirmed" ? `<span class="faint">—</span>` : b.covered ? `${b.covered}/${b.total} 章已分析` :
        b.active ? `<span class="pill info">分析中</span>` : `<span class="faint">尚未分析</span>`}</td>
      <td style="text-align:right">${actionBtn(b)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">没有匹配的书</td></tr>`;
    $$("#lb-rows tr.click").forEach(tr => tr.onclick = e => {
      if (e.target.closest("input,button")) return;
      go(`#/book/${tr.dataset.id}`);
    });
    $$("#lb-rows [data-go]").forEach(b => b.onclick = () => go(b.dataset.go));
    $$("#lb-rows [data-sel]").forEach(c => c.onchange = () => {
      const id = +c.dataset.sel; c.checked ? sel.add(id) : sel.delete(id); updSel();
    });
  };
  const updSel = () => {
    $("#lb-sel").textContent = sel.size ? `已选 ${sel.size} 本` : "";
    $("#lb-cmp").disabled = sel.size < 1;
  };
  $("#lb-q").oninput = draw; $("#lb-g").onchange = draw;
  $("#lb-cmp").onclick = () => go(`#/compare/new?books=${[...sel].join(",")}`);
  draw(); updSel();
}, "library");

/* ================================================================ P02 导入 */
routeAdd("/import", async (main) => {
  const S = state.importSession || (state.importSession = {step: 1, files: []});
  const groups = await api("/groups");
  const steps = ["选文件", "检查文本", "确认目录"];
  const head = `<div class="crumb"><a href="#/library">书库</a> / 导入小说</div>
    <div class="head"><h1>导入小说</h1><div class="actions"><button onclick="state.importSession=null;go('#/library')">完成并返回书库</button></div></div>
    <div class="steps">${steps.map((s, i) => `<div class="${S.step === i + 1 ? "on" : S.step > i + 1 ? "done" : ""}">${i + 1}. ${s}</div>`).join("")}</div>`;
  const imported = () => S.files.filter(f => f.book);

  if (S.step === 1) {
    main.innerHTML = `<div class="page">${head}
      <div class="drop" id="im-drop"><b>拖入 TXT 文件，或点击选择</b><div class="small">可一次选择多本；一份失败不影响其他文件。原始文件不会被修改。</div></div>
      <input type="file" id="im-file" accept=".txt,text/plain" multiple hidden>
      <div class="card" style="margin-top:14px;padding:6px 16px" id="im-list"></div>
      <div class="row" style="justify-content:flex-end"><button class="primary" id="im-next">下一步：检查文本</button></div></div>`;
    const list = () => {
      $("#im-list").innerHTML = S.files.length ? S.files.map((f, i) => `<div class="row" style="padding:8px 0;border-bottom:1px solid var(--line2)">
        <div class="grow"><b>${h(f.name)}</b> <span class="sub small">${fmtBytes(f.size)}</span></div>
        ${f.status === "uploading" ? `<span class="pill info">导入中…</span>` :
          f.status === "ok" ? `<span class="pill ok">已导入</span>` :
          f.status === "error" ? `<span class="pill err">${h(f.error)}</span>` :
          f.status === "duplicate" ? `<span class="small sub">已存在同一文件：</span>${f.existing.map(b => `<a href="#/book/${b.id}">${h(b.title)}</a>`).join("、")}
            <button class="sm" data-copy="${i}">创建独立副本</button><button class="sm ghost" data-skip="${i}">跳过</button>` :
          f.status === "skipped" ? `<span class="pill">已跳过</span>` : `<span class="pill">等待</span>`}
      </div>`).join("") : `<div class="sub small" style="padding:10px 0">还没有选择文件</div>`;
      $$("[data-copy]").forEach(b => b.onclick = () => upload(S.files[+b.dataset.copy], true));
      $$("[data-skip]").forEach(b => b.onclick = () => { S.files[+b.dataset.skip].status = "skipped"; list(); });
      $("#im-next").disabled = !imported().length || S.files.some(f => f.status === "uploading");
    };
    const upload = async (f, force = false) => {
      f.status = "uploading"; list();
      try {
        const r = await api(`/import?filename=${encodeURIComponent(f.name)}${force ? "&force=1" : ""}`, {body: f.buf});
        if (r.status === "ok") { f.status = "ok"; f.book = r.book; }
        else if (r.status === "duplicate") { f.status = "duplicate"; f.existing = r.existing; }
        else { f.status = "error"; f.error = r.error || "导入失败"; }
      } catch (e) { f.status = "error"; f.error = e.message; }
      list();
    };
    const add = async files => {
      for (const file of files) {
        const f = {name: file.name, size: file.size, status: "wait"};
        if (!/\.txt$/i.test(file.name)) { f.status = "error"; f.error = "第一版只支持 TXT"; S.files.push(f); continue; }
        f.buf = await file.arrayBuffer();
        S.files.push(f);
      }
      list();
      for (const f of S.files.filter(x => x.status === "wait")) await upload(f);
    };
    const drop = $("#im-drop");
    drop.onclick = () => $("#im-file").click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); add([...e.dataTransfer.files]); };
    $("#im-file").onchange = e => add([...e.target.files]);
    $("#im-next").onclick = () => { S.step = 2; render(); };
    list();
    return;
  }

  if (S.step === 2) {
    const books = imported();
    const infos = await Promise.all(books.map(f => api(`/books/${f.book.id}/inspect`)));
    main.innerHTML = `<div class="page">${head}
      ${books.map((f, i) => {
        const b = f.book, info = infos[i], c = info.check;
        return `<div class="card" data-book="${b.id}">
          <div class="grid3">
            <div class="field"><label>书名</label><input type="text" data-k="title" value="${h(b.title)}"></div>
            <div class="field"><label>作者</label><input type="text" data-k="author" value="${h(b.author)}" placeholder="可留空"></div>
            <div class="field"><label>分组</label><select data-k="grp"><option value="">未分组</option>
              ${groups.map(g => `<option ${b.grp === g ? "selected" : ""}>${h(g)}</option>`).join("")}<option value="__new">＋ 新增分组…</option></select></div>
          </div>
          <div class="row wrap small sub" style="margin-bottom:8px">
            <span>文件：${h(info.file_name)}</span><span>大小：${fmtBytes(info.file_size)}</span><span>编码：${h(info.encoding)}</span>
            <span>正文约 ${fmtChars(c.n_chars)}</span>
            ${c.level === "ok" ? `<span class="pill ok">文本检查通过</span>` : c.level === "warn" ? `<span class="pill warn">请核对</span>` : `<span class="pill err">不能用于分析</span>`}
          </div>
          ${arr(c.issues).map(x => `<div class="banner ${c.level === "block" ? "err" : "warn"}">${h(x)}</div>`).join("")}
          <details ${c.level !== "ok" ? "open" : ""}><summary>正文预览</summary>
            <div style="white-space:pre-wrap;font-size:13px;background:#fff;border:1px solid var(--line);border-radius:6px;padding:10px;max-height:220px;overflow:auto">${h(info.preview)}</div></details>
        </div>`;
      }).join("")}
      <div class="row" style="justify-content:space-between"><button onclick="state.importSession.step=1;render()">上一步</button>
        <button class="primary" id="im-next2">下一步：确认目录</button></div></div>`;
    $$("[data-book]").forEach(card => {
      const id = card.dataset.book;
      $$("[data-k]", card).forEach(inp => inp.onchange = async () => {
        let v = inp.value;
        if (inp.dataset.k === "grp" && v === "__new") {
          v = await promptBox("新增分组", "分组名称");
          if (!v) { inp.value = ""; return; }
          inp.insertAdjacentHTML("afterbegin", `<option>${h(v)}</option>`); inp.value = v;
        }
        await guard(() => post(`/books/${id}`, {[inp.dataset.k]: v}));
        const f = books.find(x => x.book.id == id); f.book[inp.dataset.k] = v;
        toast("已保存");
      });
    });
    $("#im-next2").onclick = () => { S.step = 3; render(); };
    return;
  }

  const books = imported();
  main.innerHTML = `<div class="page">${head}
    <div class="banner info">每本书需要单独确认目录：检查章节切分、合并或拆分章节、排除广告段。未确认的目录可以阅读，但不能分析。</div>
    <div class="card tablecard"><table><tbody>${books.map(f => `<tr><td><b>${h(f.book.title)}</b></td>
      <td class="num">${h(f.book.grp || "未分组")}</td><td style="text-align:right">
      <button class="primary sm" onclick="go('#/book/${f.book.id}/directory')">确认目录</button></td></tr>`).join("")}</tbody></table></div>
    <div class="row" style="justify-content:space-between"><button onclick="state.importSession.step=2;render()">上一步</button>
      <button onclick="state.importSession=null;go('#/library')">稍后再确认（保存草稿）</button></div></div>`;
}, "library");

/* ================================================================ 目录确认 */
routeAdd("/book/(\\d+)/directory", async (main, bid, q) => {
  const book = await api(`/books/${bid}`);
  const draft = book.versions.find(v => v.status === "draft");
  const vid = +(q.v || (draft ? draft.id : book.current_version));
  let V = await api(`/versions/${vid}`);
  const rules = await api("/rules");
  const inspect = await api(`/books/${bid}/inspect`);
  let cur = +(q.ch || 0) || (V.chapters[0] && V.chapters[0].id);
  let full = false;
  const editable = V.status === "draft";

  const shell = () => {
    const c = V.checks;
    main.innerHTML = `<div class="page wide">
      <div class="crumb"><a href="#/library">书库</a> / <a href="#/book/${bid}">${h(book.title)}</a> / 目录</div>
      <div class="head"><div><h1>${editable ? "确认目录" : "目录"}</h1>
        <div class="meta">正文版本 v${V.vno} · ${editable ? `<span class="pill warn">草稿</span>` : `<span class="pill ok">已确认</span>`}
          · 识别 ${V.chapters.length} 章 · ${fmtChars(V.n_chars)}
          ${V.split_rule.ignored_toc ? ` · 已忽略 ${V.split_rule.ignored_toc} 个疑似目录行` : ""}</div></div>
        <div class="actions">${editable ? `
          ${V.parent ? `<button class="ghost" id="dv-discard">丢弃此草稿</button>` : ""}
          <button onclick="go('#/book/${bid}')">保存草稿</button>
          <button class="primary" id="dv-confirm" ${inspect.check.level === "block" ? "disabled" : ""}>确认并进入书籍</button>` :
          `<button id="dv-newdraft">修改目录（生成新版本）</button><button class="primary" onclick="go('#/book/${bid}')">返回书籍</button>`}</div></div>
      ${inspect.check.level === "block" ? `<div class="banner err">${h(inspect.check.issues.join("；"))}。乱码或空文件不能创建分析任务，请换一个文件重新导入。</div>` :
        inspect.check.level === "warn" ? `<div class="banner warn">${h(inspect.check.issues.join("；"))}</div>` : ""}
      ${V.segmented ? `<div class="banner warn">没有识别到章节标题，已按长度临时分段。这些分段会标注为「文本分段」，不代表原作章节。可以换一种切分规则再试。</div>` : ""}
      ${c.n_short || c.n_long || c.n_dup ? `<div class="banner warn">需要留意：${[c.n_short && `${c.n_short} 章字数过少`, c.n_long && `${c.n_long} 章字数异常偏多`, c.n_dup && `${c.n_dup} 章可能重复标题`].filter(Boolean).join("，")}（中位数 ${c.median} 字）</div>` : ""}
      ${editable ? `<div class="card row wrap" style="padding:10px 14px">
        <span class="small sub">切分规则</span>
        <select id="dv-rule" style="max-width:360px">${rules.map(r => `<option value="${r.key}" ${V.split_rule.rule === r.key ? "selected" : ""}>${h(r.label)}</option>`).join("")}</select>
        <input type="text" id="dv-custom" placeholder="自定义正则，例如 ^第.+章.*$" style="max-width:280px" value="${h(V.split_rule.custom || "")}">
        <button id="dv-resplit">重新切分</button>
        <span class="small faint">重新切分会丢弃当前草稿上的手工调整</span></div>` : ""}
      <div class="split"><div class="card toc" id="dv-toc"></div><div class="card" id="dv-pane"></div></div></div>`;
    $("#dv-custom") && ($("#dv-custom").style.display = $("#dv-rule").value === "custom" ? "" : "none");
    $("#dv-rule") && ($("#dv-rule").onchange = () => $("#dv-custom").style.display = $("#dv-rule").value === "custom" ? "" : "none");
    $("#dv-resplit") && ($("#dv-resplit").onclick = async () => {
      if (!await confirmBox("重新切分", "按新规则重新切分会丢弃当前草稿上的合并、拆分、重命名和排除操作。继续吗？", "重新切分")) return;
      const r = await guard(() => post(`/versions/${vid}/op`, {op: "resplit", rule: $("#dv-rule").value, custom: $("#dv-custom").value}));
      go(`#/book/${bid}/directory?v=${r.version_id}`);
    });
    $("#dv-confirm") && ($("#dv-confirm").onclick = async () => {
      const ex = V.chapters.filter(x => x.excluded).length;
      if (!await confirmBox("确认目录", `将以 <b>${V.chapters.length - ex}</b> 章（排除 ${ex} 章）作为正文版本 v${V.vno}。确认后这个版本不再改动；之后修改目录会生成新版本，已有报告保持不变。`, "确认")) return;
      await guard(() => post(`/versions/${vid}/confirm`));
      toast("目录已确认"); state.importSession = state.importSession && {...state.importSession};
      go(`#/book/${bid}`);
    });
    $("#dv-newdraft") && ($("#dv-newdraft").onclick = async () => {
      const r = await guard(() => post(`/versions/${vid}/newdraft`));
      go(`#/book/${bid}/directory?v=${r.version_id}`);
    });
    $("#dv-discard") && ($("#dv-discard").onclick = async () => {
      if (!await confirmBox("丢弃草稿", "丢弃这个草稿，书籍会回到上一个已确认的正文版本。", "丢弃", true)) return;
      await guard(() => post(`/versions/${vid}/discard`)); go(`#/book/${bid}`);
    });
    drawToc(); drawPane();
  };
  const KIND = {chapter: "正文", segment: "文本分段", front: "开篇 / 简介", extra: "番外"};
  const drawToc = () => {
    $("#dv-toc").innerHTML = V.chapters.map(c => `<div class="it ${c.id === cur ? "on" : ""} ${c.excluded ? "ex" : ""}" data-id="${c.id}">
      <span class="seqn">${c.seq}</span><span class="t">${h(c.title)}</span>
      ${c.kind !== "chapter" ? `<span class="tag">${KIND[c.kind]}</span>` : ""}
      ${c.flags.filter(f => f !== "文本分段").map(f => `<span class="tag warn">${h(f)}</span>`).join("")}
      ${c.excluded ? `<span class="tag">已排除</span>` : ""}
      <span class="small faint">${c.n_chars}</span></div>`).join("");
    $$("#dv-toc .it").forEach(el => el.onclick = () => { cur = +el.dataset.id; full = false; drawToc(); drawPane(); });
  };
  const drawPane = async () => {
    const pane = $("#dv-pane");
    if (!cur) { pane.innerHTML = `<div class="empty">没有章节</div>`; return; }
    const d = await api(`/chapters/${cur}/text?all=1`);
    const c = V.chapters.find(x => x.id === cur) || d.chapter;
    const ps = d.paragraphs;
    const shown = full || ps.length <= 14 ? ps : [...ps.slice(0, 7), null, ...ps.slice(-6)];
    pane.innerHTML = `
      <div class="row wrap" style="margin-bottom:12px">
        ${editable ? `<input type="text" id="dv-title" value="${h(c.title)}" style="max-width:360px"><button class="sm" id="dv-rename">重命名</button>
          <select id="dv-kind" style="max-width:130px">${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${c.kind === k ? "selected" : ""}>${v}</option>`).join("")}</select>` :
          `<h3 style="margin:0">${h(c.title)}</h3>`}
        <span class="grow"></span><span class="small sub">${ps.length} 段 · ${c.n_chars} 字</span>
        ${editable ? `<button class="sm" id="dv-merge">与下一章合并</button>
          <button class="sm" id="dv-ex">${c.excluded ? "恢复本章" : "排除本章"}</button>` : ""}
      </div>
      ${c.flags && c.flags.length ? `<div class="small" style="margin-bottom:8px">${c.flags.map(f => `<span class="tag warn">${h(f)}</span>`).join("")}</div>` : ""}
      ${editable ? `<div class="small faint" style="margin-bottom:8px">鼠标移到段落上：可以「从这里拆为新章」或「排除此段」（如广告、站点声明）。</div>` : ""}
      <div>${shown.map(p => p === null ? `<div class="gapline">中间省略 ${ps.length - 13} 段 · <a href="javascript:void 0" id="dv-full">展开全部</a></div>` :
        `<div class="para ${p.excluded ? "ex" : ""}"><span class="lb">${c.seq}-${p.pseq}</span><span class="tx">${h(p.text)}</span>
          ${editable ? `<span class="ops">${p.pseq > 1 ? `<button class="sm" data-split="${p.id}">从这里拆为新章</button>` : ""}
            <button class="sm" data-exp="${p.id}" data-v="${p.excluded ? 0 : 1}">${p.excluded ? "恢复此段" : "排除此段"}</button></span>` : ""}</div>`).join("")}</div>`;
    $("#dv-full") && ($("#dv-full").onclick = () => { full = true; drawPane(); });
    const reload = async (keep = true) => { V = await api(`/versions/${vid}`); if (!V.chapters.find(x => x.id === cur)) cur = V.chapters[0]?.id; drawToc(); drawPane(); };
    if (!editable) return;
    $("#dv-rename").onclick = async () => { await guard(() => post(`/versions/${vid}/op`, {op: "rename", chapter_id: cur, title: $("#dv-title").value})); reload(); };
    $("#dv-kind").onchange = async () => { await guard(() => post(`/versions/${vid}/op`, {op: "kind", chapter_id: cur, kind: $("#dv-kind").value})); reload(); };
    $("#dv-merge").onclick = async () => { await guard(() => post(`/versions/${vid}/op`, {op: "merge_next", chapter_id: cur})); toast("已合并"); reload(); };
    $("#dv-ex").onclick = async () => { await guard(() => post(`/versions/${vid}/op`, {op: "exclude_chapter", chapter_id: cur, excluded: !c.excluded})); reload(); };
    $$("[data-split]").forEach(b => b.onclick = async () => {
      const r = await guard(() => post(`/versions/${vid}/op`, {op: "split_at", paragraph_id: +b.dataset.split}));
      cur = r.chapter_id; toast("已拆分为新章节，可以重命名"); reload();
    });
    $$("[data-exp]").forEach(b => b.onclick = async () => {
      await guard(() => post(`/versions/${vid}/op`, {op: "exclude_para", paragraph_id: +b.dataset.exp, excluded: b.dataset.v === "1"})); reload();
    });
  };
  shell();
}, "library");

/* ================================================================ P03 书籍详情 */
routeAdd("/book/(\\d+)(?:/(overview|chapters|reports|jobs))?", async (main, bid, tab) => {
  tab = tab || "overview";
  const b = await api(`/books/${bid}`);
  const confirmed = b.version_status === "confirmed";
  const draftV = b.versions.find(v => v.status === "draft");
  main.innerHTML = `<div class="page">
    <div class="crumb"><a href="#/library">书库</a> / ${h(b.title)}</div>
    <div class="head"><div><h1>${h(b.title)}</h1>
      <div class="meta">${h(b.author || "作者未填写")} · ${b.grp ? `<span class="pill accent">${h(b.grp)}</span>` : "未分组"}
        · ${fmtChars(b.n_chars)} · ${b.n_chapters} 章 · 正文版本 v${b.vno} ${confirmed ? "" : `<span class="pill warn">目录待确认</span>`}</div></div>
      <div class="actions">
        <button class="ghost" id="bk-edit">编辑信息</button>
        <button id="bk-dir">${confirmed ? (draftV ? "继续修改目录" : "查看目录") : "检查目录"}</button>
        <button class="primary" ${confirmed ? "" : "disabled title='先确认目录'"} onclick="go('#/book/${bid}/new')">新建分析</button>
      </div></div>
    ${!confirmed ? `<div class="banner warn"><span class="grow">目录尚未确认：可以阅读正文，但不能分析。</span><button class="sm" onclick="go('#/book/${bid}/directory')">去确认目录</button></div>` : ""}
    ${draftV && confirmed ? `<div class="banner info"><span class="grow">有一个未确认的目录草稿（v${draftV.vno}）。确认前，分析仍使用当前版本 v${b.vno}。</span><button class="sm" onclick="go('#/book/${bid}/directory?v=${draftV.id}')">打开草稿</button></div>` : ""}
    <div class="tabs">${[["overview", "概览"], ["chapters", "章节与正文"], ["reports", "分析报告"], ["jobs", "任务记录"]]
      .map(([k, t]) => `<a href="#/book/${bid}/${k}" class="${tab === k ? "on" : ""}">${t}</a>`).join("")}</div>
    <div id="bk-body"></div></div>`;
  $("#bk-dir").onclick = () => go(`#/book/${bid}/directory${draftV ? "?v=" + draftV.id : ""}`);
  $("#bk-edit").onclick = () => editBook(b);
  const body = $("#bk-body");
  const reports = b.jobs.filter(j => ["done", "partial"].includes(j.status));

  if (tab === "overview") {
    const latest = reports[0];
    const pct = b.total ? Math.round(100 * b.covered / b.total) : 0;
    body.innerHTML = `<div class="grid3">
      <div class="card"><h3>最新报告</h3>${latest ? `<div><a href="#/reader/${latest.id}"><b>${h(latest.title)}</b></a></div>
        <div class="small sub">${h(latest.template)} · ${ENGINE_LABEL[latest.engine]} · ${fmtTime(latest.finished || latest.created)}</div>
        <div style="margin-top:6px">${pill(latest.status)}</div>` : `<div class="sub small">还没有报告</div>
        ${confirmed ? `<button class="sm primary" style="margin-top:8px" onclick="go('#/book/${bid}/new')">新建分析</button>` : ""}`}</div>
      <div class="card"><h3>分析覆盖 <span class="sub small">（当前正文版本）</span></h3><div class="stat">${b.covered}<span class="sub" style="font-size:14px"> / ${b.total} 章</span></div>
        <div class="bar" style="margin-top:6px"><div style="width:${pct}%"></div></div>
        <div class="small faint" style="margin-top:6px">按完成的不同章节统计，重试不重复计入</div></div>
      <div class="card"><h3>待核对结论</h3><div class="stat">${b.pending_review}</div>
        <div class="small sub">${reports.reduce((n, j) => n + (j.doubts || 0), 0)} 条标记为有疑问</div></div></div>
      <div class="card"><h3>我的研究笔记 <span class="sub small">（单独保存，不会混入模型结论）</span></h3>
        <textarea id="bk-notes" rows="7" placeholder="读这本书的感受、想验证的问题、想借鉴的写法……">${h(b.notes)}</textarea>
        <div class="small faint" id="bk-notes-st" style="margin-top:4px">自动保存</div></div>`;
    let t;
    $("#bk-notes").oninput = () => { clearTimeout(t); $("#bk-notes-st").textContent = "保存中…";
      t = setTimeout(async () => { await post(`/books/${bid}`, {notes: $("#bk-notes").value}); $("#bk-notes-st").textContent = "已保存"; }, 700); };
    return;
  }

  if (tab === "chapters") {
    const V = await api(`/versions/${b.current_version}`);
    const done = new Set(await api(`/books/${bid}/covered`));
    let cur = V.chapters[0]?.id;
    const picked = new Set();
    body.innerHTML = `<div class="split"><div class="card toc" id="bc-toc"></div><div class="card" id="bc-pane"></div></div>`;
    const drawToc = () => {
      $("#bc-toc").innerHTML = `<div class="row" style="padding:8px 12px;border-bottom:1px solid var(--line)">
        <span class="small sub grow">已选 ${picked.size} 章</span>
        <button class="sm primary" id="bc-go" ${picked.size && confirmed ? "" : "disabled"}>从选中章节开始分析</button></div>` +
        V.chapters.map(c => `<div class="it ${c.id === cur ? "on" : ""} ${c.excluded ? "ex" : ""}" data-id="${c.id}">
          <input type="checkbox" data-pick="${c.id}" ${picked.has(c.id) ? "checked" : ""} ${c.excluded ? "disabled" : ""}>
          <span class="seqn">${c.seq}</span><span class="t">${h(c.title)}</span>
          ${done.has(c.id) ? `<span class="pill ok" style="font-size:11px">已分析</span>` : ""}<span class="small faint">${c.n_chars}</span></div>`).join("");
      $$("#bc-toc .it").forEach(el => el.onclick = e => { if (e.target.matches("input")) return; cur = +el.dataset.id; drawToc(); drawPane(); });
      $$("[data-pick]").forEach(x => x.onchange = () => { x.checked ? picked.add(+x.dataset.pick) : picked.delete(+x.dataset.pick); drawToc(); });
      $("#bc-go").onclick = () => go(`#/book/${bid}/new?scope=chapters&ids=${[...picked].join(",")}`);
    };
    const drawPane = async () => {
      const d = await api(`/chapters/${cur}/text`);
      $("#bc-pane").innerHTML = `<h3>${h(d.chapter.title)} <span class="sub small">${d.chapter.n_chars} 字</span></h3>` +
        d.paragraphs.map(p => `<div class="para"><span class="lb">${p.label}</span><span class="tx">${h(p.text)}</span></div>`).join("");
    };
    drawToc(); drawPane();
    return;
  }

  if (tab === "reports") {
    if (!reports.length) { body.innerHTML = `<div class="card empty">还没有可查看的报告。${confirmed ? `<p><button class="primary" onclick="go('#/book/${bid}/new')">新建分析</button></p>` : ""}</div>`; return; }
    const pickedR = new Set();
    body.innerHTML = `<div class="row" style="margin-bottom:10px"><span class="small sub grow">每次任务生成一个报告版本；默认显示最近一次有效结果。勾选两个可以比较历史版本。</span>
      <button class="sm" id="rp-cmp" disabled>比较所选两个版本</button></div>
      <div class="card tablecard"><table><thead><tr><th></th><th>报告</th><th>拆解方法</th><th>模型</th><th>正文版本</th><th>状态</th><th>时间</th><th></th></tr></thead><tbody>
      ${reports.map((j, i) => `<tr><td><input type="checkbox" data-r="${j.id}"></td>
        <td><a href="#/reader/${j.id}"><b>${h(j.title)}</b></a>${i === 0 ? ` <span class="pill accent">最新</span>` : ""}
          ${j.unchecked ? `<div class="small sub">${j.unchecked} 条待核对${j.doubts ? `，${j.doubts} 条有疑问` : ""}</div>` : ""}</td>
        <td>${h(j.template)}</td><td class="num">${ENGINE_LABEL[j.engine]}<div class="small">${h(j.model || "默认模型")}</div></td>
        <td class="num">v${j.vno}${j.version_id !== b.current_version ? ` <span class="tag warn">旧版本</span>` : ""}</td>
        <td>${pill(j.status)}</td><td class="num">${fmtTime(j.finished || j.created)}</td>
        <td style="text-align:right;white-space:nowrap"><a class="small" href="/api/jobs/${j.id}/export.md">MD</a> · <a class="small" href="/api/jobs/${j.id}/export.json">JSON</a></td></tr>`).join("")}
      </tbody></table></div>`;
    $$("[data-r]").forEach(c => c.onchange = () => { c.checked ? pickedR.add(+c.dataset.r) : pickedR.delete(+c.dataset.r); $("#rp-cmp").disabled = pickedR.size !== 2; });
    $("#rp-cmp").onclick = () => compareVersions(...pickedR);
    return;
  }

  body.innerHTML = b.jobs.length ? `<div class="card tablecard"><table><thead><tr><th>范围</th><th>拆解方法</th><th>模型</th><th>进度</th><th>状态</th><th>创建时间</th></tr></thead><tbody>
    ${b.jobs.map(j => `<tr class="click" onclick="go('#/task/${j.id}')"><td><b>${h(j.title)}</b><div class="small sub">${scopeText(j.scope)}</div></td>
      <td>${h(j.template)}</td><td class="num">${ENGINE_LABEL[j.engine]}<div class="small">${h(j.model || "默认模型")}</div></td>
      <td class="num">${j.progress.done}/${j.progress.total} 章</td><td>${pill(j.status)}</td><td class="num">${fmtTime(j.created)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="card empty">还没有任务</div>`;
}, "library");

async function editBook(b) {
  const groups = await api("/groups");
  const m = modal(`<h2>编辑书籍信息</h2>
    <div class="field"><label>书名</label><input type="text" id="eb-t" value="${h(b.title)}"></div>
    <div class="field"><label>作者</label><input type="text" id="eb-a" value="${h(b.author)}"></div>
    <div class="field"><label>分组</label><input type="text" id="eb-g" value="${h(b.grp)}" list="eb-gl" placeholder="选择或输入新分组">
      <datalist id="eb-gl">${groups.map(g => `<option value="${h(g)}">`).join("")}</datalist></div>
    <div class="foot"><button class="danger ghost" id="eb-del" style="margin-right:auto">删除这本书</button><button data-close>取消</button><button class="primary" id="eb-ok">保存</button></div>`);
  $("#eb-ok", m.el).onclick = async () => {
    await guard(() => post(`/books/${b.id}`, {title: $("#eb-t").value, author: $("#eb-a").value, grp: $("#eb-g").value}));
    m.close(); render();
  };
  $("#eb-del", m.el).onclick = async () => { m.close(); deleteBook(b); };
}

async function deleteBook(b) {
  const imp = await api(`/books/${b.id}/impact`);
  if (imp.active) { toast("这本书有正在执行的任务，请先停止任务再删除", true); return; }
  const ok = await confirmBox(`删除《${b.title}》`, `<p>将删除本工具保存的：正文副本、${imp.versions} 个正文版本及章节、${imp.reports} 份报告、${imp.jobs} 条任务记录和核对内容。</p>
    <p>你电脑上的原始 TXT 文件不受影响。</p>
    ${imp.comparisons.length ? `<p>引用这本书的比较报告（${imp.comparisons.map(c => h(c.title)).join("、")}）会标为「来源已删除」，但保留自身快照。</p>` : ""}`, "删除", true);
  if (!ok) return;
  await guard(() => post(`/books/${b.id}/delete`));
  toast("已删除"); go("#/library");
}

async function compareVersions(a, b) {
  const [ra, rb] = await Promise.all([api(`/jobs/${a}/report`), api(`/jobs/${b}/report`)]);
  const col = r => {
    const s = r.summary;
    const head = `<div class="small sub">${h(r.template.name)} v${r.template.version} · ${ENGINE_LABEL[r.job.engine]} ${h(r.job.model || "")} · 正文 v${r.version.vno} · ${fmtTime(r.job.finished)}</div>`;
    if (!s) return head + `<div class="sub" style="margin-top:10px">这个版本没有范围汇总</div>`;
    const secs = [{key: "overview", label: "范围概述"}, ...r.template.sections];
    return head + `<p><b>${h(s.data.title || r.job.title)}</b>：${h(s.data.one_line)}</p>` + secs.map(sc => {
      const items = (s.data.sections || {})[sc.key] || [];
      return items.length ? `<div style="margin-top:10px"><b class="small">${h(sc.label)}</b><ul style="margin:4px 0;padding-left:18px">${items.map(it => `<li class="small">${h(it.claim)}</li>`).join("")}</ul></div>` : "";
    }).join("");
  };
  modal(`<h2>比较历史版本</h2><div class="grid2"><div>${col(ra)}</div><div>${col(rb)}</div></div><div class="foot"><button data-close>关闭</button></div>`, {wide: true});
}
