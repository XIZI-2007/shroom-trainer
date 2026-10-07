# -*- coding: utf-8 -*-
"""从游戏串表抽「卡牌意图词典」，产出 src/card_intents.json。

为什么需要它：游戏把卡牌效果建模成 `(CardIntent 枚举, 数值)` 的列表（实测元数据里
有 `CardIntent` / `CardIntentPair` / `_intents` 等符号），而每个意图在串表里都有一条
带占位符的文案模板。把模板里的占位符解析出来，程序就能知道"这条意图是干什么的、
数值挂在哪个槽、影响几个目标"——**这是让面板理解卡牌效果的基础**，且完全离线可得。

数据来源（只读游戏文件，不修改任何东西）：
  StreamingAssets/aa/StandaloneWindows64/
    localization-string-tables-english(en)_assets_all.bundle   → Card_en
    localization-string-tables-chinese(simplified)(zh-hans)…   → Card_zh-Hans
    localization-assets-shared_assets_all.bundle               → Card Shared Data（key 顺序 + 分节注释）

串表结构（实测确认）：
  · SharedTableData.m_Entries 是**有序**的，形如 `// Intents` 的 key 就是**分节标记**
    （已见 12 节：Class names / Property names / Trigger names / Common words / Intents /
      Foresights / Evolve / ToolTips ×5）
  · 每组按 `m_Id` 与 StringTable.m_TableData 对齐；**Card_en 与 Card Shared Data 同一套 m_Id**
    （但 Card_en 与 Card Names_en **不是**同一套，别搞混）

占位符语法（从实测文案里归纳）：
  [PNUM]                  数值槽（渲染时才填）—— 卡的实际数值走内存，不在这里
  [LINKKEY=WORD_X]        术语引用（跳转到该术语的 tooltip）
  [JOINER=WORD_X]         连接词
  [MULTIPLICITY_ENEMY|CARD]  作用目标数量
  [MULTIPLY_MOD_1|2]      修正系数槽
  其它：EXTRA_DAMAGE / HEXCODE / CARD_NAME / PROPERTY_* 等

用法：
    python tools/intent_build.py              # 自动找游戏目录，写 src/card_intents.json
    python tools/intent_build.py <游戏目录>

游戏一更新就要重跑（文案/意图会变），跑完重新打包。
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
OUT = os.path.join(ROOT, "src", "card_intents.json")

from loc_build import find_aa_dir, table_values  # noqa: E402  复用现成的目录定位与读表

SLOT_RE = re.compile(r"\[([A-Z_0-9]+)(?:=([A-Za-z_0-9]+))?\]")
# ⚠️ 名字里**含数字**（`MULTIPLY_MOD_1` / `MULTIPLICITY_CARD_2`），字符类必须带 0-9，
#    否则这些占位符会被整条漏掉（踩过：DAMAGE_SELF 的 mods 明明是空的）。


def shared_sections(shared_bundle, table_name="Card Shared Data"):
    """按 key 顺序读出 (m_Id, m_Key)，并用 `// xxx` 注释行切分为若干节。"""
    env = UnityPy.load(shared_bundle)
    entries = None
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read()
        if getattr(d, "m_Name", "") == table_name and getattr(d, "m_Entries", None):
            entries = [(str(getattr(e, "m_Id", "")), str(getattr(e, "m_Key", "")))
                       for e in d.m_Entries]
            break
    if entries is None:
        raise SystemExit("读不到 %s，游戏版本可能变了" % table_name)

    sections, cur = [], None
    for mid, key in entries:
        if key.startswith("//"):
            cur = {"name": key.strip("/ ").strip(), "items": []}
            sections.append(cur)
            continue
        if cur is None:
            cur = {"name": "(开头)", "items": []}
            sections.append(cur)
        cur["items"].append((mid, key))
    return sections


def parse_slots(text):
    """把文案里的 [XXX] / [XXX=YYY] 拆成结构化信息。"""
    nums, refs, joiners, targets, mods, others = [], [], [], [], [], []
    for m in SLOT_RE.finditer(text or ""):
        name, arg = m.group(1), m.group(2)
        if name in ("PNUM", "EXTRA_DAMAGE", "EXTRA_HEAL") or name.endswith(("_NUM", "_DAMAGE", "_HEAL")):
            # EXTRA_DAMAGE / EXTRA_HEAL 也是"数值"（额外加成），一起归入数值槽
            nums.append(name)
        elif name == "LINKKEY":
            refs.append(arg or "")
        elif name == "JOINER":
            joiners.append(arg or "")
        elif name.startswith("MULTIPLICITY"):
            targets.append(name.split("_", 1)[1] if "_" in name else name)
        elif name.startswith("MULTIPLY"):
            mods.append(name)
        else:
            others.append(name)
    return nums, refs, joiners, targets, mods, others


def main():
    aa = find_aa_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print("游戏资源目录：%s" % aa)
    en_b = glob.glob(os.path.join(aa, "localization-string-tables-english*"))[0]
    zh_b = glob.glob(os.path.join(aa, "localization-string-tables-chinese*"))[0]
    sh_b = glob.glob(os.path.join(aa, "localization-assets-shared*"))[0]

    en = table_values(en_b, "Card_en")
    zh = table_values(zh_b, "Card_zh-Hans")
    print("Card_en %d 条 / Card_zh-Hans %d 条" % (len(en), len(zh)))

    sections = shared_sections(sh_b)
    print("分节 %d 个：%s" % (len(sections), " / ".join(s["name"] for s in sections)))

    intents = []
    for sec in sections:
        # 只取「Intents」节（"// ToolTips Intents" 是 tooltip 文案，不是意图定义）
        if sec["name"].strip().lower() != "intents":
            continue
        for mid, key in sec["items"]:
            e_txt, z_txt = en.get(mid, ""), zh.get(mid, "")
            nums, refs, joiners, targets, mods, others = parse_slots(e_txt)
            intents.append({
                "key": key,
                # 去掉 INTENT_ 前缀就是"操作名"，与 CardIntent 枚举成员大致同源
                "op": key[len("INTENT_"):] if key.startswith("INTENT_") else key,
                "en": e_txt,
                "zh": z_txt,
                "slots": nums,        # 数值槽（实际数值在内存里）
                "refs": refs,         # 术语引用
                "joiners": joiners,   # 连接词
                "targets": targets,   # 作用目标（ENEMY / CARD …）
                "mods": mods,         # 修正系数槽
                "others": others,     # 其它占位符
            })
    if not intents:
        raise SystemExit("Intents 节是空的，串表结构可能变了")

    data = {
        "_comment": "由 tools/intent_build.py 从游戏串表生成；游戏更新后重跑并重新打包。",
        "source": os.path.basename(aa),
        "sections": [s["name"] for s in sections],
        "count": len(intents),
        "intents": intents,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    with_num = sum(1 for i in intents if i["slots"])
    with_tgt = sum(1 for i in intents if i["targets"])
    print()
    print("写出 %s" % OUT)
    print("  意图 %d 条（其中 %d 条带数值槽、%d 条带目标数）" % (len(intents), with_num, with_tgt))
    print("  %d 字节" % os.path.getsize(OUT))


if __name__ == "__main__":
    main()
