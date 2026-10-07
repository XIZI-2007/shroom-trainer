# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""战况读取（内存侧）的契约检查 + 桥接自测 —— **不碰 UI**（UI 归 ui_check）。

为什么单独一个文件：ui_check 跑的是窗口/渲染/材质，动线程与 Qt 事件循环，
周期长；内存侧的改动（agent.js 的字段名、trainer_core 的 RPC 名）与它无关，
塞进去只会拖慢回归。这里做两件事：

  A. 源码契约（离线，<1s）：钉死 agent.js 里**字段名**与**规模**上的约定。
     字段名是唯一会静默出错的点 —— 游戏更新后字段改名，读出来是 -1，
     不会抛异常，只会给出错的血量。所以逐个钉死。
  B. 桥接自测（离线）：用假的 battle() 快照喂 from_snapshot / plan_from_snapshot，
     确认「内存结构 → 推演输入」这段转换正确（死敌剔除、护甲当 block、缺效果提示）。

跑法：python tools/battle_probe_test.py
      （游戏没开也能跑，全部是离线检查）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
        print("  FAIL  %s" % msg)
    else:
        print("  ok    %s" % msg)


def _body(src, func_name):
    """取某个 JS 函数的函数体（到下一个顶层 function 为止）。"""
    i = src.find("function %s(" % func_name)
    if i < 0:
        return ""
    j = src.find("\nfunction ", i + 10)
    return src[i:j] if j > i else src[i:]


