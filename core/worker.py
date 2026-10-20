"""顺序任务队列（第一版并发固定为 1）与任务状态机（设计稿第 5 节）。

状态：draft 草稿 / queued 排队中 / running 执行中 / pausing 暂停中 / paused 已暂停 /
     waiting_login 等待登录 / waiting_quota 等待额度 / partial 部分完成 / done 已完成 /
     failed 失败 / cancelled 已取消
"""
from __future__ import annotations

import threading
import time
import traceback

from . import analysis, db, engines, templates, textproc
from .engines import EngineError, FRIENDLY

STATUS_LABEL = {
    "draft": "草稿", "queued": "排队中", "running": "执行中", "pausing": "暂停中", "paused": "已暂停",
    "waiting_login": "等待登录", "waiting_quota": "等待额度", "partial": "部分完成", "done": "已完成",
    "failed": "失败", "cancelled": "已取消",
}
ACTIVE = ("queued", "running", "pausing")


class Control:
    def __init__(self):
        self.pause = False
        self.cancel = False
        self.proc = None


CONTROLS: dict[int, Control] = {}
BLOCKED: dict[str, str] = {}      # engine -> 原因（登录失效 / 额度用尽时暂停该引擎的队列）
WAKE = threading.Event()
_lock = threading.Lock()


def ctl(job_id: int) -> Control:
    with _lock:
        return CONTROLS.setdefault(job_id, Control())


def set_status(job_id: int, status: str, reason: str = ""):
    db.x("UPDATE jobs SET status=?, status_reason=? WHERE id=?", (status, reason, job_id))


# ============================================================ 启动恢复

def recover():
    """应用关闭或崩溃后：执行中的任务标为「已暂停：应用中断」，进行中的单元标为「结果待核实」。"""
    for j in db.q("SELECT id FROM jobs WHERE status IN ('running','pausing')"):
        set_status(j["id"], "paused", "应用中断")
        db.log_event(j["id"], "应用中断，任务已暂停。恢复前请确认待核实的单元。", "warn")
    db.x("UPDATE units SET status='uncertain', error='结果待核实：应用中断时请求可能已在远端完成并消耗额度' "
         "WHERE status='running'")


# ============================================================ 用户操作

