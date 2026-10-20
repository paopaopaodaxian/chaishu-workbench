"""拆书任务：任务创建、工作单元规划、提示词、输出校验、汇总与多书比较的生成规则。"""
from __future__ import annotations

import re

from . import db, templates, textproc
from .engines import EngineError

PART_MAX = 14000            # 超过这个字数的章节拆成子单元
SEGMENT_SIZE = 10           # 指定范围超过 12 章时，每 10 章先做一次分段汇总
SEGMENT_THRESHOLD = 12
DETAIL = {"brief": "每个维度写 1–2 条结论。", "standard": "每个维度写 2–3 条结论。", "detailed": "每个维度写 3–5 条结论。"}
SUFF = ["充分", "有限", "不足"]

# ============================================================ 结构化输出约束

ITEM = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "inferred": {"type": "boolean"},
        "sufficiency": {"type": "string", "enum": SUFF},
        "reason": {"type": "string"},
    },
    "required": ["claim", "evidence", "inferred", "sufficiency", "reason"],
    "additionalProperties": False,
}


def sections_schema(keys):
    return {"type": "object", "properties": {k: {"type": "array", "items": ITEM} for k in keys},
            "required": list(keys), "additionalProperties": False}


def chapter_schema(keys):
    return {"type": "object", "properties": {"one_line": {"type": "string"}, "sections": sections_schema(keys)},
            "required": ["one_line", "sections"], "additionalProperties": False}


POINT = {"type": "object", "properties": {"text": {"type": "string"},
                                          "sources": {"type": "array", "items": {"type": "string"}}},
         "required": ["text", "sources"], "additionalProperties": False}

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "dimension": {"type": "string"},
                "cells": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"book": {"type": "string"}, "claim": {"type": "string"},
                                   "sources": {"type": "array", "items": {"type": "string"}},
                                   "sufficiency": {"type": "string", "enum": SUFF}},
                    "required": ["book", "claim", "sources", "sufficiency"], "additionalProperties": False}},
            },
            "required": ["dimension", "cells"], "additionalProperties": False}},
        "common": {"type": "array", "items": POINT},
        "differences": {"type": "array", "items": POINT},
        "hypotheses": {"type": "array", "items": POINT},
        "principles": {"type": "array", "items": POINT},
    },
    "required": ["rows", "common", "differences", "hypotheses", "principles"],
    "additionalProperties": False,
}

SUMMARY_EXTRA = {"key": "overview", "label": "范围概述", "question": "在本范围内故事整体推进了什么？结构上分几个段落？"}

# ============================================================ 范围

def scope_chapters(version_id: int, scope: dict) -> list[dict]:
    chs = db.q("SELECT id,seq,title,kind,n_chars FROM chapters WHERE version_id=? AND excluded=0 ORDER BY seq",
               (version_id,))
    t = scope.get("type", "first3")
    if t == "first3":
        body = [c for c in chs if c["kind"] in ("chapter", "segment")]
        return body[:3]
    if t == "chapters":
        ids = set(int(i) for i in scope.get("chapter_ids") or [])
        return [c for c in chs if c["id"] in ids]
    out = [c for c in chs if c["kind"] != "front" or scope.get("include_front")]
    if scope.get("exclude_extra"):
        out = [c for c in out if c["kind"] != "extra"]
    return out


def scope_title(scope: dict, chs: list[dict], gap: bool) -> str:
    t = scope.get("type")
    if t == "first3":
        base = "前三章分析"
    elif t == "all":
        base = "全书结构分析" + ("（不含番外）" if scope.get("exclude_extra") else "")
    else:
        seqs = [c["seq"] for c in chs]
        if seqs and seqs == list(range(seqs[0], seqs[0] + len(seqs))):
            base = f"第 {seqs[0]}–{seqs[-1]} 章分析" if len(seqs) > 1 else f"第 {seqs[0]} 章分析"
        else:
            base = f"指定章节分析（{len(seqs)} 章）"
    if gap:
        base = ("范围汇总" if t == "all" else base) + "（存在缺口）"
    return base


