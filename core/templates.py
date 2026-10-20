"""拆解方法（模板）。内置模板只读，可复制为个人模板；修改模板生成新版本，旧任务继续使用创建时的版本。"""
from __future__ import annotations

from . import db

S = {
    "promise": ("开场与期待", "开篇给读者什么问题或期待（故事承诺）？哪些信息已交代，哪些被刻意暂缓？"),
    "function": ("本章叙事功能", "这一章在叙事中承担什么功能？"),
    "state_change": ("开头与结尾状态", "开头与结尾相比，人物的目标、关系、已知信息和处境分别发生了什么变化？"),
    "characters": ("人物目标与阻力", "角色想要什么？遇到什么阻力？这一章人物关系如何变化？"),
    "conflict": ("关键冲突", "谁采取行动？按「触发 → 行动 → 后果 → 遗留问题」写清冲突，并说明冲突升级还是缓解。"),
    "emotion": ("情绪与期待", "文本用什么手段制造紧张、委屈、期待或满足？（这是文本分析判断，不是读者实测反应）"),
    "chapter_end": ("信息与章尾牵引", "新信息在哪里出现？章末留下什么未解决事项？下一章可能承担什么功能（推测须标为推断）？"),
    "method": ("可迁移方法", "哪种叙事手法可迁移到其他故事？成立条件是什么？有哪些反例，或哪些仅凭当前材料无法确认？"),
    "rel_stage": ("关系阶段", "男女主关系处于什么阶段（相识、试探、暧昧、确认、磨合、危机、重建等）？依据是什么？"),
    "initiative": ("双方主动性", "这一章里谁在推动关系？双方各自主动做了什么、回避了什么？"),
    "career": ("职业事件的作用", "职业 / 工作事件如何影响人物处境和关系推进？"),
    "family": ("家庭矛盾", "原生家庭、婚姻家庭或亲属带来什么矛盾？如何作用于主线？"),
    "choice": ("女主选择", "女主面临什么选择？她如何选择，代价与动机是什么？"),
}

COMPARE_DIMS = [
    ("开场期待", "各书开篇向读者提出的问题或承诺"),
    ("主角目标", "主角想要什么、阻力是什么"),
    ("第一轮冲突", "第一轮冲突的触发、行动与后果"),
    ("关系推进", "主要人物关系在范围内的变化"),
    ("章尾悬念", "章末留下的未解决事项与牵引方式"),
    ("职业 / 家庭事件的作用", "职业或家庭事件对人物与关系的作用"),
]


def _sections(keys):
    return [{"key": k, "label": S[k][0], "question": S[k][1]} for k in keys]


EVIDENCE_REQ = ("每条重要结论必须引用本章段落编号作为依据；不得仅凭书名、目录、简介或对作品的既有印象补写结论；"
                "推测性内容必须标为推断；证据不足时如实标注。")

BUILTIN = [
    {
        "name": "开篇研究",
        "scene": "研究一本书怎样开局：故事承诺、人物目标、首次冲突、章尾悬念。默认分析前三章。",
        "default_scope": "first3",
        "sections": _sections(["promise", "function", "characters", "conflict", "emotion", "chapter_end", "method"]),
        "evidence_req": EVIDENCE_REQ,
        "compare_dims": [d[0] for d in COMPARE_DIMS],
        "extra_prompt": "",
    },
    {
        "name": "逐章结构",
        "scene": "逐章梳理状态变化、关系进展、信息与冲突，适合指定章节或全书。",
        "default_scope": "chapters",
        "sections": _sections(["function", "state_change", "characters", "conflict", "emotion", "chapter_end", "method"]),
        "evidence_req": EVIDENCE_REQ,
        "compare_dims": [d[0] for d in COMPARE_DIMS],
        "extra_prompt": "",
    },
    {
        "name": "婚恋专项",
        "scene": "在基础维度上增加关系阶段、双方主动性、职业事件作用、家庭矛盾与女主选择，适合现代言情。",
        "default_scope": "first3",
        "sections": _sections(["function", "state_change", "characters", "conflict", "emotion", "chapter_end",
                               "rel_stage", "initiative", "career", "family", "choice", "method"]),
        "evidence_req": EVIDENCE_REQ,
        "compare_dims": [d[0] for d in COMPARE_DIMS],
        "extra_prompt": "",
    },
]