def action(job_id: int, act: str, confirm: bool = False) -> dict:
    job = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise ValueError("任务不存在")
    s = job["status"]
    c = ctl(job_id)
    if act == "start":
        if s != "draft":
            raise ValueError("只有草稿可以开始")
        db.x("UPDATE jobs SET status='queued', queued_at=? WHERE id=?", (db.now(), job_id))
        db.log_event(job_id, "已提交，等待执行")
    elif act == "pause":
        if s == "running":
            c.pause = True
            set_status(job_id, "pausing", "完成当前章节后暂停")
            db.log_event(job_id, "已请求暂停：完成当前工作单元后暂停")
        elif s == "queued":
            set_status(job_id, "paused", "用户暂停")
        else:
            raise ValueError("当前状态不能暂停")
    elif act == "cancel":
        if s in ("done", "cancelled"):
            raise ValueError("当前状态不能取消")
        c.cancel = True
        set_status(job_id, "cancelled", "用户取消")
        if c.proc:
            engines.interrupt(c.proc)
        db.x("UPDATE units SET status='pending' WHERE job_id=? AND status='running'", (job_id,))
        db.log_event(job_id, "已取消，已校验的结果保留")
    elif act in ("resume", "retry"):
        if s not in ("paused", "waiting_login", "waiting_quota", "failed", "partial", "cancelled"):
            raise ValueError("当前状态不能继续")
        if s == "cancelled":
            raise ValueError("已取消的任务不能继续，请创建后续任务")
        unc = db.q1("SELECT COUNT(*) n FROM units WHERE job_id=? AND status='uncertain'", (job_id,))["n"]
        if unc and not confirm:
            return {"need_confirm": unc,
                    "message": f"有 {unc} 个单元结果待核实：应用中断时请求可能已在远端完成并消耗了额度。确认后将重新分析这些单元。"}
        with db.tx():
            db.x("UPDATE units SET status='pending', error='' WHERE job_id=? AND status='uncertain'", (job_id,))
            if s in ("partial", "failed") or act == "retry":
                db.x("UPDATE units SET status='pending', attempts=0, error='', error_kind='' "
                     "WHERE job_id=? AND status IN ('failed','skipped')", (job_id,))
            db.x("UPDATE jobs SET status='queued', status_reason='', queued_at=?, finished=NULL WHERE id=?",
                 (db.now(), job_id))
        BLOCKED.pop(job["engine"], None)
        c.pause = c.cancel = False
        db.log_event(job_id, "已恢复，只处理未完成的单元")
    elif act == "gap_summary":
        if s != "partial":
            raise ValueError("只有部分完成的任务可以生成带缺口的汇总")
        with db.tx():
            for u in db.q("SELECT * FROM units WHERE job_id=? AND kind IN ('segment','summary') AND status IN ('skipped','failed')",
                          (job_id,)):
                extra = db.jload(u["extra"], {})
                extra["gap"] = True
                db.x("UPDATE units SET status='pending', attempts=0, error='', extra=? WHERE id=?", (db.jdump(extra), u["id"]))
            db.x("UPDATE jobs SET status='queued', status_reason='生成带缺口的范围汇总', queued_at=? WHERE id=?",
                 (db.now(), job_id))
        c.pause = c.cancel = False
        db.log_event(job_id, "将使用已完成章节生成带缺口标注的范围汇总")
    elif act == "delete":
        if s in ACTIVE:
            raise ValueError("任务执行中，请先取消")
        delete_job(job_id)
        return {"ok": True}
    else:
        raise ValueError("未知操作")
    WAKE.set()
    return {"ok": True}


def delete_job(job_id: int):
    with db.tx():
        rids = [r["id"] for r in db.q("SELECT id FROM results WHERE job_id=?", (job_id,))]
        if rids:
            db.x(f"DELETE FROM reviews WHERE result_id IN ({','.join('?' * len(rids))})", rids)
        for t in ("results", "units", "events"):
            db.x(f"DELETE FROM {t} WHERE job_id=?", (job_id,))
        db.x("DELETE FROM jobs WHERE id=?", (job_id,))


# ============================================================ 执行

def next_job() -> dict | None:
    for j in db.q("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, queued_at"):
        if j["engine"] in BLOCKED:
            continue
        return j
    return None


def loop():
    db.init()
    while True:
        try:
            job = next_job()
            if not job:
                WAKE.wait(2)
                WAKE.clear()
                continue
            run_job(job)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            time.sleep(2)


def start():
    threading.Thread(target=loop, daemon=True, name="job-worker").start()


def run_job(job: dict):
    jid = job["id"]
    c = ctl(jid)
    c.pause = c.cancel = False
    set_status(jid, "running", "")
    if not job["started"]:
        db.x("UPDATE jobs SET started=? WHERE id=?", (db.now(), jid))
    t0 = time.time()
    db.log_event(jid, "开始执行")
    try:
        while True:
            if c.cancel:
                return
            if c.pause:
                set_status(jid, "paused", "用户暂停")
                db.log_event(jid, "已暂停")
                return
            unit = db.q1("SELECT * FROM units WHERE job_id=? AND status='pending' ORDER BY seq LIMIT 1", (jid,))
            if not unit:
                finalize(jid)
                return
            outcome = run_unit(job, unit, c)
            now = time.time()
            db.x("UPDATE jobs SET elapsed=COALESCE(elapsed,0)+? WHERE id=?", (now - t0, jid))
            t0 = now
            if outcome == "stop":
                return
    finally:
        db.x("UPDATE jobs SET elapsed=COALESCE(elapsed,0)+? WHERE id=?", (time.time() - t0, jid))
        c.proc = None


