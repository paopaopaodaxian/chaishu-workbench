#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拆书工作台
本地小说结构分析：导入 → 确认目录 → 选择范围 → 调用本机 Claude Code / Codex → 有正文依据的报告 → 人工核对 → 多书比较 → 导出。

运行：python3 server.py   然后打开 http://127.0.0.1:8765
零第三方依赖，Python 3.9+。数据保存在 ./data（可用环境变量 NOVEL_DATA 指定）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import analysis, db, engines, reports, templates, textproc, worker  # noqa: E402
from core.worker import STATUS_LABEL  # noqa: E402

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 200 * 1024 * 1024
DEFAULT_GROUPS = ["职场恋爱", "婚恋磨合", "婚姻重建"]
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class HTTPError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# ============================================================ 状态与连接

_engine_cache = {"t": 0, "data": None}


@route("GET", "/api/ping")
def ping(req):
    import platform
    ver = (Path(__file__).resolve().parent / "VERSION")
    return {"app": "chaishu", "pid": os.getpid(), "arch": platform.machine(), "python": sys.version.split()[0],
            "root": str(Path(__file__).resolve().parent),
            "version": os.environ.get("CHAISHU_VERSION") or (ver.read_text().strip() if ver.exists() else "")}


@route("POST", "/api/shutdown")
def shutdown(req):
    """关闭工作台：正在执行的任务会在下次启动时标为「已暂停：应用中断」，可以继续。"""
    def bye():
        time.sleep(0.6)
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return {"ok": True}


@route("GET", "/api/overview")
def overview(req):
    running = db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('running','pausing','queued')")["n"]
    waiting = db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('waiting_login','waiting_quota','paused')")["n"]
    if time.time() - _engine_cache["t"] > 60 or _engine_cache["data"] is None:
        _engine_cache["data"] = {k: _light_status(e) for k, e in engines.ENGINES.items()}
        _engine_cache["t"] = time.time()
    return {"engines": _engine_cache["data"], "running": running, "waiting": waiting, "blocked": worker.BLOCKED,
            "data_dir": str(db.DATA_DIR)}


def _light_status(e):
    if not e.bin():
        return {"installed": False, "logged_in": False}
    try:
        st = e.status()
        return {"installed": True, "logged_in": st.get("logged_in"), "kind": st["account"].get("kind")}
    except Exception:  # noqa: BLE001
        return {"installed": True, "logged_in": None}


@route("GET", "/api/engines")
def engines_info(req):
    out = {k: e.info(deep=True) for k, e in engines.ENGINES.items()}
    _engine_cache["data"] = {k: {"installed": v["installed"], "logged_in": v.get("logged_in"),
                                 "kind": (v.get("account") or {}).get("kind")} for k, v in out.items()}
    _engine_cache["t"] = time.time()
    return out


@route("POST", "/api/engines/(claude|codex)/(login|logout|verify|login_cancel|allow_env)")
def engine_action(req, key, act):
    e = engines.get(key)
    body = req.json()
    _engine_cache["t"] = 0
    if act == "login":
        return e.start_login()
    if act == "login_cancel":
        e.cancel_login()
        return {"ok": True}
    if act == "logout":
        return e.logout()
    if act == "allow_env":
        db.set_setting(f"{key}.allow_env_credentials", bool(body.get("allow")))
        return {"ok": True}
    res = e.verify((body.get("model") or "").strip())
    if res.get("ok"):
        worker.BLOCKED.pop(key, None)
    return res


@route("GET", "/api/engines/(claude|codex)/login_state")
def engine_login_state(req, key):
    return engines.get(key).login_state()


# ============================================================ 书库与导入