def ensure_builtin():
    if db.q1("SELECT id FROM templates WHERE builtin=1"):
        return
    for t in BUILTIN:
        create(t, builtin=True)


def create(data: dict, builtin=False, source=None) -> int:
    with db.tx():
        tid = db.x("INSERT INTO templates(name,builtin,source_template,created) VALUES(?,?,?,?)",
                   (data["name"], int(builtin), source, db.now()))
        vid = db.x("INSERT INTO template_versions(template_id,version,data,created) VALUES(?,?,?,?)",
                   (tid, 1, db.jdump(data), db.now()))
        db.x("UPDATE templates SET latest=? WHERE id=?", (vid, tid))
    return tid


def get_version(tvid: int) -> dict:
    r = db.q1("SELECT tv.*, t.name tname, t.builtin FROM template_versions tv JOIN templates t ON t.id=tv.template_id"
              " WHERE tv.id=?", (tvid,))
    if not r:
        return None
    d = db.jload(r["data"], {})
    d.update({"version_id": r["id"], "version": r["version"], "template_id": r["template_id"], "builtin": r["builtin"]})
    return d


def list_all() -> list[dict]:
    out = []
    for t in db.q("SELECT * FROM templates WHERE deleted=0 ORDER BY builtin DESC, id"):
        v = get_version(t["latest"])
        uses = db.q1("SELECT COUNT(*) c FROM jobs j JOIN template_versions tv ON tv.id=j.template_version_id"
                     " WHERE tv.template_id=? AND j.kind='analysis'", (t["id"],))["c"]
        out.append({"id": t["id"], "builtin": t["builtin"], "latest": t["latest"], "uses": uses, **v})
    return out


def validate(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("模板名称不能为空")
    secs = []
    seen = set()
    for i, s in enumerate(data.get("sections") or []):
        label = (s.get("label") or "").strip()
        question = (s.get("question") or "").strip()
        if not label or not question:
            continue
        key = (s.get("key") or "").strip() or f"s{i + 1}"
        key = "".join(ch for ch in key if ch.isalnum() or ch == "_")[:30] or f"s{i + 1}"
        while key in seen:
            key += "_"
        seen.add(key)
        secs.append({"key": key, "label": label[:30], "question": question[:400]})
    if not secs:
        raise ValueError("至少需要一个分析维度")
    return {
        "name": name[:40], "scene": (data.get("scene") or "").strip()[:300],
        "default_scope": data.get("default_scope") if data.get("default_scope") in ("first3", "chapters", "all") else "first3",
        "sections": secs, "evidence_req": (data.get("evidence_req") or EVIDENCE_REQ).strip()[:600],
        "compare_dims": [d.strip()[:30] for d in (data.get("compare_dims") or [x[0] for x in COMPARE_DIMS]) if d.strip()][:12],
        "extra_prompt": (data.get("extra_prompt") or "").strip()[:3000],
    }


def save_new_version(tid: int, data: dict) -> int:
    t = db.q1("SELECT * FROM templates WHERE id=?", (tid,))
    if not t or t["deleted"]:
        raise ValueError("模板不存在")
    if t["builtin"]:
        raise ValueError("内置模板只读，请先复制为个人模板")
    data = validate(data)
    ver = db.q1("SELECT MAX(version) m FROM template_versions WHERE template_id=?", (tid,))["m"] + 1
    vid = db.x("INSERT INTO template_versions(template_id,version,data,created) VALUES(?,?,?,?)",
               (tid, ver, db.jdump(data), db.now()))
    db.x("UPDATE templates SET latest=?, name=? WHERE id=?", (vid, data["name"], tid))
    return vid


def copy(tid: int) -> int:
    t = db.q1("SELECT * FROM templates WHERE id=?", (tid,))
    d = get_version(t["latest"])
    data = {k: d[k] for k in ("name", "scene", "default_scope", "sections", "evidence_req", "compare_dims", "extra_prompt") if k in d}
    data["name"] = d["name"] + "（副本）"
    return create(data, builtin=False, source=tid)
