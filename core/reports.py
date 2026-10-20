"""报告组装、导出（Markdown / JSON）、备份与恢复。"""
from __future__ import annotations

import io
import json
import shutil
import time
import zipfile

from . import db, templates
from .worker import STATUS_LABEL

ENGINE_LABEL = {"claude": "Claude Code", "codex": "Codex"}


def fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


def job_report(job_id: int) -> dict:
    job = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["kind"] != "analysis":
        raise ValueError("报告不存在")
    book = db.q1("SELECT * FROM books WHERE id=?", (job["book_id"],))
    version = db.q1("SELECT * FROM text_versions WHERE id=?", (job["version_id"],))
    tv = templates.get_version(job["template_version_id"])
    units = db.q("SELECT * FROM units WHERE job_id=? ORDER BY seq", (job_id,))
    chap_units = {}
    for u in units:
        if u["kind"] == "chapter":
            chap_units.setdefault(u["chapter_id"], []).append(u)
    ch_ids = list(chap_units)
    chapters = db.q(f"SELECT id,seq,title,kind,n_chars FROM chapters WHERE id IN ({','.join('?' * len(ch_ids))}) ORDER BY seq",
                    ch_ids) if ch_ids else []
    results = {r["chapter_id"]: r for r in db.q("SELECT * FROM results WHERE job_id=? AND kind='chapter'", (job_id,))}
    for c in chapters:
        us = chap_units[c["id"]]
        r = results.get(c["id"])
        c["status"] = "done" if r else ("failed" if any(u["status"] == "failed" for u in us) else
                                        "running" if any(u["status"] == "running" for u in us) else
                                        "uncertain" if any(u["status"] == "uncertain" for u in us) else "pending")
        c["result"] = {"id": r["id"], "data": db.jload(r["data"], {}), "created": r["created"],
                       "usage": db.jload(r["usage"], {})} if r else None
    others = db.q("SELECT r.*, u.label, u.seq useq FROM results r JOIN units u ON u.id=r.unit_id WHERE r.job_id=? "
                  "AND r.kind IN ('segment','summary') ORDER BY u.seq", (job_id,))
    segs = [{"id": r["id"], "label": r["label"], "data": db.jload(r["data"], {})} for r in others if r["kind"] == "segment"]
    summ = next(({"id": r["id"], "data": db.jload(r["data"], {}), "created": r["created"]} for r in others
                 if r["kind"] == "summary"), None)
    rids = [c["result"]["id"] for c in chapters if c["result"]] + [s["id"] for s in segs] + ([summ["id"]] if summ else [])
    reviews = {}
    if rids:
        for rv in db.q(f"SELECT * FROM reviews WHERE result_id IN ({','.join('?' * len(rids))})", rids):
            reviews[f"{rv['result_id']}|{rv['item_key']}"] = rv
    summary_unit = next((u for u in units if u["kind"] == "summary"), None)
    actual = sorted({(db.jload(r["usage"], {}) or {}).get("model") for r in db.q("SELECT usage FROM results WHERE job_id=?", (job_id,))} - {None, ""})
    return {
        "actual_models": actual,
        "job": {**job, "scope": db.jload(job["scope"], {}), "options": db.jload(job["options"], {}),
                "status_label": STATUS_LABEL.get(job["status"], job["status"])},
        "book": book, "version": version,
        "template": {"name": tv["name"], "version": tv["version"], "sections": tv["sections"],
                     "version_id": tv["version_id"], "template_id": tv["template_id"]},
        "chapters": chapters, "segments": segs, "summary": summ,
        "summary_unit": summary_unit, "reviews": reviews,
        "seq_titles": {str(c["seq"]): c["title"] for c in db.q("SELECT seq,title FROM chapters WHERE version_id=?",
                                                                (job["version_id"],))},
    }


def _src(ids, seq_titles):
    out = []
    for i in ids or []:
        cs, ps = i.split("-")
        out.append(f"第{cs}章《{seq_titles.get(cs, '')}》第{ps}段")
    return "；".join(out)


def _items_md(result_id, sections, data, rep, L):
    for s in sections:
        items = (data.get("sections") or {}).get(s["key"]) or []
        if not items:
            continue
        L.append(f"**{s['label']}**")
        L.append("")
        for i, it in enumerate(items):
            rv = rep["reviews"].get(f"{result_id}|{s['key']}:{i}")
            claim = it["claim"]
            tags = [f"证据{it['sufficiency']}"]
            if it.get("inferred"):
                tags.append("推断")
            tags += it.get("flags") or []
            tags.append(rv["status"] if rv else "待核对")
            L.append(f"- {claim}（{'，'.join(tags)}）")
            if rv and rv.get("revision"):
                L.append(f"  - 人工修订：{rv['revision']}")
            if rv and rv.get("note"):
                L.append(f"  - 核对备注：{rv['note']}")
            if it.get("evidence"):
                L.append(f"  - 依据：{_src(it['evidence'], rep['seq_titles'])}")
            if it.get("reason"):
                L.append(f"  - 证据说明：{it['reason']}")
        L.append("")