def book_row(b):
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (b["current_version"],)) or {}
    total = db.q1("SELECT COUNT(*) n FROM chapters WHERE version_id=? AND excluded=0 AND kind!='front'",
                  (b["current_version"],))["n"]
    covered = db.q1("SELECT COUNT(DISTINCT r.chapter_id) n FROM results r JOIN jobs j ON j.id=r.job_id "
                    "WHERE r.kind='chapter' AND j.version_id=?", (b["current_version"],))["n"]
    reports_n = db.q1("SELECT COUNT(*) n FROM jobs WHERE book_id=? AND kind='analysis' AND status IN ('done','partial')",
                      (b["id"],))["n"]
    active = db.q1("SELECT COUNT(*) n FROM jobs WHERE book_id=? AND status IN ('queued','running','pausing')", (b["id"],))["n"]
    draft_v = db.q1("SELECT id FROM text_versions WHERE book_id=? AND status='draft'", (b["id"],))
    if v.get("status") != "confirmed":
        state, action = "待确认目录", "check"
    elif reports_n:
        state, action = f"{covered}/{total} 章已分析", "report"
    elif active:
        state, action = "分析中", "tasks"
    else:
        state, action = "尚未分析", "analyze"
    return {**b, "n_chars": v.get("n_chars", 0), "n_chapters": v.get("n_chapters", 0), "vno": v.get("vno"),
            "version_status": v.get("status"), "covered": covered, "total": total, "state": state, "action": action,
            "reports": reports_n, "active": active, "draft_version": draft_v["id"] if draft_v else None}


@route("GET", "/api/books")
def books(req):
    qs = req.query
    sql, args = "SELECT * FROM books WHERE 1=1", []
    if qs.get("q"):
        sql += " AND (title LIKE ? OR author LIKE ?)"
        args += [f"%{qs['q']}%"] * 2
    if qs.get("grp"):
        sql += " AND grp=?"
        args.append(qs["grp"])
    return [book_row(b) for b in db.q(sql + " ORDER BY updated DESC", args)]


@route("GET", "/api/groups")
def groups(req):
    used = [r["grp"] for r in db.q("SELECT DISTINCT grp FROM books WHERE grp!=''")]
    return list(dict.fromkeys(DEFAULT_GROUPS + used))


@route("POST", "/api/import")
def import_file(req):
    raw = req.raw()
    name = req.query.get("filename") or "未命名.txt"
    if not name.lower().endswith(".txt"):
        raise HTTPError(400, "第一版只支持 TXT 文件")
    res = textproc.import_file(raw, name, force_copy=req.query.get("force") == "1")
    if res["status"] == "ok":
        b = db.q1("SELECT * FROM books WHERE id=?", (res["book_id"],))
        res["book"] = book_row(b)
    return res


@route("GET", r"/api/books/(\d+)")
def book_detail(req, bid):
    b = db.q1("SELECT * FROM books WHERE id=?", (bid,))
    if not b:
        raise HTTPError(404, "书籍不存在")
    row = book_row(b)
    versions = db.q("SELECT id,vno,status,n_chars,n_chapters,created,confirmed,segmented FROM text_versions "
                    "WHERE book_id=? ORDER BY vno DESC", (bid,))
    jobs = db.q("SELECT * FROM jobs WHERE book_id=? AND kind='analysis' ORDER BY created DESC", (bid,))
    for j in jobs:
        tv = templates.get_version(j["template_version_id"])
        j["template"] = f"{tv['name']} v{tv['version']}" if tv else ""
        j["status_label"] = STATUS_LABEL.get(j["status"], j["status"])
        j["scope"] = db.jload(j["scope"], {})
        v = next((x for x in versions if x["id"] == j["version_id"]), None)
        j["vno"] = v["vno"] if v else None
        u = db.q1("SELECT COUNT(*) total, SUM(status='done') done FROM units WHERE job_id=? AND kind='chapter'", (j["id"],))
        j["progress"] = {"done": u["done"] or 0, "total": u["total"]}
        j["unchecked"] = 0
    pending_review = 0
    for j in jobs:
        if j["status"] in ("done", "partial"):
            n_items = 0
            for r in db.q("SELECT id,data FROM results WHERE job_id=? AND kind IN ('chapter','summary')", (j["id"],)):
                d = db.jload(r["data"], {})
                n_items += sum(len(v) for v in (d.get("sections") or {}).values())
            reviewed = db.q1("SELECT COUNT(*) n FROM reviews rv JOIN results r ON r.id=rv.result_id WHERE r.job_id=? "
                             "AND rv.status!='待核对'", (j["id"],))["n"]
            j["unchecked"] = max(0, n_items - reviewed)
            doubt = db.q1("SELECT COUNT(*) n FROM reviews rv JOIN results r ON r.id=rv.result_id WHERE r.job_id=? "
                          "AND rv.status='有疑问'", (j["id"],))["n"]
            j["doubts"] = doubt
            pending_review += j["unchecked"]
    return {**row, "versions": versions, "jobs": jobs, "pending_review": pending_review}


