# -*- coding: utf-8 -*-
"""建「卡牌意图 op → 规范状态名」的映射表（离线，零硬编码偏移）。

为什么需要它：
  推演引擎要算「打这张牌会给敌人/自己挂上什么状态」。agent.js 读出的是意图名
  （`ApplyVulnerable`），而状态层数表（`readEnemyStatuses` 的返回）用的是
  **StatusEffect 枚举名**（`Vulnerable`）。两者名字**不是**简单的前缀关系
  （`ApplyConfuse` → `Confused`、`ApplyInfested` → `Infested`、`ApplyMarked` → `Marked`），
  所以必须有一张对的映射，不能靠字符串拼。

数据来源（全部已有，不新抓游戏）：
  src/card_intent_dict.json     意图 → 中英文案模板（模板里带 [LINKKEY=STATUS_XXX] 引用）
  src/status_effect_enum.json   StatusEffect 权威成员（66 个，规范名）
  src/status_i18n.json          规范名 → 本地化键（STATUS_XXX）

算法：
  1. 在意图模板里正则抽 `[LINKKEY=(STATUS_xxx|WORD_xxx)]`（中英模板都扫）。
  2. 归一化（去非字母数字 + 大写）后与 `status_i18n` 的 key 比对，命中即取规范名。
  3. 归一化对不上的少数（游戏键名与枚举名有出入）走 `KEY_FIXUPS` 定向修正，
     **不做模糊匹配**（模糊会在 INFEST/INFESTED 这类上错配）。

用法：
    python tools/status_intent_map_build.py           # → src/status_intent_map.json
    python tools/status_intent_map_build.py --dump    # 额外打印全部映射

输出：src/status_intent_map.json
    { _comment, count, map: {"ApplyVulnerable": "Vulnerable", …},
      unmapped: ["CreateLodgedCardWithDecay", …],   # 指向"生成牌"这类非状态意图
      fixups: {…} }
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

# ⚠️ 游戏串表的键名与 StatusEffect 枚举名**有出入**的几条（不是笔误，是游戏自己不一致）。
#    只列经过人工核对的定向修正，**禁止**改成模糊/前缀匹配 —— 那会把
#    STATUS_INFEST(ed) 与 STATUS_INFESTED 之类撞在一起，进而把状态认错。
KEY_FIXUPS = {
    "STATUS_INFEST": "Infested",      # 模板用 INFEST，i18n 键是 STATUS_INFESTED
    "STATUS_MARK": "Marked",          # 模板用 MARK，i18n 键是 STATUS_MARKED
    # 下面两条**不在 StatusEffect 枚举里**，但本地化表 + tooltip 都有，是真实状态
    # （见 MEMORY.md「敌人状态」段：Vulnerable/Poisoned 属另一套键）。
    "STATUS_VULNERABLE": "Vulnerable",
    "STATUS_POISONED": "Poisoned",
}

# 归一化后要排除的引用键（不是状态）
NOT_STATUS = {"STATUS_COLOR", "STATUS_NONE", "STATUS_TYPE"}

_REF = re.compile(r"LINKKEY=(STATUS_[A-Za-z0-9_]+)")


def norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def load(name, default):
    p = os.path.join(SRC, name)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def main():
    dump = "--dump" in sys.argv

    d = load("card_intent_dict.json", {"intents": []})
    intents = d.get("intents") or []
    enum = load("status_effect_enum.json", {"members": []})
    members = enum.get("members") or []
    i18n = load("status_i18n.json", {"by_name": {}})
    by_name = i18n.get("by_name") or {}
    sem = (load("intent_semantics.json", {}) or {}).get("intents") or {}

    # 规范名集合 = 枚举成员 ∪ i18n 里出现过的名字（i18n 多出的那几只也要认）
    canonical = set(members) | set(by_name.keys())

    # 归一化键 → 规范名。⚠️ 索引的**键是本地化键**（`STATUS_CONFUSED`），
    # 不是状态名本身 —— 模板里引用的是 `[LINKKEY=STATUS_CONFUSED]`，
    # 拿 `norm("Confused")="CONFUSED"` 去查永远查不到（踩过：只命中 4 条）。
    norm2canon = {}
    for n in canonical:
        rec = by_name.get(n)
        if rec and rec.get("key"):
            norm2canon.setdefault(norm(rec["key"]), n)
        # 兜底：状态名本身也建一条（少数没有本地化键的）
        norm2canon.setdefault(norm(n), n)
    # Fixups 参与反查（它们指向的是规范名）
    fixup_norm = dict(KEY_FIXUPS)

    if not intents:
        raise SystemExit("card_intent_dict.json 缺 intents")

    mapping, unmapped, fixups_used = {}, [], {}
    side_of, metric_of = {}, {}
    for e in intents:
        op = e.get("op")
        if not op:
            continue
        blob = " ".join(str(e.get(k) or "") for k in ("zh", "en"))
        keys = sorted(set(_REF.findall(blob)))
        if not keys:
            continue        # 模板根本没引用状态（如纯伤害牌）
        names = []
        for k in keys:
            if norm(k) in NOT_STATUS:
                continue
            if k in fixup_norm:
                names.append(fixup_norm[k])
                fixups_used[k] = fixup_norm[k]
                continue
            hit = norm2canon.get(norm(k))
            if hit:
                names.append(hit)
        if not names:
            unmapped.append(op)
            continue
        # 一张牌可能引用多个状态（罕见）⇒ 取第一个，其余记进 unmapped 供核查
        mapping[op] = names[0]
        s = sem.get(op) or {}
        side_of[op] = s.get("target") or ""
        metric_of[op] = s.get("metric") or ""
        if len(names) > 1:
            unmapped.append("%s(多状态:%s)" % (op, ",".join(names)))

    # ⚠️ **只有 `metric == "status"` 的才是"挂状态"**。其余是：
    #    · metric="card"/cat="cardgen" → 生成/抽一张带某状态的牌，**不是**给敌人挂状态
    #      （`CreateLodgedCardWithDecay`/`DrawConfusionCard`）
    #    · metric="damage"（ApplyPoisoned）→ 语义层把中毒归成伤害，实际是挂层数；
    #      保留它（本游戏中毒确实是状态层数）
    #    target="self" → 挂在自己身上，推演里记 player 侧，不进敌人 statuses
    apply_map, self_map, note_only = {}, {}, {}
    for op, name in mapping.items():
        t, m = side_of.get(op, ""), metric_of.get(op, "")
        # ⚠️ 先判 metric=="card"：这一类是**生成/抽取一张带某状态的牌**
        #    （`CreateLodgedCardWithDecay` / `DrawConfusionCard`），
        #    跟"给谁挂状态"无关 —— 不能因为模板里引用了 STATUS_DECAY 就当施加。
        if m == "card" or "cardgen" in (sem.get(op) or {}).get("cat", ""):
            note_only[op] = name
        elif t == "self":
            self_map[op] = name            # 自身增益（Charged/Blessed/Thorns…）
        else:
            apply_map[op] = name           # 施加到敌人

    print("意图→状态 映射：%d 条（给敌人 %d / 给自己 %d / 仅备注 %d）"
          % (len(mapping), len(apply_map), len(self_map), len(note_only)))
    if fixups_used:
        print("定向修正生效：%s" % json.dumps(fixups_used, ensure_ascii=False))
    if unmapped:
        print("未映射（模板不引用状态）：%s" % unmapped)

    if dump:
        print("\n== 施加到敌人 ==")
        for k in sorted(apply_map):
            print("  %-24s %s" % (k, apply_map[k]))
        print("\n== 加到自己 ==")
        for k in sorted(self_map):
            print("  %-24s %s" % (k, self_map[k]))
        print("\n== 仅备注（生成带状态的牌）==")
        for k in sorted(note_only):
            print("  %-24s %s" % (k, note_only[k]))

    out = {
        "_comment": ("卡牌意图 op → StatusEffect 规范名。由 tools/status_intent_map_build.py "
                     "从 card_intent_dict.json 的 [LINKKEY=STATUS_*] 引用 + "
                     "status_effect_enum.json/status_i18n.json 反查生成。"
                     "推演引擎据此把「打牌」翻译成「状态层数增减」。"
                     "apply=施加到敌人；self=加到自己；note=生成带状态的牌（不当挂状态）。"),
        "count": len(mapping),
        "map": dict(sorted(mapping.items())),
        "apply": dict(sorted(apply_map.items())),
        "self": dict(sorted(self_map.items())),
        "note": dict(sorted(note_only.items())),
        "side": side_of,
        "metric": metric_of,
        "unmapped": sorted(unmapped),
        "fixups": fixups_used,
    }
    dst = os.path.join(SRC, "status_intent_map.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("写出 %s (%d 字节)" % (os.path.relpath(dst, ROOT), os.path.getsize(dst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
