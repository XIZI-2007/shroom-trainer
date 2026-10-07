# -*- coding: utf-8 -*-
"""battle_planner 单测：用真实词典数据造牌，验证推演结论是否符合直觉。

跑法：python tools/battle_planner_test.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from battle_planner import Enemy, plan, describe, card as mkcard   # noqa: E402
from battle_planner import (State, play_card as run_card,         # noqa: E402
                            status_kind, status_is_computable)
from intent_reader import IntentReader                            # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
        print("  FAIL  %s" % msg)
    else:
        print("  ok    %s" % msg)


def main():
    r = IntentReader()

    def C(name, cost, pairs):
        intents = [{"intent": op, "number": n, "mult": m} for op, n, m in pairs]
        return mkcard(name, cost, r.summary(intents, cost=cost))

    print("=== 例 1：两只敌，先易伤还是先重击？ ===")
    # 重击 9 伤（2 费）、易伤 2 层（1 费）。
    # ⚠️ 本游戏的易伤**每层 +50% 受伤**（用户实测口径，见 src/status_model.json 的 param）
    #    ⇒ 2 层 = ×2.0 ⇒ 先上易伤再打 9 伤 = 18 血，远高于"只打重击"。
    #    ⚠️ 旧实现按"层数即百分比"（2 层 = ×1.02）算，于是易伤几乎不影响排序，
    #       **用户实测发现"该先易伤、算法却把易伤排第三"** —— 这条断言就是那个回归。
    heavy = C("Heavy", 2, [("DealHalfHealthInDamage", 9, "Single")])
    heavy["effects"]["deal"] = 9.0          # 该意图无固定数值，手工给 9（模拟实际读到的 number）
    vuln = C("Vuln", 1, [("ApplyVulnerable", 2, "Single")])
    e = Enemy(hp=40, name="A")
    res = plan([vuln, heavy], energy=3, enemies=[e])
    print("   ", describe(res))
    check(res["ok"], "推演成功")
    check(res["sequence"][0]["name"] == "Vuln",
          "★ 先上易伤再打重击（每层 +50%，先易伤后攻击更划算）")
    check(res["damageDealt"] >= 18, "★ 2 层易伤下 9 伤变 18（实际 %.1f）" % res["damageDealt"])
    # 倍率口径：每层 +50%
    e3 = Enemy(hp=100, statuses={"Vulnerable": 3})
    check(abs(e3.damage_multiplier() - 2.5) < 1e-6,
          "★ 3 层易伤 = ×2.5（每层 +50%%）实际 %.3f" % e3.damage_multiplier())
    e1 = Enemy(hp=100, statuses={"Vulnerable": 1})
    check(abs(e1.damage_multiplier() - 1.5) < 1e-6,
          "★ 1 层易伤 = ×1.5（实际 %.3f）" % e1.damage_multiplier())

    print("\n=== 例 1b：Airborne 减伤 / Thorns 反伤（照官方 tooltip）===")
    e_air = Enemy(hp=50, statuses={"Airborne": 1})
    check(abs(e_air.damage_multiplier() - 0.5) < 1e-6,
          "Airborne 承伤减半 ⇒ ×0.5（实际 %.3f）" % e_air.damage_multiplier())
    e_both = Enemy(hp=50, statuses={"Airborne": 1, "Vulnerable": 3})
    check(abs(e_both.damage_multiplier() - 1.25) < 1e-6,
          "Airborne ×0.5 与 3 层易伤 ×2.5 相乘 = ×1.25（实际 %.3f）" % e_both.damage_multiplier())
    e_th = Enemy(hp=50, statuses={"Thorns": 3})
    check(e_th.counter_damage() == 3, "Thorns 3 层 ⇒ 反击 3（实际 %d）" % e_th.counter_damage())
    # 反伤要真的扣到玩家血上（没打死时）
    strike1 = C("S", 1, [("DealDamage", 5, "Single")])
    st = State(player_hp=30, energy=3, enemies=[Enemy(hp=50, statuses={"Thorns": 4})])
    run_card(st, strike1)
    check(st.player_hp == 26.0, "打出 5 伤、敌人未死 ⇒ 玩家吃 4 点反伤（实际 %.0f）" % st.player_hp)
    # 打死了就不反伤（tooltip：除非伤害致命）
    st2 = State(player_hp=30, energy=3, enemies=[Enemy(hp=3, statuses={"Thorns": 4})])
    run_card(st2, strike1)
    check(st2.player_hp == 30.0, "一击致命 ⇒ 不触发反伤（实际 %.0f）" % st2.player_hp)

    print("\n=== 例 1c：状态照 status_intent_map 通用挂载（不再硬编码）===")
    st_stun = C("Bash", 1, [("ApplyStunned", 2, "Single")])
    st3 = State(player_hp=30, energy=3, enemies=[Enemy(hp=50)])
    gains = run_card(st3, st_stun)
    check(st3.enemies[0].stacks("Stunned") == 2, "ApplyStunned 2 ⇒ 敌人挂上 Stunned 2")
    check(any(s["name"] == "Stunned" for s in gains["status"]), "收益里记下了状态")
    # Confused 是"敌人攻击降低 N"，不该被当成伤害或反伤
    check(status_kind("Confused") == "enemy_penalty", "Confused 归类为 enemy_penalty")
    check(status_kind("Vulnerable") == "taken_pct", "Vulnerable 归类为 taken_pct")
    check(status_kind("Armoured") == "taken_reduce", "Armoured 归类为 taken_reduce（幅度未知）")
    # ⚠️ 文案没给数值的状态**不能**被当成可算（Armoured/Decay 都不行）
    check(not status_is_computable("Armoured"), "★ Armoured 幅度未知 ⇒ 不可算（不猜）")
    check(not status_is_computable("Decay"), "★ Decay 幅度未知 ⇒ 不可算（不猜）")
    check(status_is_computable("Vulnerable"), "Vulnerable 可算")

    print("\n=== 例 2：斩杀判定 ===")
    strike = C("Strike", 1, [("DealHalfHealthInDamage", 6, "Single")])
    strike["effects"]["deal"] = 6.0
    res2 = plan([strike, strike, strike], energy=3, enemies=[Enemy(hp=18)])
    print("   ", describe(res2))
    check(res2["lethal"], "18 血 3 张 6 伤 = 斩杀（18 恰好够）")
    check(res2["damageDealt"] >= 18, "削血 ≥18")

    print("\n=== 例 3：不打负收益的牌 ===")
    # 只有治疗，敌人在场且满血 —— 应该判定为"没有值得打出的牌"
    heal = C("Heal", 1, [("HealSelf", 5, "Self")])
    res3 = plan([heal], energy=3, enemies=[Enemy(hp=20)])
    print("   ", describe(res3))
    check(res3["ok"] and not res3["sequence"], "纯治疗在满血无威胁时不被推荐")

    print("\n=== 例 4：AOE 打到所有敌人 ===")
    aoe = C("Swipe", 2, [("DealHalfHealthInDamage", 4, "All")])
    aoe["effects"]["deal"] = 4.0
    aoe["effects"]["aoe"] = True
    res4 = plan([aoe], energy=3, enemies=[Enemy(hp=10), Enemy(hp=10), Enemy(hp=10)])
    print("   ", describe(res4))
    check(res4["damageDealt"] == 12, "AOE 打 3 只 = 12（实际 %.1f）" % res4["damageDealt"])
    check(not res4["lethal"], "4×3=12 < 3 只共 30 血 ⇒ 不该误判斩杀")
    check(res4["enemiesLeft"] == 3, "三只都还活着")

    print("\n=== 例 5：AOE 真的能斩杀 ===")
    aoe2 = C("BigSwipe", 2, [("DealHalfHealthInDamage", 12, "All")])
    aoe2["effects"]["deal"] = 12.0
    aoe2["effects"]["aoe"] = True
    res5 = plan([aoe2], energy=3, enemies=[Enemy(hp=10), Enemy(hp=10), Enemy(hp=10)])
    print("   ", describe(res5))
    check(res5["lethal"] and res5["enemiesLeft"] == 0, "12 伤 AOE 清掉 3 只 10 血")

    print("\n=== 例 6：精力约束（打不完就只能挑） ===")
    s1 = C("Strike", 1, [("DealHalfHealthInDamage", 6, "Single")])
    s1["effects"]["deal"] = 6.0
    big = C("Big", 3, [("DealHalfHealthInDamage", 15, "Single")])
    big["effects"]["deal"] = 15.0
    res6 = plan([s1, s1, big], energy=3, enemies=[Enemy(hp=100)])
    print("   ", describe(res6))
    check(res6["ok"], "推演成功")
    check(res6["sequence"][0]["name"] == "Big", "精力 3 时优先 15 伤的大牌（15 > 6+6）")
    check(res6["spentEnergy"] <= 3, "消耗不超过 3 精力（实际 %d）" % res6["spentEnergy"])

    print("\n=== 例 7：穷举 vs 贪心的边界 ===")
    hand = []
    for i in range(7):                       # 7 张 > MAX_EXACT(6) ⇒ 走贪心
        c = C("S%d" % i, 1, [("DealHalfHealthInDamage", 5, "Single")])
        c["effects"]["deal"] = 5.0
        hand.append(c)
    res7 = plan(hand, energy=3, enemies=[Enemy(hp=50)])
    print("   ", describe(res7), "method=%s nodes=%d" % (res7["method"], res7["nodes"]))
    check(res7["method"] == "greedy", "7 张走贪心（超穷举上限）")
    check(res7["confidence"] == "medium", "贪心的置信度标为 medium")
    check(res7["spentEnergy"] == 3, "精力 3 正好打 3 张")

    print("\n=== 例 8：健壮性 ===")
    check(not plan([], enemies=[Enemy(hp=5)])["ok"], "空手牌 → 不崩、给出原因")
    check(not plan([strike], enemies=[])["ok"], "无敌人 → 不崩、给出原因")
    bad = plan([C("Free", 0, [])], energy=0, enemies=[Enemy(hp=5)])
    # ⚠️ 空效果牌**不是**错误，是"没有值得打的牌" ⇒ ok=True + 空序列（与例 3 同一种结果）。
    #    早先断言写成 ok=False，是**测试错**而不是实现错 —— 已按真实语义改正。
    check(bad["ok"] and not bad["sequence"], "0 费空牌 → 不推荐打出（空序列，不是报错）")
    check("没有值得打出" in describe(bad), "描述里说清「没有值得打出的牌」")

    print("\n=== 例 9：内存战况快照 → 推演（端到端桥接）===")
    # 模拟 agent.js battle(withIntents=True) 的真实返回结构
    snap = {
        "ok": True, "warning": "",
        "hp": 42, "maxHp": 60, "energy": 3, "softCap": 3,
        "deckTypeName": "战斗",
        "enemies": [
            {"name": "洞窟鼠", "hp": 12, "maxHp": 12, "armour": 0, "alive": True, "retreated": False},
            {"name": "苔藓怪", "hp": 30, "maxHp": 30, "armour": 4, "alive": True, "retreated": False},
            {"name": "尸体", "hp": 0, "maxHp": 20, "armour": 0, "alive": False, "retreated": False},
            {"name": "逃兵", "hp": 15, "maxHp": 15, "armour": 0, "alive": True, "retreated": True},
        ],
        "hand": [
            {"name": "Bash", "cost": 1,
             "intents": [{"intent": "DealHalfHealthInDamage", "number": 8, "mult": "Single"}]},
            {"name": "Guard", "cost": 1,
             "intents": [{"intent": "ShieldDONTUSE", "number": 6, "mult": "Self"}]},
        ],
        "handCount": 2, "handSize": 5,
    }
    from battle_planner import from_snapshot, plan_from_snapshot        # noqa: E402
    br = from_snapshot(snap, reader=r)
    check(len(br["enemies"]) == 2, "死敌与已撤退的敌人被剔除（4 → 2 只，实际 %d）" % len(br["enemies"]))
    check(br["energy"] == 3 and br["player_hp"] == 42.0, "精力/血量取自快照")
    check(br["enemies"][1].block == 4, "敌人护甲当作 block 传入（苔藓怪 4）")

    res9, br9 = plan_from_snapshot(snap, reader=r)
    print("   ", describe(res9))
    check(res9["ok"], "快照直接推演出方案")
    check(res9["bridge"]["deckTypeName"] == "战斗", "桥接信息带回牌库类型")
    # 不写死译名（汉化表会变）——用 reader 自己翻一次再比
    check(res9.get("sequence") and res9["sequence"][0]["name"] == r.card_name("Bash"),
          "首选伤害牌（%s）" % (res9["sequence"][0]["name"] if res9.get("sequence") else "无"))
    check(res9["damageDealt"] == 8.0, "8 伤打在 12 血老鼠身上 = 削 8（实际 %.1f）" % res9["damageDealt"])

    print("\n=== 例 10：快照没带效果时要说清楚（不许默默算错）===")
    snap2 = dict(snap)
    snap2["hand"] = [{"name": "Bash", "cost": 1}]          # 缺 intents
    res10, _ = plan_from_snapshot(snap2, reader=r)
    check(any("没带效果" in a for a in res10.get("assumptions") or []),
          "缺效果时在 assumptions 里显式提示（不静默低估）")

    print("\n=== 例 11：真机快照回归（第 62 轮实测，防 DealDamage 静默失效）===")
    # ⚠️ 这是**真机上跑出来的**快照（PID 178688，战斗牌库，敌 5 只）。
    #    修复前：DealDamage 不在语义表 ⇒ 伤害全不计 ⇒ 输出"没有值得打出的牌"。
    #    修复后：应给出以伤害为主的出牌线。**这是那条 bug 的专用回归**。
    live = {
        "ok": True, "deckTypeName": "战斗",
        "hp": 44, "maxHp": 82, "energy": 2, "softCap": 3,
        "enemies": [
            {"name": "A", "hp": 10, "maxHp": 11, "armour": 0, "alive": True, "retreated": False},
            {"name": "B", "hp": 10, "maxHp": 11, "armour": 0, "alive": True, "retreated": False},
            {"name": "C", "hp": 99, "maxHp": 113, "armour": 0, "alive": True, "retreated": False},
            {"name": "D", "hp": 10, "maxHp": 11, "armour": 0, "alive": True, "retreated": False},
            {"name": "E", "hp": 10, "maxHp": 11, "armour": 0, "alive": True, "retreated": False},
        ],
        "hand": [
            {"name": "Stab", "cost": 1, "intents": [{"intent": "DealDamage", "number": 16, "mult": "Single"}]},
            {"name": "Stab", "cost": 1, "intents": [{"intent": "DealDamage", "number": 6, "mult": "Single"}]},
            {"name": "Stab", "cost": 1, "intents": [{"intent": "DealDamage", "number": 4, "mult": "Single"},
                                                    {"intent": "ApplyVulnerable", "number": 2, "mult": "Single"}]},
            {"name": "Roast", "cost": 1, "intents": [{"intent": "DealDamage", "number": 3, "mult": "Single"}]},
            {"name": "Spicy Toasty", "cost": 0, "intents": [{"intent": "HealSelf", "number": 3, "mult": "Single"},
                                                            {"intent": "DealDamage", "number": 3, "mult": "AllButSelf"},
                                                            {"intent": "Consume", "number": 1, "mult": "Single"}]},
        ],
    }
    res11, br11 = plan_from_snapshot(live, reader=r)
    print("    " + describe(res11))
    check(res11.get("ok"), "真机快照能推出方案")
    check(bool(res11.get("sequence")), "★ 有非空出牌线（修复前恒为空 = DealDamage 未计入）")
    check(res11.get("damageDealt", 0) > 0, "★ 削减血量 > 0（实际 %s）" % res11.get("damageDealt"))
    check(br11["energy"] == 2 and len(br11["enemies"]) == 5, "精力/敌人数取自快照")
    # 手牌 5 张、精力 2 ⇒ 至多打 2 张 1 费牌（+0 费）
    check(len(res11.get("sequence") or []) <= 3, "出牌数受精力约束（≤3 张）")

    print("\n=== 例 12：快照带状态 → 推演按状态改倍率 ===")
    # 同一份手牌，只把首个敌人挂上 100 层易伤 ⇒ 倍率 ×2，削血必须变多。
    snap_st = dict(live)
    snap_st["enemies"] = [dict(e) for e in live["enemies"]]
    snap_st["enemies"][0]["statuses"] = [{"name": "Vulnerable", "number": 100}]
    res_st, br_st = plan_from_snapshot(snap_st, reader=r)
    print("    " + describe(res_st))
    check(br_st["enemies"][0].stacks("Vulnerable") == 100, "快照里的状态传进了 Enemy")
    check(res_st["bridge"].get("enemyStatuses"), "桥接信息带回敌人状态")
    # 100 层易伤的那只血少（10），第 1 张牌若打它就超杀；这里只验证"倍率生效"
    # 用一只高血敌人更直观：
    snap_st2 = dict(live)
    snap_st2["enemies"] = [dict(live["enemies"][2])]        # 99 血那只
    snap_st2["enemies"][0]["statuses"] = [{"name": "Vulnerable", "number": 1}]
    snap_st2["hand"] = [{"name": "Stab", "cost": 1,
                         "intents": [{"intent": "DealDamage", "number": 10, "mult": "Single"}]}]
    res_a, _ = plan_from_snapshot(snap_st2, reader=r)
    snap_st3 = dict(snap_st2)
    snap_st3["enemies"] = [dict(snap_st2["enemies"][0])]
    snap_st3["enemies"][0]["statuses"] = []
    res_b, _ = plan_from_snapshot(snap_st3, reader=r)
    check(res_a["damageDealt"] > res_b["damageDealt"],
          "★ 1 层易伤下削血 %.1f > 无状态 %.1f（倍率真的生效）"
          % (res_a["damageDealt"], res_b["damageDealt"]))
    check(abs(res_a["damageDealt"] - 15.0) < 0.01,
          "10 伤 ×1.5（1 层易伤）= 15（实际 %.1f）" % res_a["damageDealt"])

    print("\n=== 例 13：状态读不出来时必须说清楚（不许静默偏乐观）===")
    snap_diag = dict(live)
    snap_diag["enemies"] = [dict(e) for e in live["enemies"]]
    snap_diag["enemies"][0]["statusDiag"] = {"statusUnknown": [{"ptr": "0x1", "slots": []}]}
    res13, br13 = plan_from_snapshot(snap_diag, reader=r)
    check(any("状态没读出来" in a for a in res13.get("assumptions") or []),
          "敌人状态读取失败时在 assumptions 里提示（不静默）")

    print("\n=== 例 14：SpellWeakness 挂在敌人身上 ⇒ 打牌自伤（照官方 tooltip）===")
    # 文案：「当你打出一张牌时，将会受到 [PNUM] 点伤害」—— 挂在**敌人**身上。
    sw = C("X", 1, [("ApplySpellWeakness", 3, "Single")])
    st14 = State(player_hp=30, energy=3, enemies=[Enemy(hp=50)])
    run_card(st14, sw)
    check(st14.enemies[0].stacks("SpellWeakness") == 3,
          "SpellWeakness 挂在敌人身上（实际 %d）" % st14.enemies[0].stacks("SpellWeakness"))
    check(st14.player_hp == 27.0, "★ 本张牌自己也吃一次自伤 ⇒ 30-3（实际 %.0f）" % st14.player_hp)
    # 已经在场上的惩罚：再打一张别的牌，还要再吃一次
    other = C("Y", 1, [("DealDamage", 5, "Single")])
    run_card(st14, other)
    check(st14.player_hp == 24.0, "★ 再打一张再吃 3（实际 %.0f）" % st14.player_hp)
    # 自身增益（Blessed/Charged/Thorns）走玩家侧
    bl = C("B", 1, [("ApplyBlessed", 2, "Single")])
    st14b = State(player_hp=30, energy=3, enemies=[Enemy(hp=50)])
    run_card(st14b, bl)
    check(st14b.player_stacks("Blessed") == 2, "Blessed 记在玩家侧（增益自身）")

    print("\n=== 例 15：增伤类 buff 牌（★ 用户报：以前完全不会被推荐）===")
    # 「令卡牌伤害 +6」的 1 费牌 + 一张 8 伤 2 费攻击，精力 3。
    # 不打 buff：8 伤；先 buff 再打：14 伤（多花 1 费，精力刚好）。
    buff = C("Sharp", 1, [("IncreaseDamage", 6, "Card")])
    atk = C("Hit", 2, [("DealDamage", 8, "Single")])
    res15 = plan([buff, atk], energy=3, enemies=[Enemy(hp=100)])
    print("    " + describe(res15))
    check(bool(res15["sequence"]) and res15["sequence"][0]["name"] == "Sharp",
          "★ 增伤牌排在攻击牌**之前**（先上 buff，后面那张吃加成）")
    check(res15["damageDealt"] >= 14.0,
          "★ 8 伤吃到 +6 ⇒ 14（实际 %.1f）" % res15["damageDealt"])
    # 伤害翻倍牌（MultiplyDamage）
    dbl = C("Double", 1, [("MultiplyDamage", 2, "Card")])
    res15b = plan([dbl, atk], energy=3, enemies=[Enemy(hp=100)])
    print("    " + describe(res15b))
    check(res15b["damageDealt"] >= 16.0,
          "★ 8 伤被 ×2 ⇒ 16（实际 %.1f）" % res15b["damageDealt"])
    # 反例：光上 buff、没牌吃它 ⇒ 不该推荐（不然等于白花精力）
    res15c = plan([C("SharpOnly", 1, [("IncreaseDamage", 6, "Card")])],
                  energy=3, enemies=[Enemy(hp=100)])
    check(res15c["ok"] and not res15c["sequence"],
          "★ 只有增伤牌、没有伤害牌可吃 ⇒ 不推荐（上了也是白上）")

    print("\n=== 例 16：每步的「使用对象」要带血量（用户靠血量判断打谁）===")
    snap16 = {
        "ok": True, "deckTypeName": "战斗", "hp": 40, "energy": 3,
        "enemies": [
            {"name": "A", "seq": 1, "hp": 1, "maxHp": 5, "armour": 3,
             "alive": True, "retreated": False},
            {"name": "B", "seq": 2, "hp": 9, "maxHp": 10, "armour": 0,
             "alive": True, "retreated": False},
        ],
        "hand": [
            {"name": "S1", "cost": 1,
             "intents": [{"intent": "DealDamage", "number": 4, "mult": "Single"}]},
            {"name": "Sw", "cost": 1,
             "intents": [{"intent": "DealDamage", "number": 3, "mult": "All"}]},
        ],
    }
    res16, _ = plan_from_snapshot(snap16, reader=r)
    print("    " + describe(res16))
    check(bool(res16["sequence"]) and res16["sequence"][0]["target"] is not None,
          "★ 每一步都带 target（界面靠它显示「→ 目标血量」）")
    t0 = res16["sequence"][0]["target"]
    check(t0.get("label") and t0.get("hp") is not None and t0.get("maxHp") == 5.0,
          "★ target 带 label/hp/maxHp：%s" % t0)
    check(t0["hp"] == 1.0,
          "★ 血量取的是**打之前**的（① 1/5，不是打完的 0）实际 %s" % t0.get("hp"))
    _sw = [s for s in res16["sequence"] if s["name"] == "Sw"]
    check(bool(_sw) and bool(_sw[0]["target"]) and _sw[0]["target"]["aoe"] is True,
          "★ 全体牌标 aoe=True（界面显示「→ 全体」）"
          + ("" if _sw else "（本次精力没够打出它）"))
    # 目标标签来自快照的 seq（① ② …），不是游戏里的名字
    check(t0["label"] == "①", "★ 目标标签是「从左到右」的编号：%s" % t0["label"])

    # ★★ 同一个敌人被打两次 ⇒ 两步显示的都必须是**实时血**，
    #    而不是"第二步时它只剩 X"的推演预测值（用户实测报的"出牌后血量不刷新"就是这个）。
    snap16b = {
        "ok": True, "deckTypeName": "战斗", "hp": 40, "energy": 3,
        "enemies": [{"name": "A", "seq": 1, "hp": 9, "maxHp": 10, "armour": 0,
                     "alive": True, "retreated": False}],
        "hand": [
            {"name": "S1", "cost": 1,
             "intents": [{"intent": "DealDamage", "number": 4, "mult": "Single"}]},
            {"name": "S2", "cost": 1,
             "intents": [{"intent": "DealDamage", "number": 4, "mult": "Single"}]},
        ],
    }
    res16b, _ = plan_from_snapshot(snap16b, reader=r)
    print("    " + describe(res16b))
    _hps = [s["target"]["hp"] for s in res16b["sequence"] if s.get("target")]
    check(len(_hps) >= 2 and all(h == 9.0 for h in _hps),
          "★★ 两步都报**实时血 9**（不是预测的 9→5）实际 %s" % _hps)

    print()
    if FAIL:
        print("FAILED %d 项" % len(FAIL))
        for f in FAIL:
            print("  -", f)
        return 1
    print("BATTLE PLANNER OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
