"""TXT 导入、编码检查、章节切分、目录编辑。原始文件只读保存，切分结果作为独立文本版本。"""
from __future__ import annotations

import hashlib
import re
import statistics

from . import db

# ---------------------------------------------------------------- 编码与文本检查

def decode(raw: bytes) -> tuple[str, str]:
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace"), "UTF-8 (BOM)"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace"), "UTF-16"
    for enc, label in (("utf-8", "UTF-8"), ("gb18030", "GBK / GB18030"), ("big5", "Big5")):
        try:
            return raw.decode(enc), label
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace"), "UTF-8（含无法识别字节）"


def inspect_text(text: str) -> dict:
    """返回 level: ok / warn / block 以及问题说明。"""
    body = text.strip()
    issues, level = [], "ok"
    if len(body) < 100:
        return {"level": "block", "issues": ["文件为空或正文过短，无法分析"], "n_chars": len(body)}
    n = len(body)
    bad = sum(1 for ch in body if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF
              or (ord(ch) < 32 and ch not in "\n\r\t"))
    cjk = sum(1 for ch in body if "一" <= ch <= "鿿")
    common = sum(body.count(c) for c in "的了是不我一他她你在有这")
    ratio = bad / n
    if ratio > 0.02:
        level = "block"
        issues.append(f"检测到大量无法识别的字符（约 {ratio:.1%}），可能是编码错误或文件损坏")
    elif ratio > 0.0005:
        level = "warn"
        issues.append(f"检测到少量可疑字符（{bad} 个），请在预览里核对")
    # 正常中文小说里「的了是不我…」约占 10%–20%；乱码（如 UTF-8 被当成 GBK）几乎为 0
    if cjk / n > 0.3 and common / n < 0.02:
        level = "block"
        issues.append("中文常用字几乎没有出现，很可能是编码错误导致的乱码")
    elif cjk / n > 0.3 and common / n < 0.05:
        level = "block" if level == "block" else "warn"
        issues.append("中文常用字比例偏低，可能有乱码，请核对预览")
    return {"level": level, "issues": issues, "n_chars": n}


def guess_meta(filename: str, text: str) -> tuple[str, str]:
    title = re.sub(r"\.(txt|TXT)$", "", filename).strip() or "未命名"
    author = ""
    m = re.match(r"^《(.+?)》(?:\s*作者[:：]?\s*(.+))?$", title)
    if m:
        title, author = m.group(1), (m.group(2) or "").strip()
    head = text[:1500]
    m = re.search(r"作者[:：]\s*([^\s\n]{1,20})", head)
    if m and not author:
        author = m.group(1)
    m = re.search(r"(?:书名|^)\s*《(.{1,40}?)》", head, re.M)
    if m and title == "未命名":
        title = m.group(1)
    return title, author


# ---------------------------------------------------------------- 切分规则

NUM = r"[0-9０-９零〇一二两三四五六七八九十百千万]+"
# 标题后不能紧跟虚词（避免把「第三章的内容……」这类正文句子当成标题），整行不能含句号
TAIL = r"(?![的了里中内后前之是也都就还])[^\n。]{0,40}$"
RULES = {
    "auto": {"label": "自动识别（第X章 / 回 / 节 / 话、序章、楔子、尾声、番外）",
             "re": rf"^[ \t　]*(?:第{NUM}[章回节話话卷]|序章|序言|序幕|楔子|引子|尾声|后记|终章|番外|Chapter\s*\d+){TAIL}"},
    "zh_chapter": {"label": "仅「第X章」", "re": rf"^[ \t　]*第{NUM}章{TAIL}"},
    "number": {"label": "数字编号（1. / 001 / 1、开头的短行）",
               "re": r"^[ \t　]*\d{1,4}[\.、．\s][^\n]{0,30}$"},
    "length": {"label": "无章节标题，按约 5000 字临时分段", "re": None},
}