def plan_units(version_id: int, chs: list[dict]) -> list[dict]:
    units = []
    for c in chs:
        n = c["n_chars"]
        parts = max(1, -(-n // PART_MAX)) if n > PART_MAX else 1
        for i in range(parts):
            label = f"第 {c['seq']} 章《{c['title']}》" + (f" 第 {i + 1}/{parts} 部分" if parts > 1 else "")
            units.append({"kind": "chapter", "chapter_id": c["id"], "part_idx": i, "part_total": parts, "label": label,
                          "extra": {}})
    if len(chs) > SEGMENT_THRESHOLD:
        for i in range(0, len(chs), SEGMENT_SIZE):
            grp = chs[i:i + SEGMENT_SIZE]
            units.append({"kind": "segment", "chapter_id": None, "part_idx": 0, "part_total": 1,
                          "label": f"分段汇总 第 {grp[0]['seq']}–{grp[-1]['seq']} 章",
                          "extra": {"chapter_ids": [c["id"] for c in grp]}})
    units.append({"kind": "summary", "chapter_id": None, "part_idx": 0, "part_total": 1, "label": "范围汇总",
                  "extra": {"chapter_ids": [c["id"] for c in chs]}})
    return units


def preview(book_id: int, scope: dict) -> dict:
    book = db.q1("SELECT * FROM books WHERE id=?", (book_id,))
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (book["current_version"],))
    chs = scope_chapters(v["id"], scope)
    units = plan_units(v["id"], chs) if chs else []
    nch = sum(1 for u in units if u["kind"] == "chapter")
    return {
        "version": {"id": v["id"], "vno": v["vno"], "status": v["status"]},
        "chapters": [{"id": c["id"], "seq": c["seq"], "title": c["title"], "n_chars": c["n_chars"], "kind": c["kind"]} for c in chs],
        "n_chapters": len(chs), "n_chars": sum(c["n_chars"] for c in chs),
        "n_calls_chapter": nch, "n_calls_segment": sum(1 for u in units if u["kind"] == "segment"),
        "n_calls_total": len(units), "title": scope_title(scope, chs, False),
        "split_chapters": sum(1 for c in chs if c["n_chars"] > PART_MAX),
    }


# ============================================================ 创建任务

def create_job(book_id: int, template_version_id: int, engine: str, model: str, scope: dict, options: dict,
               draft: bool = False, parent_job: int | None = None) -> int:
    book = db.q1("SELECT * FROM books WHERE id=?", (book_id,))
    if not book:
        raise ValueError("书籍不存在")
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (book["current_version"],))
    if v["status"] != "confirmed":
        raise ValueError("目录尚未确认，确认后才能分析")
    tv = templates.get_version(template_version_id)
    if not tv:
        raise ValueError("拆解方法不存在")
    chs = scope_chapters(v["id"], scope)
    if not chs:
        raise ValueError("所选范围没有可分析的章节")
    opts = {"detail": options.get("detail", "standard"), "context": int(options.get("context", 3)),
            "retries": max(0, min(2, int(options.get("retries", 2)))), "doubt": options.get("doubt")}
    title = scope_title(scope, chs, False)
    status = "draft" if draft else "queued"
    with db.tx():
        jid = db.x("INSERT INTO jobs(kind,book_id,version_id,template_version_id,engine,model,scope,options,title,status,"
                   "parent_job,created,queued_at) VALUES('analysis',?,?,?,?,?,?,?,?,?,?,?,?)",
                   (book_id, v["id"], template_version_id, engine, model or "", db.jdump(scope), db.jdump(opts), title,
                    status, parent_job, db.now(), None if draft else db.now()))
        for i, u in enumerate(plan_units(v["id"], chs), 1):
            db.x("INSERT INTO units(job_id,seq,kind,chapter_id,part_idx,part_total,label,extra) VALUES(?,?,?,?,?,?,?,?)",
                 (jid, i, u["kind"], u["chapter_id"], u["part_idx"], u["part_total"], u["label"], db.jdump(u["extra"])))
    db.log_event(jid, "任务已创建" + ("（草稿）" if draft else "，等待执行"))
    return jid


# ============================================================ 提示词

def _sections_text(secs):
    return "\n".join(f"- {s['key']}（{s['label']}）：{s['question']}" for s in secs)


def prev_context(job: dict, chapter: dict, n: int) -> str:
    if n <= 0:
        return "（未使用前文摘要）"
    rows = db.q("SELECT r.data, c.seq, c.title FROM results r JOIN chapters c ON c.id=r.chapter_id"
                " WHERE r.job_id=? AND r.kind='chapter' AND c.seq<? ORDER BY c.seq DESC LIMIT ?",
                (job["id"], chapter["seq"], n))
    if not rows:
        return "（本章之前没有已完成的章节分析）"
    out = []
    for r in reversed(rows):
        d = db.jload(r["data"], {})
        line = f"第 {r['seq']} 章《{r['title']}》：{d.get('one_line', '')}"
        sc = d.get("sections") or {}
        for key in ("state_change", "chapter_end", "promise"):
            for it in (sc.get(key) or [])[:1]:
                line += f"\n  - {it.get('claim', '')}"
        out.append(line)
    return "\n".join(out)