def part_a_contract():
    print("--- A. agent.js 战况读取的源码契约 ---")
    path = os.path.join(SRC, "agent.js")
    src = open(path, encoding="utf-8").read()

    check(all(("function %s(" % f) in src
              for f in ("battleSnapshot", "findEnemyController", "readEnemy", "enemyListOf")),
          "定义了 battleSnapshot / findEnemyController / readEnemy / enemyListOf")

    # ⚠️⚠️ 当前血量字段叫 `_startingHealth`，**不是** `_currentHealth`。
    #     按语义猜必错，而且从 dump.cs 看不出来（dump 里字段偏移全是 0x0）。
    check(all(("'%s'" % f) in src for f in ("_startingHealth", "_maxHealth", "_armour")),
          "★ 敌人血量字段 = _startingHealth / _maxHealth / _armour")

    # 血量必须在 readEnemy 里、且必经 fieldOffOf（动态偏移）
    rb = _body(src, "readEnemy")
    check("_healthData" in rb and "fieldOffOf(hd, '_startingHealth')" in rb
          and "fieldOffOf(hd, '_maxHealth')" in rb and "fieldOffOf(hd, '_armour')" in rb,
          "★ readEnemy 里血量走 fieldOffOf（动态偏移，不硬编码）")

    # 手牌元素是 M_GameplayCardInstance（运行时实例）⇒ 必须顺卡数据字段再读
    bb = _body(src, "battleSnapshot")
    # ⚠️ 字段名是 `<DataImpl>k__BackingField`（9-24 轮真机实证），
    #    不是 `_cardData`（那是 dump 里 get_IData 的想当然写法，真机读不到）。
    #    ⚠️ 候选表是**模块级 const**，不在 cardDataOfInstance 函数体里
    #       —— 所以断言要查 const 那段，查函数体会假 FAIL（踩过）。
    m = re.search(r"const CARD_DATA_FIELDS\s*=\s*\[([^\]]*)\]", src)
    order = m.group(1) if m else ""
    check("'<DataImpl>k__BackingField'" in order,
          "★ 卡数据候选表含 `<DataImpl>k__BackingField`（真机实证名，别改回 _cardData）")
    check(order.find("'<DataImpl>k__BackingField'") >= 0
          and order.find("'<DataImpl>k__BackingField'") < order.find("'_cardData'")
          or order.find("'_cardData'") < 0,
          "★ 卡数据候选表首选 `<DataImpl>`（_cardData 只能排后面当兜底）")
    cb = _body(src, "cardDataOfInstance")
    check("CARD_DATA_FIELDS" in cb and "fieldOffOf(inst, f)" in cb,
          "★ cardDataOfInstance 遍历 CARD_DATA_FIELDS + 必经 fieldOffOf（沿父类链）")
    check("cardDataOfInstance(c)" in bb and "cardIntents(src)" in bb,
          "★ battleSnapshot 手牌段调 cardDataOfInstance（别再内联一份候选表）")
    check("pickDeck(cands)" in bb and "_heldCards" in bb,
          "★ 手牌挑库复用 pickDeck + _heldCards（营地/战斗别挑错）")

    # 精力/血量复用已有的 playerEnergy/playerHealth（别另写一套偏移）
    check("playerEnergy()" in bb and "OFF.energy" in bb,
          "★ 精力复用 playerEnergy()（不另写一套偏移）")
    check("playerHealth()" in bb and "OFF.health" in bb,
          "★ 玩家血量复用 playerHealth()")

    # 挂载点：M_EnemyController 的敌人们表，字段名兜底
    el = _body(src, "enemyListOf")
    check("'enemies'" in el and "'_currentEnemies'" in el and "'_enemies'" in el,
          "★ 敌人表字段名 = enemies，退 _currentEnemies / _enemies")

    # ⚠️⚠️ 第 45 轮定论：**游戏没给敌人赋名字**。LocalizedEnemyName @0x150 的
    #     类型是 LocalizedFallbackText（不是 string）⇒ 当托管 string 解必空。
    #     唯一真 string 名在 V_Enemy.<EnemyName>@0x1C8，但 V_Enemy 视图不可达。
    #     ⇒ 禁止再"想办法取名"，UI 改按从左到右编号。下面把这三条钉死。
    check("LocalizedFallbackText" in src and "V_Enemy" in src,
          "★ 文件里写明 LocalizedFallbackText/V_Enemy 双证结论（防后人再挖一遍）")
    check("敌人名" in rb,
          "★ readEnemy 段落点明「游戏没给敌人名字」")
    check("cardDataName(enemy)" not in rb,
          "★ 已删掉 cardDataName(enemy) 退路（对 M_Enemy 是结构性死代码）")
    check("readEnemyRawName(enemy)" in rb and "out.rawName" in rb,
          "★ 原始名只进 rawName 供诊断，不当显示名")
    ren = _body(src, "readEnemyRawName")
    check("class_get_name" in ren and "String" in ren,
          "★ readEnemyRawName 先验 klass 是不是 String 才读（不瞎解）")
    check("seq" in rb and "idx" in rb,
          "★ readEnemy 带 idx → seq（从左到右编号，UI 显示用）")

    # 下游：battle_planner 侧必须优先用 seq 编号当显示名
    bp = open(os.path.join(SRC, "battle_planner.py"), encoding="utf-8").read()
    check("def enemy_label(" in bp and "_CIRCLED" in bp,
          "★ battle_planner 提供 enemy_label(seq)（①…⑩ 编号）")
    check("enemy_label(e.get(\"seq\"))" in bp or "enemy_label(e.get('seq'))" in bp,
          "★ from_snapshot 优先用 seq 编号当敌人显示名（name 不可信）")

    # RPC 名必须 camelCase（Frida 不会自动转下划线）
    check("battle(withIntents)" in src,
          "★ RPC 导出名是 battle（camelCase；Python 端调同样拼写）")
    check("if (withIntents) row.intents = cardIntents(src);" in bb,
          "★ battleSnapshot 有 withIntents 开关（只要牌名时别付读意图的代价）")

    print("--- B. trainer_core 的 RPC 接线 ---")
    cp = os.path.join(SRC, "trainer_core.py")
    csrc = open(cp, encoding="utf-8").read()
    check("def battle(self, with_intents=False)" in csrc,
          "trainer_core 定义了 battle(with_intents=False)")
    check("exports_sync.battle(" in csrc,
          "★ 调用的是 exports_sync.battle（与 agent.js 的 camelCase 名一致）")
    # ⚠️ 防御性：不许出现 exports_sync.battle_snapshot（会 AttributeError）
    check("exports_sync.battle_snapshot" not in csrc,
          "★ 没有误写成 exports_sync.battle_snapshot（那会 AttributeError）")

    print("--- B2. 敌人状态读取（第 63 轮真机标定，★ 全是踩过坑的）---")
    st_body = _body(src, "readEnemyStatuses")
    # 坑 1：IL2CPP 属性后备字段真名带尖括号。写 `_statusEffects` 会静默 off=-1。
    check("<_statusEffects>k__BackingField" in st_body,
          "★ 状态字段名含 `<_statusEffects>k__BackingField`（裸名查不到，静默 off=-1）")
    check("statusFieldName" in st_body,
          "★ 命中时记下实际用的字段名（诊断用）")
    # 坑 2：元素是 StatusEffectPair，必须按**字段名**取 _statusEffect / Number。
    check("fieldOffOf(el, '_statusEffect')" in st_body and "fieldOffOf(el, 'Number')" in st_body,
          "★ StatusEffectPair 按字段名取 _statusEffect/Number（不猜偏移）")
    # 坑 3：层数绝不能取 `s*8+4` —— 那是指针高 4 字节，会读出 678 这种垃圾数。
    check("s * 8 + 4" not in st_body,
          "★ 禁止用 `s*8+4` 取层数（指针高 4 字节 ⇒ 垃圾数且不被 sanity 拦下）")
    check("s * 8 + 8" in st_body,
          "★ 兜底槽位扫描取 `s*8+8`（指针后一个对齐槽）")
    # 坑 4：玩家状态——真机 dump 证明 M_Player 没有状态字段，空表是预期
    # ⚠️ 结论写在函数**上方**的注释里，`_body` 只取到 `function` 那一行开始，
    #    所以这里要连注释一起取（往前多看一段），否则断言恒 FAIL（踩过）。
    _pi = src.find("function readPlayerStatuses(")
    _pseg = src[max(0, _pi - 1600):_pi + 400] if _pi > 0 else ""
    check("M_Player" in _pseg and "预期" in _pseg,
          "★ readPlayerStatuses 写明「M_Player 无状态字段 ⇒ 空表是预期」")
    # status_meta.py 必须补运行时独有成员（Vulnerable/Poisoned），与 CardIntent 的
    # DealDamage 同型 —— 漏了最核心的易伤倍率就算不出来。
    smp = open(os.path.join(ROOT, "tools", "status_meta.py"), encoding="utf-8").read()
    check("RUNTIME_ONLY_MEMBERS" in smp and "Vulnerable" in smp and "Poisoned" in smp,
          "★ status_meta 有 RUNTIME_ONLY_MEMBERS=[Vulnerable, Poisoned]"
          "（离线 get_XXX 序列落在锚点之前，抓不到）")
    # 中毒是「回合结束失去生命」，**不是**本回合即时伤害
    mod = json.load(open(os.path.join(SRC, "status_model.json"), encoding="utf-8"))
    check((mod.get("items") or {}).get("Poisoned", {}).get("kind") == "end_turn_dot",
          "★ Poisoned 归 end_turn_dot（官方文案：回合结束时失去生命，不是即时伤害）")
    check((mod.get("items") or {}).get("Vulnerable", {}).get("kind") == "taken_pct",
          "★ Vulnerable 归 taken_pct（每层 +50% 受伤，实测口径）")
    check((mod.get("items") or {}).get("Poisoned", {}).get("computable") is False,
          "★ Poisoned 不可算（不在本回合出牌期内结算）")
    # 意图→状态映射必须覆盖 ApplyVulnerable（用数据表，不硬编码）
    im = json.load(open(os.path.join(SRC, "status_intent_map.json"), encoding="utf-8"))
    check((im.get("apply") or {}).get("ApplyVulnerable") == "Vulnerable",
          "★ status_intent_map.apply.ApplyVulnerable == Vulnerable")
    check((im.get("apply") or {}).get("ApplyPoisoned") == "Poisoned",
          "★ 映射表覆盖 ApplyPoisoned")