def split_text(text: str, rule: str = "auto", custom: str | None = None) -> tuple[list[dict], dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    info = {"rule": rule, "custom": custom, "ignored_toc": 0, "segmented": False}
    pattern = custom if rule == "custom" else RULES.get(rule, RULES["auto"])["re"]
    chapters: list[dict] = []
    if pattern:
        try:
            rx = re.compile(pattern, re.M)
        except re.error as e:
            raise ValueError(f"切分规则无效：{e}")
        ms = list(rx.finditer(text))
        if ms:
            pre = text[:ms[0].start()].strip()
            if len(pre) > 200:
                chapters.append({"title": "开篇 / 简介", "kind": "front", "body": pre})
            raw = []
            for i, m in enumerate(ms):
                end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
                raw.append({"title": m.group(0).strip(), "kind": "chapter", "body": text[m.end():end].strip()})
            kept = [c for c in raw if len(c["body"]) >= 50]
            info["ignored_toc"] = len(raw) - len(kept)
            for c in kept:
                if "番外" in c["title"]:
                    c["kind"] = "extra"
            chapters += kept
    if not [c for c in chapters if c["kind"] != "front"]:
        chapters = []
        info["segmented"] = True
        buf, n = [], 0
        for para in text.split("\n"):
            buf.append(para)
            n += len(para.strip())
            if n >= 5000:
                chapters.append({"title": f"文本分段 {len(chapters) + 1}", "kind": "segment", "body": "\n".join(buf)})
                buf, n = [], 0
        if "".join(buf).strip():
            chapters.append({"title": f"文本分段 {len(chapters) + 1}", "kind": "segment", "body": "\n".join(buf)})
    return chapters, info


def paragraphs_of(body: str) -> list[str]:
    return [p.strip().strip("　").strip() for p in body.split("\n") if p.strip().strip("　").strip()]


# ---------------------------------------------------------------- 书籍与版本

def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def original_path(h: str):
    return db.ORIGINALS / f"{h}.txt"


def import_file(raw: bytes, filename: str, force_copy: bool = False) -> dict:
    h = file_hash(raw)
    if not force_copy:
        dup = db.q("SELECT id,title FROM books WHERE file_hash=?", (h,))
        if dup:
            return {"status": "duplicate", "existing": dup}
    text, enc = decode(raw)
    check = inspect_text(text)
    if check["level"] == "block" and check["n_chars"] < 100:
        return {"status": "error", "error": check["issues"][0]}
    p = original_path(h)
    if not p.exists():
        p.write_bytes(raw)
    title, author = guess_meta(filename, text)
    t = db.now()
    with db.tx():
        bid = db.x("INSERT INTO books(title,author,grp,file_name,file_hash,file_size,encoding,created,updated)"
                   " VALUES(?,?,?,?,?,?,?,?,?)", (title, author, "", filename, h, len(raw), enc, t, t))
        vid = build_version(bid, text, "auto", None, parent=None)
        db.x("UPDATE books SET current_version=? WHERE id=?", (vid, bid))
    return {"status": "ok", "book_id": bid, "check": check, "encoding": enc}


def book_text(book: dict) -> str:
    raw = original_path(book["file_hash"]).read_bytes()
    return decode(raw)[0]


def build_version(book_id: int, text: str, rule: str, custom: str | None, parent: int | None) -> int:
    chapters, info = split_text(text, rule, custom)
    vno = (db.q1("SELECT MAX(vno) m FROM text_versions WHERE book_id=?", (book_id,))["m"] or 0) + 1
    vid = db.x("INSERT INTO text_versions(book_id,vno,status,split_rule,segmented,parent,created)"
               " VALUES(?,?,?,?,?,?,?)",
               (book_id, vno, "draft", db.jdump(info), int(info["segmented"]), parent, db.now()))
    for i, c in enumerate(chapters, 1):
        paras = paragraphs_of(c["body"])
        cid = db.x("INSERT INTO chapters(version_id,seq,title,kind,excluded,n_chars) VALUES(?,?,?,?,?,?)",
                   (vid, i, c["title"], c["kind"], 0, sum(len(p) for p in paras)))
        conn = db.conn()
        conn.executemany("INSERT INTO paragraphs(version_id,chapter_id,pseq,text) VALUES(?,?,?,?)",
                         [(vid, cid, j, p) for j, p in enumerate(paras, 1)])
    refresh_counts(vid)
    return vid


def refresh_counts(vid: int):
    for ch in db.q("SELECT id FROM chapters WHERE version_id=?", (vid,)):
        r = db.q1("SELECT COALESCE(SUM(LENGTH(text)),0) n FROM paragraphs WHERE chapter_id=? AND excluded=0", (ch["id"],))
        db.x("UPDATE chapters SET n_chars=? WHERE id=?", (r["n"], ch["id"]))
    r = db.q1("SELECT COUNT(*) c, COALESCE(SUM(n_chars),0) n FROM chapters WHERE version_id=? AND excluded=0", (vid,))
    db.x("UPDATE text_versions SET n_chapters=?, n_chars=? WHERE id=?", (r["c"], r["n"], vid))


def renumber(vid: int):
    for i, ch in enumerate(db.q("SELECT id FROM chapters WHERE version_id=? ORDER BY seq, id", (vid,)), 1):
        db.x("UPDATE chapters SET seq=? WHERE id=?", (i, ch["id"]))


def renumber_paras(cid: int):
    for j, p in enumerate(db.q("SELECT id FROM paragraphs WHERE chapter_id=? ORDER BY pseq, id", (cid,)), 1):
        db.x("UPDATE paragraphs SET pseq=? WHERE id=?", (j, p["id"]))


def version_checks(vid: int) -> dict:
    chs = db.q("SELECT id,seq,title,kind,excluded,n_chars FROM chapters WHERE version_id=? ORDER BY seq", (vid,))
    active = [c for c in chs if not c["excluded"] and c["kind"] != "front"]
    med = statistics.median([c["n_chars"] for c in active]) if active else 0
    titles = {}
    for c in chs:
        key = re.sub(r"\s+", "", c["title"])
        titles.setdefault(key, []).append(c["seq"])
    flags = {}
    for c in chs:
        f = []
        if c["kind"] == "segment":
            f.append("文本分段")
        if not c["excluded"] and c["kind"] != "front":
            if c["n_chars"] < 300:
                f.append("字数过少")
            elif med and c["n_chars"] > max(20000, med * 3):
                f.append("字数异常偏多")
        if len(titles[re.sub(r"\s+", "", c["title"])]) > 1:
            f.append("可能重复标题")
        flags[c["id"]] = f
    return {
        "median": med,
        "flags": flags,
        "n_short": sum("字数过少" in v for v in flags.values()),
        "n_long": sum("字数异常偏多" in v for v in flags.values()),
        "n_dup": sum("可能重复标题" in v for v in flags.values()),
    }


def require_draft(vid: int) -> dict:
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (vid,))
    if not v:
        raise ValueError("文本版本不存在")
    if v["status"] != "draft":
        raise ValueError("已确认的目录不能直接修改，请先「修改目录（生成新版本）」")
    return v