def job_markdown(job_id: int) -> str:
    rep = job_report(job_id)
    j, b, v, tv = rep["job"], rep["book"], rep["version"], rep["template"]
    title = (rep["summary"]["data"].get("title") if rep["summary"] else None) or j["title"]
    L = [f"# 《{b['title']}》{title}", ""]
    L += [f"- 作者：{b['author'] or '未填写'}　分组：{b['grp'] or '未分组'}",
          f"- 范围：{j['title']}（{len(rep['chapters'])} 章，{sum(c['n_chars'] for c in rep['chapters'])} 字）",
          f"- 拆解方法：{tv['name']} v{tv['version']}",
          f"- 模型：{ENGINE_LABEL.get(j['engine'], j['engine'])} / {j['model'] or 'CLI 默认模型'}"
          + (f"（实际调用：{'、'.join(rep['actual_models'])}）" if rep["actual_models"] else ""),
          f"- 正文版本：v{v['vno']}（确认于 {fmt_time(v['confirmed'])}）",
          f"- 任务状态：{j['status_label']}；生成时间：{fmt_time(time.time())}",
          "- 说明：本报告不包含原作全文；「推断」为模型的解释性判断；「证据充分 / 有限 / 不足」是分析标签，不是统计置信度。", ""]
    if rep["summary"]:
        d = rep["summary"]["data"]
        L += [f"## {d.get('title', '范围汇总')}", "", d.get("one_line", ""), ""]
        if d.get("missing"):
            L += ["> 缺口：以下章节没有有效结果，汇总未覆盖：" + "、".join(f"第{m['seq']}章《{m['title']}》" for m in d["missing"]), ""]
        _items_md(rep["summary"]["id"], [{"key": "overview", "label": "范围概述"}] + tv["sections"], d, rep, L)
    else:
        L += ["## 范围汇总", "", "（尚未生成：章节未全部完成，或尚未生成带缺口的汇总）", ""]
    for s in rep["segments"]:
        L += [f"## {s['label']}", "", s["data"].get("one_line", ""), ""]
        _items_md(s["id"], [{"key": "overview", "label": "范围概述"}] + tv["sections"], s["data"], rep, L)
    L += ["## 逐章分析", ""]
    for c in rep["chapters"]:
        L += [f"### 第 {c['seq']} 章　{c['title']}", ""]
        if not c["result"]:
            L += [f"（{ {'failed': '分析失败', 'pending': '未处理', 'running': '分析中', 'uncertain': '结果待核实'}.get(c['status'], '未完成') }）", ""]
            continue
        d = c["result"]["data"]
        L += [d.get("one_line", ""), ""]
        _items_md(c["result"]["id"], tv["sections"], d, rep, L)
    return "\n".join(L)


def job_json(job_id: int) -> dict:
    rep = job_report(job_id)
    j = rep["job"]
    return {
        "format": "novel-analyzer/report@1",
        "exported_at": time.time(),
        "book": {k: rep["book"][k] for k in ("id", "title", "author", "grp", "file_hash")},
        "text_version": {k: rep["version"][k] for k in ("id", "vno", "confirmed")},
        "job": {k: j[k] for k in ("id", "title", "status", "engine", "model", "scope", "created", "finished")},
        "template": rep["template"],
        "summary": rep["summary"], "segments": rep["segments"],
        "chapters": [{"chapter_id": c["id"], "seq": c["seq"], "title": c["title"], "status": c["status"],
                      "result": c["result"]} for c in rep["chapters"]],
        "reviews": list(rep["reviews"].values()),
        "paragraph_id_format": "章节序号-段落序号，例如 2-18 表示第 2 章第 18 段（对应该正文版本）",
    }


# ============================================================ 多书比较

def comparison_view(cmp_id: int) -> dict:
    c = db.q1("SELECT * FROM comparisons WHERE id=?", (cmp_id,))
    if not c:
        raise ValueError("比较不存在")
    items = db.jload(c["items"], {})
    flags = db.jload(c["flags"], {})
    job = db.q1("SELECT * FROM jobs WHERE id=?", (c["job_id"],)) if c["job_id"] else None
    res = db.q1("SELECT * FROM results WHERE job_id=? AND kind='comparison'", (c["job_id"],)) if c["job_id"] else None
    snap = items.get("snapshot") or []
    for b in snap:
        b["deleted"] = not db.q1("SELECT id FROM books WHERE id=?", (b["book_id"],))
    return {"comparison": {**c, "items": items, "flags": flags}, "snapshot": snap,
            "job": {**job, "status_label": STATUS_LABEL.get(job["status"], job["status"])} if job else None,
            "result": db.jload(res["data"], None) if res else None}