def finalize(jid: int):
    units = db.q("SELECT kind,status FROM units WHERE job_id=?", (jid,))
    done = [u for u in units if u["status"] == "done"]
    if len(done) == len(units):
        set_status(jid, "done", "")
        db.log_event(jid, "选定范围的章节与汇总均已完成并通过校验")
    elif done:
        failed = sum(1 for u in units if u["status"] == "failed")
        skipped = sum(1 for u in units if u["status"] == "skipped")
        set_status(jid, "partial", f"{failed} 个单元失败，{skipped} 个汇总未生成")
        db.log_event(jid, "部分完成：可以查看已有结果，重试未完成部分，或生成带缺口的汇总", "warn")
    else:
        set_status(jid, "failed", "没有获得有效结果")
    db.x("UPDATE jobs SET finished=? WHERE id=?", (db.now(), jid))


def run_unit(job: dict, unit: dict, c: Control) -> str:
    jid = job["id"]
    eng = engines.get(job["engine"])
    opts = db.jload(job["options"], {})
    retries = int(opts.get("retries", 2))
    try:
        prep = prepare_unit(job, unit)
    except SkipUnit as s:
        db.x("UPDATE units SET status='skipped', error=? WHERE id=?", (str(s), unit["id"]))
        db.log_event(jid, f"{unit['label']}：{s}", "warn")
        return "next"
    if prep is None:
        return "next"
    prompt, schema, post = prep
    db.x("UPDATE units SET status='running', started=?, error='' WHERE id=?", (db.now(), unit["id"]))
    attempt = 0
    while True:
        attempt += 1
        db.x("UPDATE units SET attempts=attempts+1 WHERE id=?", (unit["id"],))

        def register(p):
            c.proc = p
        try:
            data, raw, usage = eng.call(prompt, schema, job["model"], register)
            normalized = post(data)
            save_result(job, unit, normalized, raw, usage)
            db.log_event(jid, f"{unit['label']}：结果已校验并保存")
            return "next"
        except EngineError as e:
            if c.cancel or e.kind == "cancelled":
                db.x("UPDATE units SET status='pending' WHERE id=?", (unit["id"],))
                return "stop"
            db.x("UPDATE units SET error=?, error_kind=? WHERE id=?", (e.message[-500:], e.kind, unit["id"]))
            db.log_event(jid, f"{unit['label']} 第 {attempt} 次尝试：{FRIENDLY.get(e.kind, '')}。{e.message[-300:]}", "error")
            if e.kind == "login":
                return halt(job, unit, "waiting_login", "需要重新登录：" + FRIENDLY["login"], block=True)
            if e.kind == "quota":
                return halt(job, unit, "waiting_quota", "额度已用完或触发频率限制" + (f"（{e.hint}）" if e.hint else ""),
                            block=True)
            if e.kind == "model":
                return halt(job, unit, "failed", "所选模型不可用或无权限，请更换模型后创建新任务")
            if attempt <= retries and e.kind in ("network", "timeout", "unknown", "format"):
                wait = 5 if attempt == 1 else 20
                if e.kind == "format":
                    wait = 1
                if not sleep_unless_cancelled(c, wait):
                    db.x("UPDATE units SET status='pending' WHERE id=?", (unit["id"],))
                    return "stop"
                continue
            if e.kind == "format":
                db.x("UPDATE units SET status='failed', finished=? WHERE id=?", (db.now(), unit["id"]))
                db.log_event(jid, f"{unit['label']}：多次输出未通过校验，标为失败，继续后面的单元", "warn")
                return "next"
            return halt(job, unit, "paused", f"{FRIENDLY.get(e.kind, '调用失败')}，自动重试后仍失败，已暂停")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            db.x("UPDATE units SET status='failed', error=?, error_kind='unknown', finished=? WHERE id=?",
                 (str(e)[:500], db.now(), unit["id"]))
            db.log_event(jid, f"{unit['label']}：程序错误 {e}", "error")
            return "next"


def halt(job, unit, status, reason, block=False) -> str:
    db.x("UPDATE units SET status='pending' WHERE id=?", (unit["id"],))
    set_status(job["id"], status, reason)
    if block:
        BLOCKED[job["engine"]] = reason
    db.log_event(job["id"], reason, "warn")
    return "stop"