def op_rename(vid: int, cid: int, title: str):
    require_draft(vid)
    db.x("UPDATE chapters SET title=? WHERE id=? AND version_id=?", (title.strip()[:80] or "未命名章节", cid, vid))


def op_set_kind(vid: int, cid: int, kind: str):
    require_draft(vid)
    if kind not in ("chapter", "segment", "front", "extra"):
        raise ValueError("类型无效")
    db.x("UPDATE chapters SET kind=? WHERE id=? AND version_id=?", (kind, cid, vid))


def op_exclude_chapter(vid: int, cid: int, excluded: bool):
    require_draft(vid)
    db.x("UPDATE chapters SET excluded=? WHERE id=? AND version_id=?", (int(excluded), cid, vid))
    refresh_counts(vid)


def op_exclude_para(vid: int, pid: int, excluded: bool):
    require_draft(vid)
    db.x("UPDATE paragraphs SET excluded=? WHERE id=? AND version_id=?", (int(excluded), pid, vid))
    refresh_counts(vid)


def op_merge_next(vid: int, cid: int):
    require_draft(vid)
    ch = db.q1("SELECT * FROM chapters WHERE id=? AND version_id=?", (cid, vid))
    nxt = db.q1("SELECT * FROM chapters WHERE version_id=? AND seq>? ORDER BY seq LIMIT 1", (vid, ch["seq"]))
    if not nxt:
        raise ValueError("已经是最后一章")
    with db.tx():
        base = db.q1("SELECT COALESCE(MAX(pseq),0) m FROM paragraphs WHERE chapter_id=?", (cid,))["m"]
        first = db.q1("SELECT text FROM paragraphs WHERE chapter_id=? ORDER BY pseq LIMIT 1", (nxt["id"],))
        offset = base
        if not first or not first["text"].startswith(nxt["title"]):
            # 被合并章节的标题原本是正文里的一行，合并后作为一段保留
            offset = base + 1
            db.x("INSERT INTO paragraphs(version_id,chapter_id,pseq,text) VALUES(?,?,?,?)",
                 (vid, cid, offset, nxt["title"]))
        db.x("UPDATE paragraphs SET chapter_id=?, pseq=pseq+? WHERE chapter_id=?", (cid, offset, nxt["id"]))
        db.x("DELETE FROM chapters WHERE id=?", (nxt["id"],))
        renumber_paras(cid)
        renumber(vid)
    refresh_counts(vid)


