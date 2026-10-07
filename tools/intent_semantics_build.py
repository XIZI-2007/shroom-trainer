# -*- coding: utf-8 -*-
"""由 card_intent_dict.json 生成语义层 src/intent_semantics.json。

「理解效果」= 把 (意图名, 数值, 目标数) 变成可计算的效力。词典只给**文本模板**，
本脚本补**语义分类**：
  cat     主类别（damage/heal/defense/debuff/buff/cardgen/cardmod/resource/eat/
                     explore/summon/pet/misc）
  metric  数值口径（damage/heal/shield/energy/draw/status/resource/card/none）
  target  作用对象（self/enemy/card/world/none）
  side    玩家视角得失（gain 得利 / harm 受损 / neutral）
  phase   生效场景（combat/explore/both）
  value   是否直接产生可累加的战斗数值（阶段 3 推演用）

游戏更新后：重跑本脚本（dict 变了就跟着变）。⚠️ 新增意图若未在 S 表里会报出来。
"""
import io
import json
import os

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
DICT = os.path.join(SRC, "card_intent_dict.json")
OUT = os.path.join(SRC, "intent_semantics.json")

# op -> (cat, metric, target, side, phase, value)
# value=True 表示"该意图带的数值可直接进最优打法的收益计算"
S = {
    "ApplyBrainless":            ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyInfested":             ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "Bleed":                     ("debuff",   "damage",   "enemy", "gain", "combat", 1),
    "ConfusedBrawl":             ("damage",   "damage",   "enemy", "gain", "combat", 1),
    "CreatePet":                 ("pet",      "none",     "self",  "gain", "combat", 0),
    "Drain":                     ("damage",   "damage",   "enemy", "gain", "combat", 1),
    "DrawConfusionCard":         ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawDataCard":              ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawMachineCard":           ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawRageCard":              ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawCardNextTurn":          ("cardgen",  "draw",     "self",  "gain", "combat", 1),
    "EncouragePet":              ("pet",      "none",     "self",  "gain", "combat", 0),
    "FetchPet":                  ("pet",      "none",     "self",  "gain", "combat", 0),
    "IncreaseConfusion":         ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "LayEggs":                   ("pet",      "none",     "self",  "gain", "combat", 0),
    "LevelUpCard":               ("cardmod",  "card",     "card",  "gain", "both",   0),
    "MakeOrScaleExistingCard":   ("cardgen",  "card",     "card",  "gain", "both",   0),
    "MakeVolatile":              ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "MoveTowardsFriend":         ("misc",     "none",     "self",  "neutral", "combat", 0),
    "MultiplyConfusion":         ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "RaiseGhosts":               ("summon",   "none",     "self",  "gain", "combat", 1),
    "ReduceCountdown":           ("buff",     "none",     "self",  "gain", "combat", 1),
    "ReturnRandomExhaustedFood": ("cardgen",  "card",     "self",  "gain", "combat", 0),
    "ShieldDONTUSE":             ("defense",  "shield",   "self",  "gain", "combat", 1),
    "Taunt":                     ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "GetStoked":                 ("buff",     "status",   "self",  "gain", "combat", 1),
    "Prepare":                   ("buff",     "none",     "self",  "gain", "combat", 1),
    "AddCardToTopOfDeck":        ("cardgen",  "card",     "self",  "gain", "both",   0),
    "AddPropertyToCard":         ("cardmod",  "card",     "card",  "gain", "both",   0),
    "DischargeAllBattery":       ("resource", "energy",   "self",  "gain", "combat", 1),
    "IncreaseBattery":           ("resource", "energy",   "self",  "gain", "combat", 1),
    "UseBattery":                ("resource", "energy",   "self",  "gain", "combat", 1),
    "ConsumeCard":               ("eat",      "resource", "card",  "gain", "both",   0),
    "CreateGloom":               ("cardgen",  "card",     "self",  "gain", "both",   0),
    "DrawConsumeGunkCard":       ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawCreatureCard":          ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawDamageCard":            ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawFoodCard":              ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawGearCard":              ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "DrawStoreGunkCard":         ("cardgen",  "card",     "self",  "gain", "combat", 1),
    "EatCreature":               ("eat",      "heal",     "self",  "gain", "combat", 1),
    "EatData":                   ("eat",      "heal",     "self",  "gain", "combat", 1),
    "EatFood":                   ("eat",      "heal",     "self",  "gain", "combat", 1),
    "EatFoodAndAbsorbDamage":    ("eat",      "heal",     "self",  "gain", "combat", 1),
    "EatFoodAndStoreProtein":    ("eat",      "resource", "self",  "gain", "combat", 1),
    "ExhaustTargetCard":         ("cardmod",  "card",     "card",  "gain", "combat", 0),
    "ExhaustTargetFood":         ("eat",      "resource", "card",  "gain", "both",   0),
    "GainAReroll":               ("resource", "resource", "self",  "gain", "both",   1),
    "IncreaseRage":              ("buff",     "status",   "self",  "gain", "combat", 1),
    "MakeAPermanentCopy":        ("cardgen",  "card",     "card",  "gain", "both",   0),
    "MakeATemporaryCopy":        ("cardgen",  "card",     "card",  "gain", "both",   0),
    "MakeATemporaryCopyOfCreature": ("cardgen", "card",   "card",  "gain", "combat", 0),
    "MakeATemporaryOfTriggerSource": ("cardgen", "card",  "card",  "gain", "combat", 0),
    "MakeCard":                  ("cardgen",  "card",     "card",  "gain", "both",   0),
    "MakeCardDamageEqualToProtein": ("cardgen", "damage", "card",  "gain", "combat", 0),
    "MakeCardFullDamage":        ("cardgen",  "damage",   "card",  "gain", "combat", 0),
    "MakeCardHalfDamage":        ("cardgen",  "damage",   "card",  "gain", "combat", 0),
    "MakeCardNextCombat":        ("cardgen",  "card",     "card",  "gain", "combat", 0),
    "MakeCardNextExplore":       ("cardgen",  "card",     "card",  "gain", "explore", 0),
    "MakeCardWithDamage":        ("cardgen",  "damage",   "card",  "gain", "combat", 0),
    "MakeCardWithDecay":         ("cardgen",  "card",     "card",  "gain", "combat", 0),
    "MakeCardWithTargetDecay":   ("cardgen",  "card",     "card",  "gain", "combat", 0),
    "MakeSacrifice":             ("misc",     "none",     "self",  "neutral", "combat", 0),
    "AddMakeCard":               ("cardgen",  "card",     "card",  "gain", "both",   0),
    "Consume":                   ("eat",      "resource", "card",  "gain", "both",   0),
    "IncreaseAppliedDecay":      ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "IncreaseAppliedDecayMultiply": ("debuff", "status",  "enemy", "gain", "combat", 1),
    "IncreaseDamage":            ("buff",     "status",   "self",  "gain", "combat", 1),
    "IncreaseHealing":           ("buff",     "status",     "self",  "gain", "combat", 1),
    "IncreaseMakeCard":          ("buff",     "card",     "self",  "gain", "both",   0),
    "IncreaseSelfHealing":       ("buff",     "status",     "self",  "gain", "both",   1),
    "IncreaseSpellWeakness":     ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "IncreaseUses":              ("cardmod",  "card",     "card",  "gain", "both",   0),
    "LeachCost":                 ("resource", "energy",   "enemy", "gain", "combat", 1),
    "ReduceCost":                ("cardmod",  "card",     "card",  "gain", "both",   0),
    "ReduceCostPermanently":     ("cardmod",  "card",     "card",  "gain", "both",   0),
    "ResetDamage":               ("buff",     "status",   "self",  "gain", "combat", 1),
    "ResetHealing":              ("buff",     "status",     "self",  "gain", "combat", 1),
    "SetCostToZero":             ("cardmod",  "card",     "card",  "gain", "both",   0),
    "SetCostToZeroPermanently":  ("cardmod",  "card",     "card",  "gain", "both",   0),
    "MultiplyDamage":            ("buff",     "status",   "self",  "gain", "combat", 1),
    "PacifyRage":                ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ReturnLastExhaustedCard":   ("cardgen",  "card",     "self",  "gain", "both",   0),
    "ReturnRandomExhaustedCard": ("cardgen",  "card",     "self",  "gain", "both",   0),
    "ReturnRandomExhaustedCreature": ("cardgen", "card",  "self",  "gain", "combat", 0),
    "SubstituteCard":            ("cardmod",  "card",     "card",  "gain", "both",   0),
    "SubstituteCardWithProperty": ("cardmod", "card",     "card",  "gain", "both",   0),
    "SwapCardOut":               ("cardmod",  "card",     "card",  "gain", "both",   0),
    "TriggerEnemyDeath":         ("summon",   "none",     "self",  "gain", "combat", 1),
    "TriggerTurret":             ("summon",   "damage",   "enemy", "gain", "combat", 1),
    "IncreaseWarmth":            ("buff",     "status",   "self",  "gain", "both",   1),
    "CollectAllGunk":            ("resource", "resource", "self",  "gain", "combat", 1),
    "CollectAllGunkAndHeal":     ("resource", "heal",     "self",  "gain", "combat", 1),
    "ConsumeAllGunk":            ("resource", "resource", "self",  "gain", "combat", 1),
    "ConsumeGunk":               ("eat",      "heal",     "self",  "gain", "combat", 1),
    "IncreaseGunk":              ("resource", "resource", "self",  "gain", "both",   1),
    "MoveGunk":                  ("resource", "resource", "self",  "gain", "combat", 1),
    "IncreaseStoredGunk":        ("resource", "resource", "self",  "gain", "both",   1),
    "StoreGunk":                 ("resource", "resource", "self",  "gain", "both",   1),
    "UseStoredGunk":             ("resource", "resource", "self",  "gain", "combat", 1),
    "EndTurn":                   ("explore",  "none",     "world", "neutral", "combat", 0),
    "Rest":                      ("explore",  "heal",     "self",  "gain", "explore", 1),
    "Adopt":                     ("explore",  "none",     "self",  "gain", "explore", 0),
    "ApplyBlessed":              ("buff",     "status",   "self",  "gain", "combat", 1),
    "ApplyCharged":              ("buff",     "status",   "self",  "gain", "combat", 1),
    "ApplyConfuse":              ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyDecay":                ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyDecayMultiply":        ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyHotSauced":            ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyLeech":                ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyMarked":               ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyMelting":              ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyNanoLaced":            ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyPlump":                ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyPoisoned":             ("debuff",   "damage",   "enemy", "gain", "combat", 1),
    "ApplySeasoned":             ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplySpellWeakness":        ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplySticky":               ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyStudied":              ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyStunned":              ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyThorns":               ("defense",  "shield",   "self",  "gain", "combat", 1),
    "ApplyVibrating":            ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "ApplyVulnerable":           ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "BecomeLodged":              ("cardmod",  "card",     "card",  "gain", "combat", 0),
    "BindToCard":                ("cardmod",  "card",     "card",  "gain", "both",   0),
    "CreateLodgedCard":          ("cardgen",  "card",     "card",  "gain", "combat", 0),
    "CreateLodgedCardWithDecay": ("cardgen",  "card",     "card",  "gain", "combat", 0),
    "Cripple":                   ("debuff",   "status",   "enemy", "gain", "combat", 1),
    "DealHalfHealthInDamage":    ("damage",   "damage",   "enemy", "gain", "combat", 1),
    "DispellConfusion":          ("buff",     "status",   "self",  "gain", "combat", 1),
    "EatenDamage":               ("damage",   "damage",   "enemy", "gain", "combat", 1),
    "ReduceStatusEffects":       ("buff",     "status",   "self",  "gain", "combat", 1),
    "DamageSelf":                ("damage",   "damage",   "self",  "harm", "combat", 1),
    "GainEnergy":                ("resource", "energy",   "self",  "gain", "combat", 1),
    "GainMaxEnergy":             ("buff",     "energy",   "self",  "gain", "combat", 1),
    "HealSelf":                  ("heal",     "heal",     "self",  "gain", "combat", 1),
    "IncreaseGloomStored":       ("resource", "resource", "self",  "gain", "both",   1),
    "IncreaseMaxHealth":         ("buff",     "status",     "self",  "gain", "both",   1),
    "WarmPlayer":                ("buff",     "status",   "self",  "gain", "explore", 1),
    "CauseRevengeArc":           ("misc",     "damage",   "enemy", "gain", "combat", 1),
    "ChooseCardToForget":        ("cardmod",  "card",     "card",  "gain", "both",   0),
    "BunchOfKeys":               ("cardgen",  "card",     "self",  "gain", "explore", 0),
    "DigForTreasure":            ("explore",  "none",     "world", "gain", "explore", 0),
    "Meditate":                  ("explore",  "heal",     "self",  "gain", "explore", 1),
    "Scavenge":                  ("explore",  "none",     "world", "gain", "explore", 0),
    "SearchPockets":             ("explore",  "none",     "world", "gain", "explore", 0),
    "StackOfKeycards":           ("cardgen",  "card",     "self",  "gain", "explore", 0),
    "TruffleHunt":               ("explore",  "none",     "world", "gain", "explore", 0),
    "DiscardAndShuffleAndRedraw": ("cardmod", "draw",     "self",  "gain", "combat", 1),
    "DiscardCard":               ("cardmod",  "card",     "card",  "gain", "both",   0),
    "DrawCardsEffect":           ("cardgen",  "draw",     "self",  "gain", "combat", 1),
    "CreateCardEncounter":       ("cardgen",  "card",     "card",  "gain", "explore", 0),
    "DestroyCampStructure":      ("explore",  "none",     "world", "neutral", "explore", 0),
    "ResetCampStructure":        ("explore",  "none",     "world", "neutral", "explore", 0),
    "ExecuteProtocol":           ("explore",  "none",     "world", "gain", "explore", 0),
    "LoyalCanary":               ("pet",      "none",     "self",  "gain", "combat", 0),
    "LoyalLouse":                ("pet",      "none",     "self",  "gain", "combat", 0),
    "OpenMap":                   ("explore",  "none",     "world", "gain", "explore", 0),
    "DigOld":                    ("explore",  "none",     "world", "gain", "explore", 0),
    "RevealAll":                 ("explore",  "none",     "world", "gain", "explore", 0),
    "StoreGloom":                ("resource", "resource", "self",  "gain", "both",   1),
    "StoredGloomToMaxHealth":    ("resource", "heal",     "self",  "gain", "both",   1),
    "RemoveTempProperties":      ("cardmod",  "card",     "card",  "neutral", "both", 0),
    "Break":                     ("explore",  "none",     "world", "gain", "explore", 0),
    "Dig":                       ("explore",  "none",     "world", "gain", "explore", 0),
    "Feed":                      ("explore",  "heal",     "self",  "gain", "explore", 1),
    "Fix":                       ("explore",  "none",     "world", "gain", "explore", 0),
    "Ignite":                    ("explore",  "none",     "world", "gain", "explore", 0),
    "Reveal":                    ("explore",  "none",     "world", "gain", "explore", 0),
    "Undiscovered":              ("explore",  "none",     "world", "neutral", "explore", 0),
    "Unlock":                    ("explore",  "none",     "world", "gain", "explore", 0),
}