@route("POST", r"/api/books/(\d+)")
def book_update(req, bid):
    b = req.json()
    fields = {k: str(b[k]).strip()[:200 if k != "notes" else 20000] for k in ("title", "author", "grp", "notes") if k in b}
    if "title" in fields and not fields["title"]:
        raise HTTPError(400, "书名不能为空")
    for k, v in fields.items():
        db.x(f"UPDATE books SET {k}=?, updated=? WHERE id=?", (v, db.now(), bid))
    return {"ok": True}


@route("GET", r"/api/books/(\d+)/covered")
def book_covered(req, bid):
    b = db.q1("SELECT current_version FROM books WHERE id=?", (bid,))
    return [r["chapter_id"] for r in db.q("SELECT DISTINCT r.chapter_id FROM results r JOIN jobs j ON j.id=r.job_id "
                                          "WHERE r.kind='chapter' AND j.version_id=?", (b["current_version"],))]


@route("GET", r"/api/books/(\d+)/inspect")
def book_inspect(req, bid):
    b = db.q1("SELECT * FROM books WHERE id=?", (bid,))
    text = textproc.book_text(b)
    chk = textproc.inspect_text(text)
    return {"encoding": b["encoding"], "file_size": b["file_size"], "file_name": b["file_name"], "check": chk,
            "preview": text.strip()[:800]}


@route("GET", r"/api/books/(\d+)/impact")
def book_impact(req, bid):
    jobs = db.q("SELECT id,status FROM jobs WHERE book_id=?", (bid,))
    cmps = [c for c in db.q("SELECT id,title,items FROM comparisons")
            if any(str(x.get("book_id")) == str(bid) for x in (db.jload(c["items"], {}).get("books") or []))]
    return {"jobs": len(jobs), "active": sum(j["status"] in worker.ACTIVE for j in jobs),
            "versions": db.q1("SELECT COUNT(*) n FROM text_versions WHERE book_id=?", (bid,))["n"],
            "reports": sum(j["status"] in ("done", "partial") for j in jobs),
            "comparisons": [{"id": c["id"], "title": c["title"]} for c in cmps]}


@route("POST", r"/api/books/(\d+)/delete")
def book_delete(req, bid):
    imp = book_impact(req, bid)
    if imp["active"]:
        raise HTTPError(400, "这本书有正在执行的任务，请先停止任务再删除")
    b = db.q1("SELECT * FROM books WHERE id=?", (bid,))
    for j in db.q("SELECT id FROM jobs WHERE book_id=?", (bid,)):
        worker.delete_job(j["id"])
    with db.tx():
        for v in db.q("SELECT id FROM text_versions WHERE book_id=?", (bid,)):
            textproc.delete_version(v["id"])
        db.x("DELETE FROM books WHERE id=?", (bid,))
    if not db.q1("SELECT id FROM books WHERE file_hash=?", (b["file_hash"],)):
        p = textproc.original_path(b["file_hash"])
        if p.exists():
            p.unlink()
    return {"ok": True}


# ============================================================ 文本版本与目录

@route("GET", "/api/rules")
def rules(req):
    return [{"key": k, "label": v["label"]} for k, v in textproc.RULES.items()] + [{"key": "custom", "label": "自定义正则"}]


@route("GET", r"/api/versions/(\d+)")
def version_detail(req, vid):
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (vid,))
    if not v:
        raise HTTPError(404, "版本不存在")
    chs = db.q("SELECT id,seq,title,kind,excluded,n_chars FROM chapters WHERE version_id=? ORDER BY seq", (vid,))
    chk = textproc.version_checks(vid)
    for c in chs:
        c["flags"] = chk["flags"].get(c["id"], [])
    b = db.q1("SELECT id,title,current_version FROM books WHERE id=?", (v["book_id"],))
    return {**v, "split_rule": db.jload(v["split_rule"], {}), "chapters": chs, "checks": {k: chk[k] for k in ("median", "n_short", "n_long", "n_dup")},
            "book": b}