def op_split_at(vid: int, pid: int):
    require_draft(vid)
    p = db.q1("SELECT * FROM paragraphs WHERE id=? AND version_id=?", (pid, vid))
    if not p:
        raise ValueError("段落不存在")
    if p["pseq"] == 1:
        raise ValueError("已经是本章第一段，无需拆分")
    ch = db.q1("SELECT * FROM chapters WHERE id=?", (p["chapter_id"],))
    title = p["text"][:30] if len(p["text"]) <= 30 else f"{ch['title']}（下）"
    with db.tx():
        db.x("UPDATE chapters SET seq=seq+1 WHERE version_id=? AND seq>?", (vid, ch["seq"]))
        nid = db.x("INSERT INTO chapters(version_id,seq,title,kind) VALUES(?,?,?,?)",
                   (vid, ch["seq"] + 1, title, ch["kind"]))
        db.x("UPDATE paragraphs SET chapter_id=? WHERE chapter_id=? AND pseq>=?", (nid, ch["id"], p["pseq"]))
        renumber_paras(nid)
        renumber(vid)
    refresh_counts(vid)
    return nid


def op_resplit(vid: int, rule: str, custom: str | None) -> int:
    """按新规则重新切分：删除当前草稿，生成新的草稿版本。"""
    v = require_draft(vid)
    book = db.q1("SELECT * FROM books WHERE id=?", (v["book_id"],))
    text = book_text(book)
    with db.tx():
        nid = build_version(book["id"], text, rule, custom, parent=v["parent"])
        delete_version(vid)
        db.x("UPDATE books SET current_version=? WHERE id=? AND current_version=?", (nid, book["id"], vid))
    return nid


def delete_version(vid: int):
    db.x("DELETE FROM paragraphs WHERE version_id=?", (vid,))
    db.x("DELETE FROM chapters WHERE version_id=?", (vid,))
    db.x("DELETE FROM text_versions WHERE id=?", (vid,))


def confirm(vid: int) -> dict:
    v = require_draft(vid)
    active = db.q1("SELECT COUNT(*) c FROM chapters WHERE version_id=? AND excluded=0", (vid,))["c"]
    if not active:
        raise ValueError("没有可用章节，无法确认")
    book = db.q1("SELECT * FROM books WHERE id=?", (v["book_id"],))
    check = inspect_text(book_text(book))
    if check["level"] == "block":
        raise ValueError("文本存在乱码或为空，不能用于分析：" + "；".join(check["issues"]))
    db.x("UPDATE text_versions SET status='confirmed', confirmed=? WHERE id=?", (db.now(), vid))
    db.x("UPDATE books SET current_version=?, updated=? WHERE id=?", (vid, db.now(), v["book_id"]))
    return {"ok": True}


def new_draft_from(vid: int) -> int:
    """从已确认版本复制出一个可编辑草稿（旧版本与其报告保持不变）。"""
    v = db.q1("SELECT * FROM text_versions WHERE id=?", (vid,))
    existing = db.q1("SELECT id FROM text_versions WHERE book_id=? AND status='draft'", (v["book_id"],))
    if existing:
        return existing["id"]
    vno = db.q1("SELECT MAX(vno) m FROM text_versions WHERE book_id=?", (v["book_id"],))["m"] + 1
    with db.tx():
        nid = db.x("INSERT INTO text_versions(book_id,vno,status,split_rule,segmented,n_chars,n_chapters,parent,created)"
                   " VALUES(?,?,?,?,?,?,?,?,?)",
                   (v["book_id"], vno, "draft", v["split_rule"], v["segmented"], v["n_chars"], v["n_chapters"], vid, db.now()))
        for ch in db.q("SELECT * FROM chapters WHERE version_id=? ORDER BY seq", (vid,)):
            ncid = db.x("INSERT INTO chapters(version_id,seq,title,kind,excluded,n_chars) VALUES(?,?,?,?,?,?)",
                        (nid, ch["seq"], ch["title"], ch["kind"], ch["excluded"], ch["n_chars"]))
            db.conn().execute("INSERT INTO paragraphs(version_id,chapter_id,pseq,text,excluded)"
                              " SELECT ?,?,pseq,text,excluded FROM paragraphs WHERE chapter_id=?", (nid, ncid, ch["id"]))
    return nid


def chapter_paragraphs(cid: int, include_excluded=False) -> list[dict]:
    sql = "SELECT id,pseq,text,excluded FROM paragraphs WHERE chapter_id=?"
    if not include_excluded:
        sql += " AND excluded=0"
    return db.q(sql + " ORDER BY pseq", (cid,))