def chapter_prompt(job: dict, tv: dict, book: dict, chapter: dict, paras: list[dict], part_idx: int,
                   part_total: int) -> str:
    opts = db.jload(job["options"], {})
    doubt = opts.get("doubt") or {}
    doubt_txt = ""
    if doubt:
        doubt_txt = (f"\n【用户核对时提出的疑问——请针对性复核】\n原结论：{doubt.get('claim', '')}\n"
                     f"疑问：{doubt.get('note', '')}\n请重新阅读正文，若原结论不成立请直接修正，并在 reason 中说明。")
    part_note = f"（本章较长，这是第 {part_idx + 1}/{part_total} 部分，只分析这一部分）" if part_total > 1 else ""
    body = "\n".join(f"[{chapter['seq']}-{p['pseq']}] {p['text']}" for p in paras)
    return f"""你是严谨的小说结构分析师，正在为一位学习写作的作者做拆书研究。
书名：《{book['title']}》{('　作者：' + book['author']) if book['author'] else ''}
拆解方法：{tv['name']}（v{tv['version']}）——{tv.get('scene', '')}
当前分析：第 {chapter['seq']} 章《{chapter['title']}》{part_note}

【证据规则】
1. 只依据下方【正文】作结论。不得凭书名、目录、简介或你对这本书的既有印象，补写本章正文中没有的情节。
2. 正文每段开头方括号里是段落编号，例如 [{chapter['seq']}-18]。每条结论的 evidence 填写直接支撑它的段落编号（不带方括号，如 "{chapter['seq']}-18"），一般 1–3 个，只能引用本章段落。
3. 需要借用【前文结构摘要】里的内容时，在 claim 里写明「据第 X 章分析」，并把 inferred 设为 true；evidence 只放本章相关段落，没有就留空。
4. inferred：结论含推测或解释性判断（如情绪效果、下一章可能承担的功能）时为 true；直接复述文本事实时为 false。
5. sufficiency 取「充分 / 有限 / 不足」之一，reason 用一句话说明理由。不要给百分比、分数或「准确率」。
6. 情绪与期待是文本分析判断，不是读者实测反应。不要推断读者年龄、完读率或收入。
7. {DETAIL.get(opts.get('detail'), DETAIL['standard'])}本章确实没有相关内容的维度，写一条 sufficiency 为「不足」的说明即可。
补充要求：{tv.get('evidence_req', '')}
{tv.get('extra_prompt', '')}{doubt_txt}

【本章需回答】（sections 的键名必须与下面的英文键一致）
{_sections_text(tv['sections'])}

one_line：用一句话（不超过 60 字）概括本章{('这一部分' if part_total > 1 else '')}发生了什么。

【前文结构摘要】
{prev_context(job, chapter, int(opts.get('context', 3)))}

【正文】
{body}
"""


def _material(results: list[dict], max_items=4) -> str:
    out = []
    for r in results:
        d = db.jload(r["data"], {})
        out.append(f"## 第 {r['seq']} 章《{r['title']}》\n一句话：{d.get('one_line', '')}")
        for key, items in (d.get("sections") or {}).items():
            for it in items[:max_items]:
                ev = ",".join(it.get("evidence") or [])
                out.append(f"- [{key}] {it.get('claim', '')}（依据 {ev or '无'}；{it.get('sufficiency', '')}"
                           f"{'；推断' if it.get('inferred') else ''}）")
    return "\n".join(out)