@route("GET", r"/api/chapters/(\d+)/text")
def chapter_text(req, cid):
    ch = db.q1("SELECT * FROM chapters WHERE id=?", (cid,))
    if not ch:
        raise HTTPError(404, "章节不存在")
    paras = textproc.chapter_paragraphs(cid, include_excluded=req.query.get("all") == "1")
    return {"chapter": ch, "paragraphs": [{**p, "label": f"{ch['seq']}-{p['pseq']}"} for p in paras]}


@route("GET", r"/api/versions/(\d+)/seq/(\d+)")
def chapter_by_seq(req, vid, seq):
    ch = db.q1("SELECT id FROM chapters WHERE version_id=? AND seq=?", (vid, seq))
    if not ch:
        raise HTTPError(404, "章节不存在")
    return chapter_text(req, ch["id"])


@route("POST", r"/api/versions/(\d+)/op")
def version_op(req, vid):
    b = req.json()
    op = b.get("op")
    if op == "rename":
        textproc.op_rename(vid, int(b["chapter_id"]), b.get("title", ""))
    elif op == "kind":
        textproc.op_set_kind(vid, int(b["chapter_id"]), b.get("kind"))
    elif op == "exclude_chapter":
        textproc.op_exclude_chapter(vid, int(b["chapter_id"]), bool(b.get("excluded")))
    elif op == "exclude_para":
        textproc.op_exclude_para(vid, int(b["paragraph_id"]), bool(b.get("excluded")))
    elif op == "merge_next":
        textproc.op_merge_next(vid, int(b["chapter_id"]))
    elif op == "split_at":
        return {"chapter_id": textproc.op_split_at(vid, int(b["paragraph_id"]))}
    elif op == "resplit":
        return {"version_id": textproc.op_resplit(vid, b.get("rule", "auto"), b.get("custom"))}
    else:
        raise HTTPError(400, "未知操作")
    return {"ok": True}


@route("POST", r"/api/versions/(\d+)/confirm")
def version_confirm(req, vid):
    return textproc.confirm(vid)


@route("POST", r"/api/versions/(\d+)/newdraft")
def version_newdraft(req, vid):
    return {"version_id": textproc.new_draft_from(vid)}


@route("POST", r"/api/versions/(\d+)/discard")
def version_discard(req, vid):
    v = textproc.require_draft(vid)
    if not v["parent"]:
        raise HTTPError(400, "这是首个版本，不能丢弃；可以删除整本书")
    with db.tx():
        textproc.delete_version(vid)
        db.x("UPDATE books SET current_version=? WHERE id=? AND current_version=?", (v["parent"], v["book_id"], vid))
    return {"ok": True}


# ============================================================ 拆解方法

@route("GET", "/api/templates")
def tpl_list(req):
    return templates.list_all()


@route("GET", r"/api/templates/(\d+)")
def tpl_detail(req, tid):
    t = db.q1("SELECT * FROM templates WHERE id=?", (tid,))
    if not t:
        raise HTTPError(404, "模板不存在")
    vers = db.q("SELECT id,version,created FROM template_versions WHERE template_id=? ORDER BY version DESC", (tid,))
    return {**templates.get_version(t["latest"]), "id": tid, "versions": vers, "latest": t["latest"],
            "compare_dim_options": [d[0] for d in templates.COMPARE_DIMS]}


@route("GET", r"/api/template_versions/(\d+)")
def tpl_version(req, tvid):
    return templates.get_version(tvid)


@route("POST", r"/api/templates/(\d+)/copy")
def tpl_copy(req, tid):
    return {"id": templates.copy(tid)}


@route("POST", r"/api/templates/(\d+)/save")
def tpl_save(req, tid):
    return {"version_id": templates.save_new_version(tid, req.json())}


@route("POST", r"/api/templates/(\d+)/delete")
def tpl_delete(req, tid):
    t = db.q1("SELECT * FROM templates WHERE id=?", (tid,))
    if t["builtin"]:
        raise HTTPError(400, "内置模板不能删除")
    db.x("UPDATE templates SET deleted=1 WHERE id=?", (tid,))
    return {"ok": True}


# ============================================================ 拆书任务

@route("POST", r"/api/books/(\d+)/preview")
def job_preview(req, bid):
    return analysis.preview(bid, req.json().get("scope") or {"type": "first3"})