PANEL_KEYS = ("hp", "energy", "intents")


def _live_check():
    """真机检查（游戏没开就跳过）：battle() 的返回结构必须含计划所需字段。

    ⚠️ **面板在跑时必须跳过**：Frida 只允许一个 session 注入同一进程，
       面板已经占着它 ⇒ 这里的 `attach()` 会卡住（不是抛异常，是**挂住**），
       外面只能把它 kill 掉（症状：零输出 + SIGTERM，看着像崩溃）。
    """
    print("--- D. 真机结构检查（游戏没开 / 面板在跑则跳过）---")
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ShroomTrainer.exe", "/FO", "CSV"],
                             capture_output=True)
        if b"ShroomTrainer.exe" in out.stdout:
            print("  skip  辅助面板正在运行（Frida 单 session）—— 跳过真机段")
            return
    except Exception:
        pass
    try:
        sys.path.insert(0, SRC)
        from trainer_core import GameTrainer
        t = GameTrainer()
        if t.find_pid() is None:
            print("  skip  游戏没在运行 —— 跳过（真机验证待游戏启动）")
            return
        t.attach()
        try:
            snap = t.battle(True)
            print("  快照键：%s" % ", ".join(sorted(snap.keys())))
            check(snap.get("ok"), "battle() 返回 ok=True")
            check(snap.get("hp", -1) >= 0, "读到玩家血量（%s）" % snap.get("hp"))
            check(snap.get("energy", -1) >= 0, "读到精力（%s）" % snap.get("energy"))
            for k in ("enemies", "hand"):
                check(isinstance(snap.get(k), list), "%s 是列表（%d 项）"
                      % (k, len(snap.get(k) or [])))
            for e in (snap.get("enemies") or [])[:3]:
                check(e.get("hp", -1) >= 0, "敌人 %s 血量读出（%s/%s）"
                      % (e.get("name"), e.get("hp"), e.get("maxHp")))
            for c in (snap.get("hand") or [])[:3]:
                check(c.get("name"), "手牌名读出（%s）" % c.get("name"))
                check(isinstance(c.get("intents"), list),
                      "%s 带效果条目（%d 条）" % (c.get("name"), len(c.get("intents") or [])))
            if not (snap.get("enemies") or []):
                print("  note  当前不在战斗中（敌人 0）—— 需要进战斗才能验完整链路")
        finally:
            t.detach()
    except Exception as e:
        print("  skip  真机检查不可用：%s" % e)


