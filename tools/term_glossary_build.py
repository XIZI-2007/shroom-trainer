# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""从游戏串表抽「术语词表 + 意图一句话释义」，产出 src/term_glossary.json。

为什么要它：意图词典里的文案模板长这样 ——
    "Apply [PNUM] [LINKKEY=STATUS_BRAINLESS][JOINER=WORD_TO][MULTIPLICITY_ENEMY]"
其中 STATUS_* / WORD_* / MULTIPLICITY_* / PROPERTY_* 是**引用键**，不是给人看的文本。
面板要「理解 + 说人话」，就得把这些键换成中文。

实测发现（省得后人重踩）：
  · 真实键前缀是 `MULT_ENEMY_*`（不是模板里写的 `MULTIPLICITY_*`）、
    状态叫 `PROPERTY_*`（模板里写 `STATUS_*`）—— **模板引用名与实际表键名不一致**，
    且 `STATUS_*` / `MULTIPLICITY_*` 在串表里**根本没有条目**（游戏自己也渲染不出中文）。
    ⇒ 靠 `ALIAS` 副表按前缀改写去捡回大部分；捡不回的就保留英文原名（不影响"理解效果"）。
  · `TOOLTIP_INTENT_*` 是**一句话释义**（"Bad for living" → "活物不宜"），
    比长模板更适合给面板做"这牌是干嘛的"摘要 ⇒ 单独抽成 `tips`。

数据来源（只读）：
  localization-string-tables-{english,chinese(simplified)} → Card_en / Card_zh-Hans
  localization-assets-shared → Card Shared Data（key 顺序 + 分节注释）

用法：python tools/term_glossary_build.py   （游戏更新后重跑并重新打包）
"""
import glob
import json
import os
import re
import sys

try:
    import UnityPy
except ImportError:
    raise SystemExit("缺 UnityPy：pip install UnityPy")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "src", "term_glossary.json")

from loc_build import find_aa_dir, table_values          # noqa: E402
from intent_build import shared_sections                 # noqa: E402

WANT_SECTIONS = {"common words", "property names", "class names", "trigger names"}

# 模板引用名 → 实际表键名的候选改写（按顺序试，第一个命中就用）
ALIAS = [
    # 状态：模板写 STATUS_X，实际在 Property names 里叫 PROPERTY_X
    (re.compile(r"^STATUS_(.+)$"), ["PROPERTY_{0}", "WORD_{0}"]),
    # 目标数：模板写 MULTIPLICITY_ENEMY，实际叫 MULT_ENEMY_SINGLE / _ALL / _RANDOM…
    (re.compile(r"^MULTIPLICITY_ENEMY$"), ["MULT_ENEMY_SINGLE"]),
    (re.compile(r"^MULTIPLICITY_CARD$"), ["MULT_CARD_SINGLE"]),
    (re.compile(r"^MULTIPLICITY_(.+)$"), ["MULT_{0}_SINGLE", "MULT_{0}"]),
    # 连接词 / 数量词：模板写 WORD_/JNUM_，实际在 Common words
    (re.compile(r"^JNUM_(.+)$"), ["WORD_QUANTIFIER{0}", "JNUM_{0}", "WORD_{0}"]),
]


def resolve(key, terms):
    """把模板里的引用键解析成词表条目；解析不到返回 None。"""
    if key in terms:
        return terms[key]
    for rx, cands in ALIAS:
        m = rx.match(key)
        if not m:
            continue
        for c in cands:
            k2 = c.format(*m.groups()) if "{0}" in c else c
            if k2 in terms:
                return terms[k2]
    return None


def norm_key(s):
    """归一化：去下划线 + 转小写，用于把 TOOLTIP_INTENT_APPLY_POISONED(大写)
    对到词典的 ApplyPoisoned(驼峰)。⚠️ 只用于比对，不用于输出。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def friendly(key):
    """串表里查不到的引用键 → 至少给个人能读的名字。
    `STATUS_BRAINLESS` → `Brainless`；`MULTIPLICITY_ENEMY` → `Enemy`。"""
    for p in ("STATUS_", "MULTIPLICITY_", "PROPERTY_", "WORD_", "MULT_"):
        if key.startswith(p):
            key = key[len(p):]
            break
    parts = [w for w in key.split("_") if w]
    return " ".join(w.capitalize() for w in parts) or key