def job_row(j):
    j = dict(j)
    j["status_label"] = STATUS_LABEL.get(j["status"], j["status"])
    j["scope"] = db.jload(j["scope"], {})
    u = db.q1("SELECT COUNT(*) total, SUM(status='done') done, SUM(kind='chapter') ch_total, "
              "SUM(kind='chapter' AND status='done') ch_done, SUM(status='failed') failed, "
              "SUM(status='uncertain') uncertain FROM units WHERE job_id=?", (j["id"],))
    j["progress"] = {k: u[k] or 0 for k in ("total", "done", "ch_total", "ch_done", "failed", "uncertain")}
    if j["kind"] == "analysis":
        b = db.q1("SELECT title FROM books WHERE id=?", (j["book_id"],))
        j["book_title"] = b["title"] if b else "（已删除）"
        tv = templates.get_version(j["template_version_id"])
        j["template"] = f"{tv['name']} v{tv['version']}" if tv else ""
    else:
        c = db.q1("SELECT title FROM comparisons WHERE id=?", (j["comparison_id"],))
        j["book_title"] = "多书比较：" + (c["title"] if c else "")
        j["template"] = "逐项对照"
    running = db.q1("SELECT label FROM units WHERE job_id=? AND status='running'", (j["id"],))
    j["current"] = running["label"] if running else ""
    j["elapsed_now"] = (j["elapsed"] or 0)
    return j


@route("POST", "/api/jobs")
def job_create(req):
    b = req.json()
    if b.get("engine") not in engines.ENGINES:
        raise HTTPError(400, "请选择模型连接")
    jid = analysis.create_job(int(b["book_id"]), int(b["template_version_id"]), b["engine"], b.get("model", ""),
                              b.get("scope") or {"type": "first3"}, b.get("options") or {}, draft=bool(b.get("draft")))
    if b.get("replace_draft"):
        old = db.q1("SELECT id,status FROM jobs WHERE id=?", (int(b["replace_draft"]),))
        if old and old["status"] == "draft":
            worker.delete_job(old["id"])
    worker.WAKE.set()
    return {"id": jid}


@route("GET", "/api/jobs")
def job_list(req):
    f = req.query.get("filter", "")
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if f == "active":
        sql += " AND status IN ('running','pausing','queued')"
    elif f == "waiting":
        sql += " AND status IN ('draft','paused','waiting_login','waiting_quota')"
    elif f == "ended":
        sql += " AND status IN ('done','partial','failed','cancelled')"
    if req.query.get("book_id"):
        sql += " AND book_id=?"
        args.append(int(req.query["book_id"]))
    return [job_row(j) for j in db.q(sql + " ORDER BY created DESC LIMIT 300", args)]


@route("GET", r"/api/jobs/(\d+)")
def job_detail(req, jid):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j:
        raise HTTPError(404, "任务不存在")
    row = job_row(j)
    units = db.q("SELECT id,seq,kind,chapter_id,part_idx,part_total,label,status,attempts,error,error_kind,started,finished "
                 "FROM units WHERE job_id=? ORDER BY seq", (jid,))
    events = db.q("SELECT ts,level,msg FROM events WHERE job_id=? ORDER BY id DESC LIMIT 80", (jid,))
    if j["status"] == "running" and j["started"]:
        start = db.q1("SELECT MAX(ts) t FROM events WHERE job_id=? AND msg='开始执行'", (jid,))["t"] or time.time()
        fin = db.q1("SELECT MAX(finished) t FROM units WHERE job_id=? AND finished>=?", (jid, start))["t"] or 0
        row["elapsed_now"] = (j["elapsed"] or 0) + (time.time() - max(start, fin))
    row["options"] = db.jload(j["options"], {})
    return {"job": row, "units": units, "events": events, "engine_blocked": worker.BLOCKED.get(j["engine"], "")}


@route("POST", r"/api/jobs/(\d+)/action")
def job_action(req, jid):
    b = req.json()
    return worker.action(jid, b.get("act"), bool(b.get("confirm")))


@route("GET", r"/api/jobs/(\d+)/report")
def job_report(req, jid):
    return reports.job_report(jid)


