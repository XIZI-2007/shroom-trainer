# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""把 66 个状态压成**推演引擎能算的模型**（src/status_model.json）。

为什么单开一份：`status_tips.json` 是**官方文案**（原样抽取，权威但不可计算），
本文件是**人工判读**（把文案翻译成"对战斗数值有什么影响"）。两者必须分开，
免得后人以为模型也是抽出来的 —— 它是**读文案读出来的**，会过时，要有审计痕迹。

⚠️ 判读原则（三条红线）：
  1. **只给能算的**。文案没写清数值的（Armoured「降低受到的伤害」没说多少、
     Regrowth「会再生生命值」没说多少）一律 `kind="unknown"`，
     **不猜** —— 猜了会让推演输出"看着精确的错答案"。
  2. **不照搬别的游戏**。本游戏没有"易伤固定 +50%"这回事：VULNERABLE 的层数
     **本身就是百分比**（文案：会额外受到 [P2NUM]% 伤害）。
  3. 每条都要能指回 `status_tips.json` 里的官方文案（本表只写 key，正文从 tips 取）。

kind 取值（推演引擎据此算）：
  taken_pct     受击伤害额外 +层数%              Vulnerable
  taken_mult    受击伤害乘固定系数                Airborne(0.5)
  taken_reduce  受击减伤（幅度未知）              Armoured
  counter       被近战打中时反击 层数 点          Thorns
  enemy_bonus   敌人造成 +层数 额外伤害           Enraged / Mutant
  enemy_penalty 敌人攻击伤害 -层数                Confused
  player_punish 玩家每打一张牌，自己受 层数 伤    SpellWeakness
  on_death_aoe  死亡时对全体敌人造成 层数 伤      Infested
  kill_bonus    击杀牌伤害 +层数                  Blessed
  decay_amp     受伤时额外受伤（幅度未知）        Decay
  ignore_effects 忽视下 层数 次效果               Spellshield
  skip_turn     跳过行动（本回合不还手）          Stunned
  regen         回合开始再生（幅度未知）          Mossy / Regrowth / Slimey
  flavor        风味/死亡掉落类，不影响当回合计算 其余绝大多数

用法：
    python tools/status_model_build.py            # → src/status_model.json
    python tools/status_model_build.py --dump     # 额外打印分组
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

# 状态名 → (kind, param)
#   param 含义随 kind 变：taken_mult → 系数；其余 → True/None（层数从运行时读）
# ⚠️ 这张表是**人工判读结果**，改之前先回去读 src/status_tips.json 的对应文案。
MODEL = {
    # ---- 直接改"打到这个敌人身上的伤害" ----
    # ⚠️ Vulnerable 的 param = **每层**的额外受伤百分比。
    #    用户实测口径（2026-10-06）：「一层易伤意味着增加 50% 的伤害」⇒ 3 层 = +150%。
    #    （早先按"层数即百分比"写成 1%/层，导致推演把易伤当垃圾、排序明显不合理。）
    #    ⚠️ 若实际是"固定 +50%、层数只是持续回合"，把这里改成 None 并在
    #       `battle_planner.damage_multiplier` 里改成不乘层数即可（一处常量）。
    "Vulnerable":  ("taken_pct",      50.0),
    "Airborne":    ("taken_mult",     0.5),    # 承受伤害降低 50%
    "Armoured":    ("taken_reduce",   None),   # 降低受到的伤害（幅度文案未给 ⇒ 不计入）
    "Decay":       ("decay_amp",      None),   # 受到伤害时额外受伤（幅度未给）
    "Spellshield": ("ignore_effects", None),   # 忽视下 N 次效果（抵扣，不是加减伤）

    # ---- 反伤 / 联动 ----
    "Thorns":      ("counter",        None),   # 非致命时反击 N（打回玩家）
    "Conductive":  ("chain",          0.5),    # ⚠️ 受伤时对**相邻敌人**造成半数伤害
                                               #    —— 不是反伤玩家，是敌人之间联动。
                                               #    推演只打首要目标、不建邻接关系 ⇒ 不建模，
                                               #    但也不能错标成 counter（会把伤害记到玩家头上）。

    # ---- 敌人攻击力（对本回合外的下一回合有影响）----
    "Enraged":     ("enemy_bonus",    None),   # 造成 N 点额外伤害
    "Mutant":      ("enemy_bonus",    None),   # 同上，受伤则翻倍
    "Inspired":    ("enemy_mult",     2.0),    # 造成**双倍**伤害 ⇒ 是系数不是点数
    "Confused":    ("enemy_penalty",  None),   # 攻击伤害降低 N
    "Stunned":     ("skip_turn",      None),   # 跳过其行动

    # ---- 玩家侧 ----
    "SpellWeakness": ("player_punish", None),  # 打一张牌，自己受 N 伤
    "Blessed":       ("kill_bonus",    None),  # 击杀牌伤害 +N

    # ---- 死亡收益（斩杀后才兑现，推演里单列）----
    "Infested":    ("on_death_aoe",   None),   # 死去时对所有敌人造成 N 伤

    # ---- 回合结束结算（**不在本回合出牌期间**，推演不当作即期收益）----
    # ⚠️ 官方文案：「回合结束时失去 [PNUM] 点生命值。」
    #    ⇒ 出牌顺序推演里**不能**把它当"立即扣血"（早先的实现就是这么错的，
    #      会把中毒算成即时伤害、虚高收益）。这里归 end_turn_dot，不计入。
    "Poisoned":    ("end_turn_dot",   None),
    "Leech":       ("on_death",       None),
    "Sticky":      ("on_death",       None),
    "Melting":     ("on_death",       None),
    "Seasoned":    ("on_death",       None),
    "Charged":     ("on_death",       None),
    "HotSauce":    ("on_death",       None),
    "Studied":     ("on_death",       None),
    "Vibrating":   ("on_death",       None),
    "NanoLaced":   ("on_death",       None),
    "Gutsy":       ("on_death",       None),
    "Sludgy":      ("on_death",       None),
    "Sporey":      ("on_death",       None),
    "Evolving":    ("on_death",       None),
    "Fruiting":    ("on_death",       None),

    # ---- 再生（幅度未给 ⇒ 只标记，不计入）----
    "Mossy":       ("regen",          None),
    "Regrowth":    ("regen",          None),
    "Slimey":      ("regen",          None),

    # ---- 回合结束/离开时 ----
    "Insight":     ("on_leave",       None),   # 离开时抽 N 张
    "Resurgence":  ("on_leave",       None),   # 离开时给 N 点精力
    "StrangeExit": ("on_leave",       None),   # 离开时造成 N 困惑
}