def summary_prompt(job: dict, tv: dict, book: dict, title: str, results: list[dict], missing: list[dict],
                   kind: str, segments: list[dict] | None = None) -> str:
    secs = [SUMMARY_EXTRA] + tv["sections"]
    scope = db.jload(job["scope"], {})
    limit = ""
    if scope.get("type") == "first3":
        limit = "这是「前三章分析」，不是全书结构。不得推断前三章之后的情节，可以指出开篇为后文埋下的问题。"
    elif scope.get("type") == "chapters":
        limit = "只对所列章节作结论，不得推断范围以外的情节。"
    gap = ""
    if missing:
        gap = ("以下章节没有有效分析结果，不得对其内容作任何结论，并在 overview 中明确写出缺口：\n"
               + "、".join(f"第 {m['seq']} 章《{m['title']}》" for m in missing))
    material = _material(results) if not segments else "\n\n".join(
        f"## {s['label']}\n" + _seg_text(s) for s in segments)
    what = "分段汇总" if kind == "segment" else f"「{title}」的范围汇总"
    return f"""你是严谨的小说结构分析师。下面是《{book['title']}》各章的结构化分析结果（不是正文）。请据此写出{what}。

【规则】
1. 只使用下面提供的章节分析。每条结论的 evidence 写它所依据的段落编号（必须是材料里出现过的编号，如 "2-18"），跨章归纳时列出多章的编号。
2. 归纳、比较、推测性内容 inferred 设为 true。sufficiency 取「充分 / 有限 / 不足」，reason 说明理由；不要给百分比。
3. {limit or '只依据材料作结论。'}
4. {DETAIL.get(db.jload(job['options'], {}).get('detail'), DETAIL['standard'])}
{gap}

【需回答】（sections 的键名必须与下面的英文键一致）
{_sections_text(secs)}

one_line：用一句话概括这个范围。

【章节分析材料】
{material}
"""


def _seg_text(seg_result: dict) -> str:
    d = db.jload(seg_result["data"], {})
    out = [f"一句话：{d.get('one_line', '')}"]
    for key, items in (d.get("sections") or {}).items():
        for it in items[:4]:
            out.append(f"- [{key}] {it.get('claim', '')}（依据 {','.join(it.get('evidence') or []) or '无'}）")
    return "\n".join(out)


# ============================================================ 校验与归一

ID_RE = re.compile(r"(\d+)\s*[-－—–_]\s*(\d+)")


def norm_id(s) -> str | None:
    m = ID_RE.search(str(s))
    return f"{int(m.group(1))}-{int(m.group(2))}" if m else None


def para_index(version_id: int, chapter_ids: list[int] | None = None) -> dict:
    sql = ("SELECT c.seq cs, p.pseq ps, c.id cid FROM paragraphs p JOIN chapters c ON c.id=p.chapter_id"
           " WHERE p.version_id=? AND p.excluded=0")
    args = [version_id]
    if chapter_ids is not None:
        if not chapter_ids:
            return {}
        sql += f" AND c.id IN ({','.join('?' * len(chapter_ids))})"
        args += chapter_ids
    return {f"{r['cs']}-{r['ps']}": r["cid"] for r in db.q(sql, args)}


def normalize_sections(data: dict, keys: list[str], valid: dict, own_chapter: int | None, require_ratio=True) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        raise EngineError("format", "输出缺少 sections 字段")
    secs = data["sections"]
    out, total, unsupported = {}, 0, 0
    for k in keys:
        items = []
        for it in secs.get(k) or []:
            if not isinstance(it, dict) or not str(it.get("claim", "")).strip():
                continue
            ev, bad, cross = [], [], False
            for e in it.get("evidence") or []:
                i = norm_id(e)
                if i and i in valid:
                    if i not in ev:
                        ev.append(i)
                    if own_chapter is not None and valid[i] != own_chapter:
                        cross = True
                else:
                    bad.append(str(e)[:20])
            suff = it.get("sufficiency") if it.get("sufficiency") in SUFF else "有限"
            flags = []
            if not ev:
                flags.append("无有效依据")
                suff = "不足"
                unsupported += 1
            if bad:
                flags.append("含无效段落编号")
            if cross:
                flags.append("引用了其他章节")
            items.append({"claim": str(it["claim"]).strip(), "evidence": ev, "invalid_evidence": bad,
                          "inferred": bool(it.get("inferred")), "sufficiency": suff,
                          "reason": str(it.get("reason", "")).strip(), "flags": flags})
            total += 1
        out[k] = items
    if total == 0:
        raise EngineError("format", "输出中没有任何有效结论")
    missing = [k for k in keys if k not in secs]
    if len(missing) > len(keys) / 2:
        raise EngineError("format", "输出缺少大部分分析维度：" + "、".join(missing))
    if require_ratio and unsupported / total > 0.6:
        raise EngineError("format", f"{unsupported}/{total} 条结论没有有效的段落依据")
    return {"one_line": str(data.get("one_line", "")).strip(), "sections": out,
            "stats": {"items": total, "unsupported": unsupported, "missing_sections": missing}}