@route("GET", r"/api/jobs/(\d+)/export\.(md|json)")
def job_export(req, jid, fmt):
    j = db.q1("SELECT j.*, b.title bt FROM jobs j JOIN books b ON b.id=j.book_id WHERE j.id=?", (jid,))
    name = f"{j['bt']}-{j['title']}-#{jid}"
    if fmt == "md":
        return Download(reports.job_markdown(jid).encode("utf-8"), name + ".md", "text/markdown; charset=utf-8")
    return Download(json.dumps(reports.job_json(jid), ensure_ascii=False, indent=1).encode("utf-8"), name + ".json",
                    "application/json; charset=utf-8")


# ============================================================ 人工核对

@route("POST", "/api/reviews")
def review_save(req):
    b = req.json()
    rid, key = int(b["result_id"]), str(b["item_key"])
    status = b.get("status", "待核对")
    if status not in ("待核对", "已核对", "有疑问"):
        raise HTTPError(400, "核对状态无效")
    db.x("INSERT INTO reviews(result_id,item_key,status,note,revision,updated) VALUES(?,?,?,?,?,?) "
         "ON CONFLICT(result_id,item_key) DO UPDATE SET status=excluded.status, note=excluded.note, "
         "revision=excluded.revision, updated=excluded.updated",
         (rid, key, status, str(b.get("note", ""))[:2000], str(b.get("revision", ""))[:4000], db.now()))
    return {"ok": True}


@route("POST", "/api/reviews/reanalyze")
def review_reanalyze(req):
    """针对有疑问的结论，只对该章发起一次新的分析任务（新版本），原结果保留。"""
    b = req.json()
    r = db.q1("SELECT * FROM results WHERE id=?", (int(b["result_id"]),))
    if not r or r["kind"] != "chapter":
        raise HTTPError(400, "只能对章节结论请求重分析")
    job = db.q1("SELECT * FROM jobs WHERE id=?", (r["job_id"],))
    book = db.q1("SELECT * FROM books WHERE id=?", (job["book_id"],))
    if book["current_version"] != job["version_id"]:
        raise HTTPError(400, "正文版本已更新，请在新版本上创建分析任务")
    sec, idx = str(b["item_key"]).split(":")
    item = (db.jload(r["data"], {}).get("sections") or {}).get(sec, [])[int(idx)]
    opts = db.jload(job["options"], {})
    opts["doubt"] = {"result_id": r["id"], "item_key": b["item_key"], "claim": item.get("claim", ""),
                     "note": str(b.get("note", ""))[:1000]}
    jid = analysis.create_job(job["book_id"], job["template_version_id"], job["engine"], job["model"],
                              {"type": "chapters", "chapter_ids": [r["chapter_id"]]}, opts, parent_job=job["id"])
    worker.WAKE.set()
    return {"id": jid}


# ============================================================ 多书比较

@route("GET", "/api/comparisons")
def cmp_list(req):
    out = []
    for c in db.q("SELECT * FROM comparisons ORDER BY updated DESC"):
        items = db.jload(c["items"], {})
        job = db.q1("SELECT status FROM jobs WHERE id=?", (c["job_id"],)) if c["job_id"] else None
        out.append({"id": c["id"], "title": c["title"], "scope": c["scope"], "updated": c["updated"],
                    "books": [b.get("title") for b in items.get("snapshot") or []] or
                             [(db.q1("SELECT title FROM books WHERE id=?", (b["book_id"],)) or {"title": "（已删除）"})["title"]
                              for b in items.get("books") or []],
                    "status": job["status"] if job else "draft",
                    "status_label": STATUS_LABEL.get(job["status"], "") if job else "草稿"})
    return out


@route("GET", "/api/comparisons/candidates")
def cmp_candidates(req):
    """每本书可用于比较的报告。"""
    ids = [int(x) for x in (req.query.get("books") or "").split(",") if x.strip().isdigit()]
    out = []
    for bid in ids:
        b = db.q1("SELECT id,title,grp,current_version FROM books WHERE id=?", (bid,))
        if not b:
            continue
        jobs = [job_row(j) for j in db.q("SELECT * FROM jobs WHERE book_id=? AND kind='analysis' "
                                         "AND status IN ('done','partial') ORDER BY created DESC", (bid,))]
        out.append({**b, "reports": [{"id": j["id"], "title": j["title"], "template": j["template"], "status": j["status"],
                                      "status_label": j["status_label"], "engine": j["engine"], "created": j["created"],
                                      "progress": j["progress"], "scope": j["scope"]} for j in jobs]})
    return out