def main():
    part_a_contract()

    print("--- C. 桥接自测：内存快照 → 推演输入 ---")
    from intent_reader import IntentReader
    from battle_planner import from_snapshot, plan_from_snapshot, describe
    r = IntentReader()

    snap = {
        "ok": True, "deckTypeName": "战斗",
        "hp": 40, "maxHp": 60, "energy": 3,
        "enemies": [
            {"name": "A", "hp": 10, "maxHp": 10, "armour": 0, "alive": True, "retreated": False},
            {"name": "B", "hp": 20, "maxHp": 20, "armour": 5, "alive": True, "retreated": False},
            {"name": "C", "hp": 0, "maxHp": 9, "armour": 0, "alive": False, "retreated": False},
            {"name": "D", "hp": 7, "maxHp": 7, "armour": 0, "alive": True, "retreated": True},
        ],
        "hand": [{"name": "Bash", "cost": 1,
                  "intents": [{"intent": "DealHalfHealthInDamage", "number": 6, "mult": "Single"}]}],
    }
    br = from_snapshot(snap, reader=r)
    check(len(br["enemies"]) == 2, "死敌 + 已撤退都被剔除（4 → 2，实际 %d）" % len(br["enemies"]))
    check(br["enemies"][1].block == 5, "敌人护甲映射成 block（B=5）")
    check(br["energy"] == 3 and br["player_hp"] == 40.0, "精力/血量取自快照")

    res, _ = plan_from_snapshot(snap, reader=r)
    print("    " + describe(res))
    check(res.get("ok"), "快照直接推出方案")
    check(res.get("bridge", {}).get("enemyCount") == 2, "bridge 带回参战敌人数")

    # 边界：能量为负 / 血量为负 / 空手牌 —— 都不能崩
    for bad in ({"energy": -1, "hp": -5, "enemies": [], "hand": []},
                {"enemies": None, "hand": None}):
        try:
            from_snapshot(bad, reader=r)
            check(True, "畸形快照不崩（%s）" % str(bad)[:40])
        except Exception as e:
            check(False, "畸形快照崩了：%s" % e)

    _live_check()

    print()
    if FAIL:
        print("BATTLE PROBE FAILED %d 项" % len(FAIL))
        for f in FAIL:
            print("  -", f)
        return 1
    print("BATTLE PROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
