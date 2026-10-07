# -*- coding: utf-8 -*-
"""合并「权威枚举成员表」+「串表描述模板」→ 统一意图词典。

数据来源三份，各自的权威范围不同：

  1. `global-metadata.dat` 的 `get_XXX` 序列
     → `CardIntent` 的**成员全集与顺序**（171 个）。这是"有哪些效果"。
  2. 串表 `Card Shared Data` 的 `// Intents` 段（235 条）
     → 每个意图的**中英文描述模板 + 占位符结构**。这是"效果长什么样"。
  3. 串表 `Card_en` 的公共词条
     → 术语表（`[LINKKEY=WORD_X]` 的展开、目标数措辞）。

意图名 ↔ 模板名不是字符串相等（模板 key 是 `INTENT_XXX`，枚举名是 `XXX`），
所以用「去除非字母数字 + 小写」归一后匹配。

用法：
    python tools/intent_dict_build.py
输出：src/card_intent_dict.json
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SRC = os.path.join(ROOT, "src")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# 少数成员在 `get_XXX` 序列里是**旧名**（改名后遗留的属性访问器），
# 而串表模板用的是新名。这里做一次显式别名，否则会白白丢掉描述。
ALIASES = {
    "makevulnerable": "applyvulnerable",   # get_MakeVulnerable → ApplyVulnerable
    "increaseuses": "increaseuses",
    # ⚠️ 伤害意图在串表里**按正负号分两条**：INTENT_DEAL_DAMAGE_POS（造成伤害）与
    #    _NEG（治疗等量）。运行时枚举成员只有一个 `DealDamage`。
    #    ⇒ 必须**显式**指向 POS；靠"去后缀猜一个"会撞上 NEG（把伤害渲染成治疗，踩过）。
    "dealdamage": "dealdamagepos",
}


# ---- 语义分类：给每个意图打一个"作用域"，供面板做战斗推演 -------------------
# 分类只影响面板怎么"理解"，不影响游戏。多标签用列表。
def classify(name, tmpl):
    low = norm(name)
    tags = []
    # 目标域
    if low.startswith("apply") or low in ("damageself", "healself", "gainenergy",
                                          "gainmaxenergy", "increasemaxhealth",
                                          "warmplayer", "bleed", "taunt",
                                          "gainareroll", "getstoked"):
        tags.append("self_or_status")
    if low.startswith(("draw", "makecard", "addcard", "create", "makea", "makeor",
                       "bunchofkeys", "stackofkeycards", "returnrandom", "returnlast")):
        tags.append("card_gen")
    if low in ("consumecard", "exhausttargetcard", "discardcard", "substitutecard",
               "swapcardout", "choosecardtoforget", "dischargecard", "levelupcard",
               "reducecost", "reducecostpermanently", "setcosttozero",
               "setcosttozeropermanently", "addpropertytocap", "addpropertytocard",
               "bindtocard", "becomelodged", "createlodgedcard",
               "createlodgedcardwithdecay", "increasempre", "increaseuses"):
        tags.append("card_modify")
    if low in ("increasegunk", "consumegunk", "collectallgunk", "movegunk",
               "storegunk", "usestoredgunk", "increasestoredgunk",
               "consumeallgunk", "collectallgunkandheal"):
        tags.append("gunk")
    if low in ("increasedamage", "multiplydamage", "resetdamage", "leachcost",
               "increasapplieddecay", "increaseapplieddecaymultiply",
               "increasespellweakness", "applyvulnerable", "applydecay",
               "applypoisoned", "applyleech", "applymarked", "cripple",
               "dealhalfhealthindamage", "eatenndamage", "reducestatuseffects"):
        tags.append("offense")
    if low in ("increasehealing", "increaseselfhealing", "increasemakecard",
               "resetthealing", "resethealing", "makecardfulldamage",
               "makecardhalfdamage"):
        tags.append("scaling")
    if low in ("eatfood", "eatcreature", "eatdata", "eatfoodandabsorbdamage",
               "eatfoodandstoreprotein", "consumecard", "consumegunk",
               "exhausttargetfood"):
        tags.append("eat")
    if low in ("rest", "meditate", "scavenge", "searchpockets", "digfortreasure",
               "digold", "dig", "trufflehunt", "openmap", "revealall", "reveal",
               "feed", "fix", "ignite", "break", "unlock", "undiscovered",
               "adopt", "endturn", "executeprotocol", "createcardencounter",
               "destroycampstructure", "resetcampstructure"):
        tags.append("explore")
    if low in ("triggerenemydeath", "trigger turret".replace(" ", ""),
               "triggerTurret".lower()):
        tags.append("summon")
    # 数值槽
    if tmpl is not None and tmpl.get("slots"):
        tags.append("has_number")
    if tmpl is not None and tmpl.get("targets"):
        tags.append("has_multiplicity")
    return tags


def main():
    enum_p = os.path.join(SRC, "card_intent_enum.json")
    tmpl_p = os.path.join(SRC, "card_intents.json")
    for p in (enum_p, tmpl_p):
        if not os.path.isfile(p):
            raise SystemExit("缺少输入：%s（先跑 tools/intent_meta.py / tools/intent_build.py）" % p)

    enum = json.load(open(enum_p, encoding="utf-8"))
    tmpl = json.load(open(tmpl_p, encoding="utf-8"))
    members = enum["members"]

    by_norm = {}
    for t in tmpl["intents"]:
        by_norm[norm(t["op"])] = t
    # 模板 key 形如 INTENT_DAMAGE_SELF，op 已剥掉前缀；再做一次保底匹配
    for t in tmpl["intents"]:
        k = norm(t["key"])
        if k not in by_norm:
            by_norm[k] = t

    # 模板侧有一批变体后缀（同一效果的两种措辞），而枚举侧只有基名。
    # 建一张「去掉变体后缀」的副表。
    # ⚠️ 必须在**归一之前**判后缀：norm() 会把下划线去掉，
    #    归一后再 endswith("_single") 永远为 False（踩过）。
    # ⚠️ **不要把 `_POS`/`_NEG` 放进来**：它们是"同效果的正/负表述"，
    #    猜哪个都会错（DealDamage 会撞上 _NEG 的治疗文案）。这类必须走 ALIASES 显式指定。
    VARIANT_SUFFIX = ("_SINGLE", "_MULTI")
    variant_map = {}
    for t in tmpl["intents"]:
        raw = (t["op"] or "").upper()
        for suf in VARIANT_SUFFIX:
            if raw.endswith(suf):
                variant_map.setdefault(norm(raw[: -len(suf)]), t)
                break

    out = []
    for name in members:
        t = by_norm.get(norm(name))
        if t is None and norm(name) in ALIASES:
            t = by_norm.get(ALIASES[norm(name)])
        if t is None:
            t = variant_map.get(norm(name))
        row = {
            "op": name,
            "tags": classify(name, t),
            "en": (t or {}).get("en", ""),
            "zh": (t or {}).get("zh", ""),
            "key": (t or {}).get("key", ""),
            "slots": (t or {}).get("slots", []),
            "refs": (t or {}).get("refs", []),
        }
        out.append(row)

    covered = sum(1 for r in out if r["en"])
    stats = {}
    for r in out:
        for g in r["tags"]:
            stats[g] = stats.get(g, 0) + 1

    payload = {
        "_comment": "CardIntent 统一词典：权威成员顺序(metadata) + 中英描述模板(string table)",
        "count": len(out),
        "covered": covered,
        "sources": {
            "enum": "tools/intent_meta.py ← global-metadata.dat",
            "text": "tools/intent_build.py ← Card Shared Data // Intents",
        },
        "tag_stats": stats,
        "intents": out,
    }
    dst = os.path.join(SRC, "card_intent_dict.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("意图总数 %d，有描述 %d（%.0f%%）" % (len(out), covered, 100.0 * covered / len(out)))
    print("标签统计：", ", ".join("%s=%d" % kv for kv in sorted(stats.items(), key=lambda x: -x[1])))
    print("写出", os.path.relpath(dst, ROOT), "(%d 字节)" % os.path.getsize(dst))


if __name__ == "__main__":
    main()