def main():
    aa = find_aa_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    en_b = glob.glob(os.path.join(aa, "localization-string-tables-english*"))[0]
    zh_b = glob.glob(os.path.join(aa, "localization-string-tables-chinese*"))[0]
    sh_b = glob.glob(os.path.join(aa, "localization-assets-shared*"))[0]

    en = table_values(en_b, "Card_en")
    zh = table_values(zh_b, "Card_zh-Hans")
    sections = shared_sections(sh_b)

    terms, tips, got_sec = {}, {}, []
    for sec in sections:
        name = sec["name"].strip().lower()
        # ⚠️ 跳过所有 ToolTips 节（下面单独处理意图释义）
        if name.startswith("tooltips"):
            if name == "tooltips intents":
                for mid, key in sec["items"]:
                    e, z = en.get(mid, ""), zh.get(mid, "")
                    if not (e or z):
                        continue
                    op = key[len("TOOLTIP_INTENT_"):] if key.startswith("TOOLTIP_INTENT_") else key
                    tips[op] = {"en": e, "zh": z}
            continue
        if name not in WANT_SECTIONS:
            continue
        got_sec.append(sec["name"])
        for mid, key in sec["items"]:
            e, z = en.get(mid, ""), zh.get(mid, "")
            if e or z:
                terms[key] = {"en": e, "zh": z}

    # MULT_ENEMY_* 等散在别处，按前缀补扫一遍全表
    for sec in sections:
        for mid, key in sec["items"]:
            if (key.startswith("MULT_") or key.startswith("MULTIPLY")) and key not in terms:
                e, z = en.get(mid, ""), zh.get(mid, "")
                if e or z:
                    terms[key] = {"en": e, "zh": z}

    if not terms:
        raise SystemExit("一条术语都没抽到，串表结构可能变了")

    # ⚠️ 释义键是大写 `APPLY_POISONED`，词典是驼峰 `ApplyPoisoned` ⇒ 必须归一后比对
    tips_by_norm = {norm_key(k): v for k, v in tips.items()}

    doc = {
        "_comment": "术语词表 + 意图一句话释义。模板里 [LINKKEY=…]/[JOINER=…]/[MULTIPLICITY_…] "
                    "的中英对照，及 TOOLTIP_INTENT_* 的短释义。由 tools/term_glossary_build.py 生成；"
                    "游戏更新后重跑并重新打包。",
        "source": os.path.basename(aa),
        "sections": got_sec,
        "count": len(terms),
        "tipCount": len(tips),
        "terms": terms,
        "tips": tips,
        # 归一化索引：词典 op(驼峰) → 释义。省得 Java/JS/Python 三边各写一遍转换
        "tipsByNorm": tips_by_norm,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    # 覆盖率：词典里引用到的键，有多少能翻出来
    dic = json.load(open(os.path.join(ROOT, "src", "card_intent_dict.json"), encoding="utf-8"))
    need = set()
    for e in dic["intents"]:
        blob = (e.get("en") or "") + (e.get("zh") or "")
        for m in re.finditer(r"\[(?:LINKKEY|JOINER|JNUM)=([A-Za-z0-9_]+)\]", blob):
            need.add(m.group(1))
        for t in re.findall(r"\[MULTIPLICITY_([A-Z]+)\]", blob):
            need.add("MULTIPLICITY_" + t)
    miss = sorted(k for k in need if resolve(k, terms) is None)
    hit = len(need) - len(miss)

    tip_hit = sum(1 for e in dic["intents"] if norm_key(e["op"]) in tips_by_norm)
    print("分节：%s" % " / ".join(got_sec))
    print("术语 %d 条 + 意图释义 %d 条，%d 字节" % (len(terms), len(tips), os.path.getsize(OUT)))
    print("词典引用覆盖：%d/%d（其余保留英文原名）" % (hit, len(need)))
    print("意图释义覆盖：%d/%d" % (tip_hit, len(dic["intents"])))
    if miss:
        print("未命中：%s" % ", ".join("%s→%s" % (k, friendly(k)) for k in miss))


if __name__ == "__main__":
    main()