# ⚠️⚠️ **运行时存在、但离线 `get_XXX` 序列抓不到的成员** —— 必须在这里补。
# 真机实证（第 62 轮）：CardIntent 类的静态字段里有 `__dealDamage` / `__nothing`，
# 卡牌上真的读到 `DealDamage×16Single`；但 `global-metadata.dat` 的 `get_XXX` 属性序列里
# **没有** `get_DealDamage`（它出现在 anchor 之前 -13651 字节，属另一批 token）
# ⇒ 离线权威表漏掉它们。
# ⚠️ 后果极隐蔽：`agent.js intentNameOf()` 能读出名字（名表走**静态字段**），
#    但 `summary()` 查不到语义 ⇒ 判成未知意图 ⇒ **伤害类意图全部不计入收益**
#    ⇒ 推演恒输出"没有值得打出的牌"。判据不是猜的：
#    真机名表(152) 与离线表(171) 的差集正好是 {DealDamage, Nothing}（且 DealDamage 在真机手牌上实测存在）。
RUNTIME_ONLY = {
    # op: (cat, metric, target, side, phase, value, num, mult)
    "DealDamage": ("damage", "damage", "enemy", "gain", "combat", 1, True, True),
    "Nothing":    ("misc",   "none",   "world", "neutral", "both",  0, False, False),
}