def sleep_unless_cancelled(c: Control, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if c.cancel:
            return False
        time.sleep(0.3)
    return True


class SkipUnit(Exception):
    pass


def prepare_unit(job, unit):
    """返回 (prompt, schema, 后处理函数)。"""
    if job["kind"] == "comparison":
        return prepare_comparison(job)
    tv = templates.get_version(job["template_version_id"])
    book = db.q1("SELECT * FROM books WHERE id=?", (job["book_id"],))
    keys = [s["key"] for s in tv["sections"]]
    extra = db.jload(unit["extra"], {})
    if unit["kind"] == "chapter":
        ch = db.q1("SELECT * FROM chapters WHERE id=?", (unit["chapter_id"],))
        paras = textproc.chapter_paragraphs(ch["id"])
        if unit["part_total"] > 1:
            paras = split_parts(paras, unit["part_total"])[unit["part_idx"]]
        prompt = analysis.chapter_prompt(job, tv, book, ch, paras, unit["part_idx"], unit["part_total"])
        valid = analysis.para_index(job["version_id"], [ch["id"]])
        return prompt, analysis.chapter_schema(keys), lambda d: analysis.normalize_sections(d, keys, valid, ch["id"])
    # 分段汇总 / 范围汇总
    gap = bool(extra.get("gap"))
    cids = extra.get("chapter_ids") or []
    chs = db.q(f"SELECT id,seq,title FROM chapters WHERE id IN ({','.join('?' * len(cids))}) ORDER BY seq", cids) if cids else []
    results = db.q("SELECT r.*, c.seq, c.title FROM results r JOIN chapters c ON c.id=r.chapter_id "
                   "WHERE r.job_id=? AND r.kind='chapter' ORDER BY c.seq", (job["id"],))
    results = [r for r in results if r["chapter_id"] in set(cids)]
    have = {r["chapter_id"] for r in results}
    missing = [c for c in chs if c["id"] not in have]
    if missing and not gap:
        raise SkipUnit(f"有 {len(missing)} 章没有有效结果，未自动生成汇总")
    if not results:
        raise SkipUnit("没有任何已完成的章节结果")
    skeys = ["overview"] + keys
    scope = db.jload(job["scope"], {})
    title = analysis.scope_title(scope, chs, bool(missing))
    valid = analysis.para_index(job["version_id"], list(have))
    segments = None
    if unit["kind"] == "summary":
        segs = db.q("SELECT r.*, u.label FROM results r JOIN units u ON u.id=r.unit_id "
                    "WHERE r.job_id=? AND r.kind='segment' ORDER BY u.seq", (job["id"],))
        if db.q1("SELECT COUNT(*) n FROM units WHERE job_id=? AND kind='segment'", (job["id"],))["n"]:
            segments = segs or None
            if not segs:
                raise SkipUnit("分段汇总未完成")
    prompt = analysis.summary_prompt(job, tv, book, title, results, missing, unit["kind"], segments)

    def post(d):
        out = analysis.normalize_sections(d, skeys, valid, None)
        out.update({"title": title if unit["kind"] == "summary" else unit["label"],
                    "covered": [{"seq": r["seq"], "title": r["title"]} for r in results],
                    "missing": [{"seq": m["seq"], "title": m["title"]} for m in missing], "gap": bool(missing)})
        return out
    if unit["kind"] == "summary":
        db.x("UPDATE jobs SET title=? WHERE id=?", (title, job["id"]))
    return prompt, analysis.chapter_schema(skeys), post


def split_parts(paras: list[dict], n: int) -> list[list[dict]]:
    total = sum(len(p["text"]) for p in paras)
    target = total / n
    parts, cur, size = [], [], 0
    for p in paras:
        cur.append(p)
        size += len(p["text"])
        if size >= target and len(parts) < n - 1:
            parts.append(cur)
            cur, size = [], 0
    parts.append(cur)
    while len(parts) < n:
        parts.append([])
    return parts


def save_result(job, unit, data, raw, usage):
    """同一单元只保留一份结果；只有仍处于执行中的单元才写入，避免重复回调产生重复报告。"""
    kind = unit["kind"]
    with db.tx():
        cur = db.q1("SELECT status FROM units WHERE id=?", (unit["id"],))
        if not cur or cur["status"] != "running":
            return
        db.x("DELETE FROM results WHERE unit_id=?", (unit["id"],))
        rkind = "part" if kind == "chapter" and unit["part_total"] > 1 else kind
        db.x("INSERT INTO results(job_id,unit_id,kind,chapter_id,part_idx,data,raw,usage,created) VALUES(?,?,?,?,?,?,?,?,?)",
             (job["id"], unit["id"], rkind, unit["chapter_id"], unit["part_idx"], db.jdump(data), raw, db.jdump(usage), db.now()))
        db.x("UPDATE units SET status='done', finished=?, error='', error_kind='' WHERE id=?", (db.now(), unit["id"]))
        if rkind == "part":
            sib = db.q("SELECT id,status FROM units WHERE job_id=? AND chapter_id=? AND kind='chapter'", (job["id"], unit["chapter_id"]))
            if all(s["status"] == "done" for s in sib):
                parts = db.q("SELECT data FROM results WHERE job_id=? AND chapter_id=? AND kind='part' ORDER BY part_idx",
                             (job["id"], unit["chapter_id"]))
                tv = templates.get_version(job["template_version_id"])
                merged = analysis.merge_parts([db.jload(p["data"], {}) for p in parts], [s["key"] for s in tv["sections"]])
                db.x("DELETE FROM results WHERE job_id=? AND chapter_id=? AND kind='chapter'", (job["id"], unit["chapter_id"]))
                db.x("INSERT INTO results(job_id,unit_id,kind,chapter_id,data,raw,usage,created) VALUES(?,?,?,?,?,?,?,?)",
                     (job["id"], unit["id"], "chapter", unit["chapter_id"], db.jdump(merged), "", "{}", db.now()))
        if kind == "comparison":
            db.x("UPDATE comparisons SET status='done', updated=? WHERE job_id=?", (db.now(), job["id"]))


# ============================================================ 多书比较

def prepare_comparison(job):
    cmp = db.q1("SELECT * FROM comparisons WHERE id=?", (job["comparison_id"],))
    snap = db.jload(cmp["items"], {}).get("snapshot") or []
    scope = cmp["scope"]
    dims = db.jload(cmp["flags"], {}).get("dims") or [d[0] for d in templates.COMPARE_DIMS]
    prompt = analysis.compare_prompt(snap, scope, dims)
    return prompt, analysis.COMPARE_SCHEMA, lambda d: analysis.normalize_compare(d, snap, dims)


def create_comparison_job(cmp_id: int, engine: str, model: str) -> int:
    cmp = db.q1("SELECT * FROM comparisons WHERE id=?", (cmp_id,))
    items = db.jload(cmp["items"], {})
    snap = analysis.compare_snapshot(items["books"], cmp["scope"])
    items["snapshot"] = snap
    with db.tx():
        jid = db.x("INSERT INTO jobs(kind,comparison_id,engine,model,scope,options,title,status,created,queued_at)"
                   " VALUES('comparison',?,?,?,?,?,?,?,?,?)",
                   (cmp_id, engine, model or "", db.jdump({"type": cmp["scope"]}), db.jdump({"retries": 2}),
                    cmp["title"], "queued", db.now(), db.now()))
        db.x("INSERT INTO units(job_id,seq,kind,label) VALUES(?,?,?,?)", (jid, 1, "comparison", "生成逐项对照"))
        db.x("UPDATE comparisons SET items=?, job_id=?, status='running', updated=? WHERE id=?",
             (db.jdump(items), jid, db.now(), cmp_id))
    db.log_event(jid, "比较任务已提交")
    WAKE.set()
    return jid