def merge_parts(parts: list[dict], keys: list[str]) -> dict:
    out = {"one_line": "；".join(p.get("one_line", "") for p in parts if p.get("one_line")),
           "sections": {k: [] for k in keys}, "stats": {"items": 0, "unsupported": 0, "missing_sections": []},
           "parts": len(parts)}
    for p in parts:
        for k in keys:
            out["sections"][k] += (p.get("sections") or {}).get(k, [])
        for s in ("items", "unsupported"):
            out["stats"][s] += (p.get("stats") or {}).get(s, 0)
    return out


# ============================================================ 多书比较

def compare_check(items: list[dict], scope: str) -> dict:
    """items: [{book_id, job_id}]；返回每本书的可比性与差异标记。"""
    rows, tvs, scopes = [], set(), set()
    for it in items:
        book = db.q1("SELECT * FROM books WHERE id=?", (it.get("book_id"),))
        row = {"book_id": it.get("book_id"), "job_id": it.get("job_id"), "title": book["title"] if book else "（来源已删除）",
               "issues": [], "ok": False}
        if not book:
            row["issues"].append("书籍已删除")
            rows.append(row)
            continue
        job = db.q1("SELECT * FROM jobs WHERE id=? AND kind='analysis' AND book_id=?", (it.get("job_id"), book["id"])) \
            if it.get("job_id") else None
        if not job:
            row["issues"].append("缺少分析报告")
            rows.append(row)
            continue
        tv = templates.get_version(job["template_version_id"])
        js = db.jload(job["scope"], {})
        row.update({"template": f"{tv['name']} v{tv['version']}", "engine": job["engine"], "model": job["model"],
                    "job_title": job["title"], "job_status": job["status"], "version_id": job["version_id"]})
        tvs.add(job["template_version_id"])
        scopes.add(js.get("type"))
        chs = material_chapters(job, scope)
        done = {r["chapter_id"] for r in db.q("SELECT chapter_id FROM results WHERE job_id=? AND kind='chapter'", (job["id"],))}
        row["chapters"] = [{"seq": c["seq"], "title": c["title"], "n_chars": c["n_chars"], "done": c["id"] in done} for c in chs]
        row["n_chars"] = sum(c["n_chars"] for c in chs)
        missing = [c for c in chs if c["id"] not in done]
        if scope == "all":
            if js.get("type") != "all" or job["status"] != "done":
                row["issues"].append("全书比较需要该书有完整覆盖的全书报告")
        if missing:
            row["issues"].append(f"范围内有 {len(missing)} 章没有分析结果")
        if book["current_version"] != job["version_id"]:
            row["issues"].append("该报告基于旧的正文版本")
        row["ok"] = not any(i.startswith(("缺少", "全书比较", "书籍")) for i in row["issues"]) and len(missing) < len(chs)
        rows.append(row)
    diffs = []
    if len(tvs) > 1:
        diffs.append("各书使用的拆解方法或模板版本不同")
    if len(scopes) > 1:
        diffs.append("各书报告的分析范围类型不同")
    chars = [r.get("n_chars") for r in rows if r.get("n_chars")]
    if chars and max(chars) > 1.5 * min(chars):
        diffs.append(f"比较范围的字数差异较大（{min(chars)}–{max(chars)} 字）")
    return {"rows": rows, "differences": diffs, "ready": sum(r["ok"] for r in rows) >= 2 and all(r["ok"] for r in rows)}


def material_chapters(job: dict, scope: str) -> list[dict]:
    if scope == "first3":
        return scope_chapters(job["version_id"], {"type": "first3"})
    js = db.jload(job["scope"], {})
    return scope_chapters(job["version_id"], js)


def compare_snapshot(items: list[dict], scope: str) -> list[dict]:
    snap = []
    for i, it in enumerate(items, 1):
        book = db.q1("SELECT * FROM books WHERE id=?", (it["book_id"],))
        job = db.q1("SELECT * FROM jobs WHERE id=?", (it["job_id"],))
        chs = material_chapters(job, scope)
        ids = [c["id"] for c in chs]
        res = db.q(f"SELECT r.id rid, r.data, c.seq, c.title, c.n_chars FROM results r JOIN chapters c ON c.id=r.chapter_id"
                   f" WHERE r.job_id=? AND r.kind='chapter' AND r.chapter_id IN ({','.join('?' * len(ids))}) ORDER BY c.seq",
                   (job["id"], *ids)) if ids else []
        tv = templates.get_version(job["template_version_id"])
        snap.append({"key": f"B{i}", "book_id": book["id"], "title": book["title"], "author": book["author"],
                     "grp": book["grp"], "job_id": job["id"], "job_title": job["title"], "version_id": job["version_id"],
                     "template": f"{tv['name']} v{tv['version']}", "engine": job["engine"], "model": job["model"],
                     "chapters": [{"seq": c["seq"], "title": c["title"], "n_chars": c["n_chars"]} for c in chs],
                     "missing": [c["seq"] for c in chs if c["seq"] not in {r["seq"] for r in res}],
                     "results": [{"result_id": r["rid"], "seq": r["seq"], "title": r["title"], "data": db.jload(r["data"], {})}
                                 for r in res]})
    return snap