CAT_LABEL = {
    "damage": "直接伤害", "heal": "治疗", "defense": "防御", "debuff": "削弱敌人",
    "buff": "增益自身", "cardgen": "产生/获取牌", "cardmod": "改造牌",
    "resource": "资源", "eat": "消耗进食", "explore": "探索行动",
    "summon": "召唤", "pet": "宠物", "misc": "其他",
}
METRIC_LABEL = {
    "damage": "伤害", "heal": "生命", "shield": "护甲", "energy": "精力",
    "draw": "抽牌", "status": "状态层数", "resource": "资源", "card": "牌", "none": "—",
}


def main():
    d = json.load(io.open(DICT, encoding="utf-8"))
    intents = d["intents"]

    out = {}
    missing = []
    for e in intents:
        op = e["op"]
        row = S.get(op)
        if row is None:
            # RUNTIME_ONLY 里的成员不在 S 表（它们不是从 get_XXX 序列来的），单独处理
            if op in RUNTIME_ONLY:
                continue
            missing.append(op)
            continue
        cat, metric, target, side, phase, value = row
        # ⚠️ 不存 en/zh/key —— 词典 card_intent_dict.json 里已有，重复存纯浪费打包体积
        out[op] = {
            "cat": cat, "metric": metric,
            "target": target, "side": side, "phase": phase, "value": value,
            "num": "has_number" in (e.get("tags") or []),
            "mult": "has_multiplicity" in (e.get("tags") or []),
        }

    extra = [k for k in S if k not in {e["op"] for e in intents}]

    # ⚠️ 补运行时独有成员（离线表抓不到，见 RUNTIME_ONLY 注释）。
    #    这些是**真机上真实出现**的意图，缺一个就可能让整类收益归零。
    for op, (cat, metric, target, side, phase, value, num, mult) in RUNTIME_ONLY.items():
        out[op] = {
            "cat": cat, "metric": metric, "target": target,
            "side": side, "phase": phase, "value": value,
            "num": num, "mult": mult,
            "runtimeOnly": True,
        }

    doc = {
        "_comment": "CardIntent 语义层：类别/数值口径/作用对象/得失/场景/是否可计入收益。"
                    "由 tools/intent_semantics_build.py 从 card_intent_dict.json 生成。",
        "count": len(out),
        "cats": CAT_LABEL,
        "metrics": METRIC_LABEL,
        "intents": out,
    }
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(
        json.dumps(doc, ensure_ascii=False, indent=1))

    # 统计
    from collections import Counter
    cc = Counter(v["cat"] for v in out.values())
    vv = Counter((v["phase"], v["value"]) for v in out.values())
    print("intents classified:", len(out), "/", len(intents))
    print("cats:", dict(cc))
    print("phase/value:", {f"{a}/{b}": n for (a, b), n in vv.items()})
    if missing:
        print("!! 未分类（必须为 0）:", missing)
    if extra:
        print("!! S 表里有词典没有的名字:", extra)
    print("size:", os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