# kind 的中文说明（给 UI 用）
KIND_LABEL = {
    "taken_pct": "每层 +{p}% 受伤（当前 {n} 层）",
    "taken_mult": "受击伤害 ×{p}",
    "taken_reduce": "受击减伤（幅度未知）",
    "decay_amp": "受伤时额外受伤（幅度未知）",
    "ignore_effects": "忽视下 {n} 次效果",
    "counter": "非致命受击时反击 {n}",
    "chain": "受伤时对相邻敌人造成 {p} 伤害",
    "enemy_bonus": "敌人造成 +{n} 伤害",
    "enemy_mult": "敌人造成 ×{p} 伤害",
    "enemy_penalty": "敌人攻击 −{n}",
    "skip_turn": "跳过其行动",
    "player_punish": "每打一张牌自己受 {n} 伤",
    "kill_bonus": "击杀牌伤害 +{n}",
    "on_death_aoe": "死亡时对全体造成 {n} 伤",
    "on_death": "死亡时掉落/生成",
    "end_turn_dot": "回合结束失去 {n} 点生命（不在本回合内结算）",
    "regen": "回合开始再生（幅度未知）",
    "on_leave": "离开时结算",
    "flavor": "风味/被动，不影响当回合数值",
    "unknown": "文案未给数值，推演不计入",
}

# 「能算出数字」的 kind —— 推演引擎真正会用的
COMPUTABLE = {"taken_pct", "taken_mult", "counter", "enemy_bonus", "enemy_mult",
              "enemy_penalty", "player_punish", "on_death_aoe", "kill_bonus"}

# ⚠️ 判读**未经真机验证**的条目：算法是这样，但没在游戏里对过数。
#    列出来是为了让后人知道"这条不是实测结论"，真机标定后从这里删掉。
UNVERIFIED = {
    "Inspired":   "『双倍伤害』按 ×2 处理，未实测",
    "Mutant":     "『受到伤害则翻倍』无法在推演内建模（本回合内不可知），只按基础值",
}


def main():
    dump = "--dump" in sys.argv
    tips = json.load(open(os.path.join(SRC, "status_tips.json"), encoding="utf-8"))["tips"]
    enum = json.load(open(os.path.join(SRC, "status_effect_enum.json"), encoding="utf-8"))
    i18n = json.load(open(os.path.join(SRC, "status_i18n.json"), encoding="utf-8"))["by_name"]

    names = set(enum["members"]) | set(tips.keys())
    items, unknown = {}, []
    for n in sorted(names):
        kind, param = MODEL.get(n, ("flavor", None))
        rec = {
            "kind": kind,
            "param": param,
            "label": KIND_LABEL.get(kind, kind),
            "zh": (i18n.get(n) or {}).get("zh") or n,
            "tipKey": (tips.get(n) or {}).get("key", ""),
            "tip": (tips.get(n) or {}).get("zh", ""),
            "computable": kind in COMPUTABLE,
            "unverified": UNVERIFIED.get(n, ""),
        }
        items[n] = rec
        if kind == "flavor":
            unknown.append(n)

    by_kind = {}
    for n, r in items.items():
        by_kind.setdefault(r["kind"], []).append(n)

    print("状态模型：%d 条（有可算效果 %d，其余为风味/未给数值）"
          % (len(items), sum(1 for r in items.values() if r["computable"])))
    for k in sorted(by_kind):
        print("  %-16s %d  %s" % (k, len(by_kind[k]), ",".join(sorted(by_kind[k])[:6])
                                  + ("…" if len(by_kind[k]) > 6 else "")))
    if dump:
        print("\n== 全部可算状态 ==")
        for n in sorted(items):
            if items[n]["computable"]:
                print("  %-16s %-14s %s" % (n, items[n]["kind"], items[n]["tip"]))

    out = {
        "_comment": ("状态 → 推演模型。**人工判读**（非抽取）：把 src/status_tips.json 的"
                     "官方文案翻译成可计算的 kind/param。文案没给数值的一律 kind=unknown/"
                     "flavor，推演不计入 —— 不猜。改这张表前先读对应 tooltip。"),
        "count": len(items),
        "computableCount": sum(1 for r in items.values() if r["computable"]),
        "kindLabel": KIND_LABEL,
        "computableKinds": sorted(COMPUTABLE),
        "items": items,
        "flavorOnly": sorted(unknown),
        "unverified": UNVERIFIED,
    }
    dst = os.path.join(SRC, "status_model.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("写出 %s (%d 字节)" % (os.path.relpath(dst, ROOT), os.path.getsize(dst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