def compare_prompt(snap: list[dict], scope: str, dims: list[str]) -> str:
    blocks = []
    for b in snap:
        lines = [f"# {b['key']}：《{b['title']}》（{b['grp'] or '未分组'}；拆解方法 {b['template']}；"
                 f"范围字数 {sum(c['n_chars'] for c in b['chapters'])}）"]
        if b["missing"]:
            lines.append("缺少分析的章节：" + "、".join(f"第 {s} 章" for s in b["missing"]))
        for r in b["results"]:
            d = r["data"]
            lines.append(f"## 第 {r['seq']} 章《{r['title']}》：{d.get('one_line', '')}")
            for key, items in (d.get("sections") or {}).items():
                for it in items[:4]:
                    lines.append(f"- [{key}] {it.get('claim', '')}（依据 {','.join(it.get('evidence') or []) or '无'}）")
        blocks.append("\n".join(lines))
    keys = "、".join(b["key"] for b in snap)
    scope_txt = "各书前三章" if scope == "first3" else "各书报告覆盖的范围"
    return f"""你是严谨的小说结构研究者。下面是 {len(snap)} 本书（{keys}）在「{scope_txt}」的章节分析结果（不是正文）。请做逐项对照。

【规则】
1. rows 按下列维度逐行输出，每行 cells 必须覆盖每一本书（book 填 {keys}），顺序一致：{'、'.join(dims)}
2. 每个单元格只依据该书的材料作结论；sources 写来源，格式为「书代号:段落编号」，如 "B1:2-18"，编号必须在该书材料中出现过。
3. 材料不足以回答时，claim 写「证据不足」，sufficiency 写「不足」，sources 留空。不要用常识或对作品的印象补全。
4. common（共性）、differences（差异）、hypotheses（待验证假设）、principles（可用于原创的写作原则）每条都要给出 sources。
5. 不输出「照此写一定成功」之类结论，不推断读者年龄、真实完读率或收入；不引用材料以外的市场数据。

{chr(10).join(blocks)}
"""


def normalize_compare(data: dict, snap: list[dict], dims: list[str]) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise EngineError("format", "比较结果缺少 rows")
    valid = {}
    for b in snap:
        ids = set()
        for r in b["results"]:
            for items in (r["data"].get("sections") or {}).values():
                for it in items:
                    ids.update(it.get("evidence") or [])
        valid[b["key"]] = ids

    def check_sources(srcs):
        good, bad = [], []
        for s in srcs or []:
            m = re.match(r"\s*(B\d+)\s*[:：]\s*(.+)", str(s))
            i = norm_id(m.group(2)) if m else None
            if m and i and i in valid.get(m.group(1), set()):
                good.append(f"{m.group(1)}:{i}")
            else:
                bad.append(str(s)[:30])
        return good, bad

    rows = []
    for row in data["rows"]:
        cells = {}
        for c in row.get("cells") or []:
            key = str(c.get("book", "")).strip()
            if key not in valid:
                continue
            good, bad = check_sources(c.get("sources"))
            claim = str(c.get("claim", "")).strip() or "证据不足"
            suff = c.get("sufficiency") if c.get("sufficiency") in SUFF else "有限"
            if not good and "证据不足" not in claim:
                suff = "不足"
            cells[key] = {"claim": claim, "sources": good, "invalid": bad, "sufficiency": suff}
        for b in snap:
            cells.setdefault(b["key"], {"claim": "证据不足", "sources": [], "invalid": [], "sufficiency": "不足"})
        rows.append({"dimension": str(row.get("dimension", "")), "cells": cells})
    out = {"rows": rows}
    for k in ("common", "differences", "hypotheses", "principles"):
        pts = []
        for p in data.get(k) or []:
            good, bad = check_sources(p.get("sources"))
            pts.append({"text": str(p.get("text", "")), "sources": good, "invalid": bad,
                        "flags": [] if good else ["无有效来源"]})
        out[k] = pts
    return out