@route("POST", "/api/comparisons/check")
def cmp_check(req):
    b = req.json()
    return analysis.compare_check(b.get("books") or [], b.get("scope", "first3"))


@route("POST", "/api/comparisons")
def cmp_create(req):
    b = req.json()
    books_ = b.get("books") or []
    if not 2 <= len(books_) <= 6:
        raise HTTPError(400, "请选择 2–6 本书")
    scope = b.get("scope", "first3")
    chk = analysis.compare_check(books_, scope)
    flags = {"differences": chk["differences"], "dims": b.get("dims") or [d[0] for d in templates.COMPARE_DIMS],
             "issues": {str(r["book_id"]): r["issues"] for r in chk["rows"]}}
    title = (b.get("title") or "").strip() or "、".join(r["title"] for r in chk["rows"])[:60] + " 对照"
    cid = b.get("id")
    if cid:
        db.x("UPDATE comparisons SET title=?, scope=?, items=?, flags=?, updated=? WHERE id=?",
             (title, scope, db.jdump({"books": books_}), db.jdump(flags), db.now(), int(cid)))
    else:
        cid = db.x("INSERT INTO comparisons(title,scope,items,flags,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                   (title, scope, db.jdump({"books": books_}), db.jdump(flags), "draft", db.now(), db.now()))
    return {"id": cid, "check": chk}


@route("GET", r"/api/comparisons/(\d+)")
def cmp_detail(req, cid):
    v = reports.comparison_view(cid)
    c = v["comparison"]
    if not c["job_id"]:
        v["check"] = analysis.compare_check(c["items"].get("books") or [], c["scope"])
    return v


@route("POST", r"/api/comparisons/(\d+)/generate")
def cmp_generate(req, cid):
    b = req.json()
    c = db.q1("SELECT * FROM comparisons WHERE id=?", (cid,))
    items = db.jload(c["items"], {})
    chk = analysis.compare_check(items.get("books") or [], c["scope"])
    if sum(r["ok"] for r in chk["rows"]) < 2:
        raise HTTPError(400, "至少需要两本书具备可用的分析报告")
    if (chk["differences"] or not chk["ready"]) and not b.get("ack"):
        raise HTTPError(400, "存在差异或缺失，请确认后继续")
    if b.get("engine") not in engines.ENGINES:
        raise HTTPError(400, "请选择模型连接")
    ok_books = [x for x, r in zip(items["books"], chk["rows"]) if r["ok"]]
    items["books"] = ok_books
    flags = db.jload(c["flags"], {})
    flags["differences"] = chk["differences"]
    db.x("UPDATE comparisons SET items=?, flags=? WHERE id=?", (db.jdump(items), db.jdump(flags), cid))
    return {"job_id": worker.create_comparison_job(cid, b["engine"], b.get("model", ""))}


@route("POST", r"/api/comparisons/(\d+)/delete")
def cmp_delete(req, cid):
    c = db.q1("SELECT * FROM comparisons WHERE id=?", (cid,))
    if c and c["job_id"]:
        j = db.q1("SELECT status FROM jobs WHERE id=?", (c["job_id"],))
        if j and j["status"] in worker.ACTIVE:
            raise HTTPError(400, "比较正在生成，请先取消")
        if j:
            worker.delete_job(c["job_id"])
    db.x("DELETE FROM comparisons WHERE id=?", (cid,))
    return {"ok": True}


@route("GET", r"/api/comparisons/(\d+)/export\.(md|json)")
def cmp_export(req, cid, fmt):
    v = reports.comparison_view(cid)
    name = f"{v['comparison']['title']}-#{cid}"
    if fmt == "md":
        return Download(reports.comparison_markdown(cid).encode("utf-8"), name + ".md", "text/markdown; charset=utf-8")
    return Download(json.dumps({"format": "novel-analyzer/comparison@1", **v}, ensure_ascii=False, indent=1).encode("utf-8"),
                    name + ".json", "application/json; charset=utf-8")


# ============================================================ 数据与导出

@route("GET", "/api/data")
def data_info(req):
    return reports.disk_usage()


@route("GET", "/api/backup")
def backup(req):
    return Download(reports.backup_bytes(), f"拆书工作台备份-{time.strftime('%Y%m%d-%H%M')}.zip", "application/zip")


@route("POST", "/api/restore")
def restore(req):
    if db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('running','pausing','queued')")["n"]:
        raise HTTPError(400, "有任务正在执行或排队，请先暂停或取消")
    res = reports.restore(req.raw())
    worker.recover()
    templates.ensure_builtin()
    return res


# ============================================================ HTTP

class Download:
    def __init__(self, data: bytes, filename: str, ctype: str):
        self.data, self.filename, self.ctype = data, filename, ctype


class Req:
    def __init__(self, h: "Handler", query: dict):
        self.h, self.query = h, query
        self._raw = None

    def raw(self) -> bytes:
        if self._raw is None:
            n = int(self.h.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise HTTPError(413, "文件太大")
            self._raw = self.h.rfile.read(n) if n else b""
        return self._raw

    def json(self) -> dict:
        r = self.raw()
        if not r:
            return {}
        try:
            return json.loads(r.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise HTTPError(400, "请求格式错误")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_body(self, code, body: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        u = urlparse(self.path)
        if method == "GET" and not u.path.startswith("/api/"):
            return self.static(u.path)
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        for m, rx, fn in ROUTES:
            if m != method:
                continue
            mt = rx.match(u.path)
            if not mt:
                continue
            args = [int(a) if a.isdigit() else a for a in mt.groups()]
            req = Req(self, query)
            try:
                if method == "POST":
                    req.raw()   # 先读完请求体，避免残留字节污染同一条 keep-alive 连接上的下一个请求
                res = fn(req, *args)
                if isinstance(res, Download):
                    return self.send_body(200, res.data, res.ctype,
                                          {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.filename)}"})
                return self.send_body(200, json.dumps(res, ensure_ascii=False, default=str).encode("utf-8"),
                                      "application/json; charset=utf-8")
            except HTTPError as e:
                return self.send_body(e.code, json.dumps({"error": str(e)}, ensure_ascii=False).encode(), "application/json")
            except (ValueError, engines.EngineError) as e:
                return self.send_body(400, json.dumps({"error": str(e)}, ensure_ascii=False).encode(), "application/json")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self.send_body(500, json.dumps({"error": f"内部错误：{e}"}, ensure_ascii=False).encode(),
                                      "application/json")
        if method == "POST":
            Req(self, query).raw()
        self.send_body(404, b'{"error":"not found"}', "application/json")

    def static(self, path):
        name = path.strip("/") or "index.html"
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            f = STATIC / "index.html"
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}.get(
            f.suffix.lstrip("."), "application/octet-stream")
        try:
            body = f.read_bytes()
        except OSError:
            # 程序文件被移走或替换（例如升级时旧版服务还在运行）
            msg = ("拆书工作台的程序文件找不到了，可能是升级时旧版本还在后台运行。"
                   "请重新双击「拆书工作台」，它会自动关闭旧版本并启动新版本。")
            return self.send_body(500, msg.encode("utf-8"), "text/plain; charset=utf-8")
        self.send_body(200, body, ctype + "; charset=utf-8")

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


def already_running() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "chaishu"
    except Exception:  # noqa: BLE001
        return False


def main():
    url = f"http://{HOST}:{PORT}"
    open_browser = os.environ.get("CHAISHU_OPEN_BROWSER") == "1"
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        if already_running():
            print(f"拆书工作台已经在运行：{url}")
            if open_browser:
                import webbrowser
                webbrowser.open(url)
            return
        print(f"端口 {PORT} 被其他程序占用，可以换一个端口再试，例如：PORT=8800 python3 server.py")
        sys.exit(1)
    srv.daemon_threads = True
    db.init()
    templates.ensure_builtin()
    worker.recover()
    worker.start()
    print(f"拆书工作台已启动：{url}")
    print(f"数据目录：{db.DATA_DIR}")
    for k, e in engines.ENGINES.items():
        p = e.bin()
        print(f"  {e.label:12s} {'已安装 ' + p if p else '未检测到'}")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, webbrowser.open, [url]).start()
        print("关闭这个窗口就会停止工作台。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