def comparison_markdown(cmp_id: int) -> str:
    v = comparison_view(cmp_id)
    c, snap, r, job = v["comparison"], v["snapshot"], v["result"], v["job"]
    L = [f"# {c['title']}", ""]
    L.append(f"- 比较范围：{'各书前三章' if c['scope'] == 'first3' else '各书报告覆盖范围'}")
    if job:
        L.append(f"- 模型：{ENGINE_LABEL.get(job['engine'], job['engine'])} / {job['model'] or 'CLI 默认模型'}；生成时间：{fmt_time(job['finished'])}")
    for d in c["flags"].get("differences") or []:
        L.append(f"- 差异标记：{d}")
    L.append("")
    for b in snap:
        L.append(f"- {b['key']}：《{b['title']}》{'（来源已删除，以下为快照）' if b['deleted'] else ''}　报告「{b['job_title']}」"
                 f"　{b['template']}　{ENGINE_LABEL.get(b['engine'], b['engine'])}"
                 + (f"　缺少第 {'、'.join(map(str, b['missing']))} 章" if b["missing"] else ""))
    L.append("")
    if not r:
        return "\n".join(L + ["（比较尚未生成）"])
    keys = [b["key"] for b in snap]
    L.append("| 维度 | " + " | ".join(f"{b['key']}《{b['title']}》" for b in snap) + " |")
    L.append("|---" * (len(snap) + 1) + "|")
    for row in r["rows"]:
        cells = []
        for k in keys:
            cell = row["cells"].get(k) or {}
            src = "，".join(cell.get("sources") or [])
            cells.append((cell.get("claim", "") + (f"〔{src}〕" if src else "")).replace("|", "／").replace("\n", " "))
        L.append(f"| {row['dimension']} | " + " | ".join(cells) + " |")
    L.append("")
    for k, label in (("common", "共性"), ("differences", "差异"), ("hypotheses", "待验证假设"), ("principles", "可用于原创的原则")):
        L += [f"## {label}", ""]
        for p in r.get(k) or []:
            L.append(f"- {p['text']}" + (f"〔{'，'.join(p['sources'])}〕" if p["sources"] else "（无有效来源）"))
        L.append("")
    L.append("来源格式：书代号:章节-段落，例如 B1:2-18 表示 B1 第 2 章第 18 段。")
    return "\n".join(L)


# ============================================================ 备份与恢复

def disk_usage() -> dict:
    total = 0
    for p in db.DATA_DIR.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return {"dir": str(db.DATA_DIR), "bytes": total,
            "books": db.q1("SELECT COUNT(*) n FROM books")["n"],
            "jobs": db.q1("SELECT COUNT(*) n FROM jobs")["n"],
            "comparisons": db.q1("SELECT COUNT(*) n FROM comparisons")["n"]}


def backup_bytes() -> bytes:
    """完整备份：数据库 + 导入的小说副本。不包含任何登录凭证（本工具本来就不保存）。"""
    buf = io.BytesIO()
    tmp = db.DATA_DIR / "backup_snapshot.db"
    if tmp.exists():
        tmp.unlink()
    db.conn().execute(f"VACUUM INTO '{tmp.as_posix()}'")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "app.db")
        for p in db.ORIGINALS.glob("*.txt"):
            z.write(p, f"originals/{p.name}")
        z.writestr("README.txt", "拆书工作台完整备份：包含导入的小说副本与研究数据，不包含任何模型登录凭证。\n"
                                 f"备份时间：{fmt_time(time.time())}\n")
    tmp.unlink()
    return buf.getvalue()


def restore(raw: bytes):
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    if "app.db" not in names:
        raise ValueError("不是有效的备份文件（缺少 app.db）")
    for n in names:
        if n.startswith("/") or ".." in n:
            raise ValueError("备份文件路径异常")
    stage = db.DATA_DIR.parent / (db.DATA_DIR.name + ".restore")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    z.extractall(stage)
    db.close_thread_conn()
    old = db.DATA_DIR.parent / (db.DATA_DIR.name + f".before-restore-{int(time.time())}")
    db.DATA_DIR.rename(old)
    stage.rename(db.DATA_DIR)
    (db.DATA_DIR / "originals").mkdir(exist_ok=True)
    db.bump_generation()
    db.init()
    return {"ok": True, "previous": str(old)}
