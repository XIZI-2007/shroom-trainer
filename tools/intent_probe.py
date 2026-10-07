# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""intent_probe.py —— 「卡牌效果」标定探针的 Python 端（只读）

用法：
    python tools/intent_probe.py                # 自检 + 采样若干张牌，打印 (意图, 数值, 目标)
    python tools/intent_probe.py --n 12         # 采样 12 张

前置：**游戏必须已经在运行**（战斗或探索场景皆可，探索场景手牌也有牌）。
      ⚠️ 跑前请先断开辅助面板 —— Frida 只允许一个 session 注入同一进程，
         且**绝不**在同一进程里同时挂出货 agent.js（带 Interceptor，会卡死游戏）。

本探针要回答的问题（决定后续「最优打法」可行性）：
  1. `GameplayCardData.<Intents>` @0x148 这个硬编码偏移**在真机上对不对**？
  2. `CardIntentPair` 的 (意图, 数值, 目标数) 能否稳定读出？
  3. 意图的**名字**是只有 guid/assetIndex，还是能直接拿到？（决定要不要查表）
"""

import json
import os
import sys
import time

import frida

PROC = "Shroom and Gloom.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS_PATH = os.path.join(HERE, "intent_probe.js")
DICT_PATH = os.path.join(ROOT, "src", "card_intent_dict.json")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(msg, flush=True)


def wait_process(timeout_s=300):
    """等游戏进程起来并 attach。

    ⚠️ 用 Win32 快照（trainer_core.list_processes）而不是 Frida 的 enumerate_processes：
    本机（frida 17.18 / Win11）Frida 枚举会被系统过滤，游戏和 steam 都在缺的那批里。
    """
    sys.path.insert(0, ROOT)
    dev = frida.get_local_device()
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        pid = None
        try:
            # ⚠️ list_processes() 返回的是 **[(pid, 完整镜像路径)]** 元组列表，
            #    不是 dict —— 早先这里写成 p.get("path")，异常被 except 吞掉后
            #    一路静默到超时（踩过）。
            from trainer_core import list_processes
            for p in list_processes():
                pid_i, path_i = p[0], p[1]
                if os.path.basename(path_i or "").lower() == PROC.lower():
                    pid = pid_i
                    break
        except Exception as e:
            last = "list_processes 失败：%s" % e
        if pid is None:
            last = "未在进程快照里看到 %s" % PROC
            time.sleep(2)
            continue
        try:
            log("找到进程 pid=%d，attach 中…" % pid)
            return dev.attach(pid)
        except Exception as e:
            last = "attach 失败：%s" % e
            time.sleep(2)
    raise SystemExit("超时：%s（%s）" % ("未找到游戏进程，请先启动游戏", last))


def load_dict():
    if not os.path.isfile(DICT_PATH):
        return {}
    d = json.load(open(DICT_PATH, encoding="utf-8"))
    return d


def load_enum_names():
    """离线权威成员表（metadata 的 get_XXX 序列），用于与运行时名表交叉校验。"""
    p = os.path.join(ROOT, "src", "card_intent_enum.json")
    if not os.path.isfile(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    m = d.get("members") if isinstance(d, dict) else d
    if not isinstance(m, list):
        return []
    out = []
    for x in m:
        out.append(x.get("name") if isinstance(x, dict) else x)
    return [x for x in out if x]


def get_reader():
    """卡牌效果理解层（把意图变成人话与可算的数）。缺文件就返回 None，探针照跑。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from intent_reader import IntentReader
        return IntentReader()
    except Exception as e:
        log("（理解层未加载：%s —— 只打印原始意图）" % e)
        return None


def show_battle(script, reader):
    """读**真机战况**（敌人血量/精力/手牌）并直接推演 —— 阶段 3 的完整闭环。

    这是本轮新增的能力：不再用假定值，敌人血量、精力、手牌全部来自内存。
    """
    log("")
    log("=" * 72)
    log("真机战况读取 + 打法推演（敌人血量/精力/手牌全部读自内存）")
    log("=" * 72)
    try:
        snap = script.exports_sync.battle(True)      # ⚠️ camelCase，withIntents=True
    except Exception as e:
        log("battle() 调用失败：%s" % e)
        return

    log("牌库类型：%s   手牌 %s/%s   敌人 %s 只（活着 %s）"
        % (snap.get("deckTypeName") or "-", snap.get("handCount"), snap.get("handSize"),
           snap.get("enemyCount"), snap.get("aliveEnemyCount")))
    log("玩家：血 %s/%s   精力 %s" % (snap.get("hp"), snap.get("maxHp"), snap.get("energy")))
    if snap.get("warning"):
        log("⚠️ " + str(snap["warning"]))

    log("")
    log("敌人：")
    for e in snap.get("enemies") or []:
        mark = "阵亡" if not e.get("alive") else ("已逃" if e.get("retreated") else "存活")
        log("  %-14s 血 %4s/%-4s 护甲 %-3s %s%s"
            % (reader.card_name(e.get("name") or "?") if reader else e.get("name"),
               e.get("hp"), e.get("maxHp"), e.get("armour"), mark,
               "  [BOSS]" if e.get("boss") else ""))

    log("")
    log("手牌：")
    for c in snap.get("hand") or []:
        raw = c.get("name") or ""
        zh = reader.card_name(raw) if reader else raw
        summ = reader.summary(c.get("intents") or [], cost=c.get("cost", -1)) if reader else None
        log("  %-22s 费%-3s %s" % (zh, c.get("cost"),
                                   summ["text"] if summ else "(未读效果)"))

    if reader is None:
        log("（理解层未加载，跳过推演）")
        return
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from battle_planner import describe, plan_from_snapshot
    except Exception as e:
        log("（推演引擎未加载：%s）" % e)
        return

    log("")
    res, br = plan_from_snapshot(snap, reader=reader)
    log("=" * 72)
    log("推论：" + describe(res))
    if res.get("ok"):
        log("（%s 求解，置信度 %s，节点 %s）"
            % (res["method"], res["confidence"], res["nodes"]))
        for a in res.get("assumptions") or []:
            log("  · " + a)
    log("=" * 72)


def demo_planner(reader, cards, energy=3, enemy_hp=60):
    """用探针读到的真牌跑一遍"最优打法"推演（阶段 3 的端到端演示）。

    ⚠️ 这条是**离线演示**（敌人血量/精力是假定值）；
       真机要用 show_battle()，那里全部读自内存。
    """
    if reader is None:
        return
    try:
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from battle_planner import Enemy, plan, describe, card as mkcard
    except Exception as e:
        log("（推演引擎未加载：%s）" % e)
        return

    hand = []
    for c in cards[:5]:
        raw = c.get("name") or ""
        summ = reader.summary(c.get("intents") or [], cost=c.get("cost", -1))
        if not summ["items"]:
            continue
        hand.append(mkcard(reader.card_name(raw), c.get("cost") or 0, summ))
    if not hand:
        log("（没有可用手牌，跳过推演）")
        return

    log("")
    log("=" * 72)
    log("打法推演演示（精力 %d / 假定敌人 %d 血 —— 只为打通链路）" % (energy, enemy_hp))
    log("=" * 72)
    for c in hand:
        log("  手牌 %-22s 费%2d  %s" % (c["name"], c["cost"], c["summary"]["text"]))
    res = plan(hand, energy=energy, enemies=[Enemy(hp=enemy_hp, name="靶子")], player_hp=60)
    log("  → " + describe(res))
    if res.get("ok"):
        log("  （%s 求解，%s）" % (res["method"], res["confidence"]))
        for a in res["assumptions"]:
            log("    · " + a)


def main():
    n = 12
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])

    src = open(JS_PATH, encoding="utf-8").read()
    sess = wait_process()
    script = sess.create_script(src)
    errors = []
    script.on("message", lambda m, d: (
        errors.append(m), log("JS 错误：" + str(m.get("description"))[:400])
    ) if m.get("type") == "error" else None)
    script.load()

    diag = script.exports_sync.selfcheck()
    log("自检：" + json.dumps(diag, ensure_ascii=False))
    if not diag.get("ok"):
        log("自检失败，探针无法工作")
        return

    # ── 交叉校验：运行时静态字段名表 vs 离线 metadata 权威表 ──
    log("")
    log("=" * 72)
    log("意图名表交叉校验（运行时静态字段 vs metadata 的 get_XXX）")
    log("=" * 72)
    try:
        # ⚠️ Frida 的 RPC 导出名是 camelCase（intentNames），Python 端不会自动
        #    转下划线 —— 早先这里写 intent_names()，一调就 AttributeError（踩过）。
        rt = script.exports_sync.intentNames()
        names = rt.get("names") or []
        info = rt.get("info") or {}
        log("静态字段 %s 个，其中 __ 前缀可读 %s 个，建表 %d 个  err=%s"
            % (info.get("fields"), info.get("statics"), len(names), info.get("err") or "-"))
        log("样例：" + ", ".join(names[:8]))
        offline = load_enum_names()
        if offline:
            miss = [x for x in offline if x not in set(names)]
            extra = [x for x in names if x not in set(offline)]
            log("离线表 %d 个 → 运行时命中 %d 个（%.0f%%）"
                % (len(offline), len(offline) - len(miss),
                   100.0 * (len(offline) - len(miss)) / max(1, len(offline))))
            if miss:
                log("  运行时缺：" + ", ".join(miss[:12]))
            if extra:
                log("  运行时多（dump 独有，正常）：" + ", ".join(extra[:12]))
    except Exception as e:
        log("交叉校验失败：%s" % e)

    log("")
    log("=" * 72)
    log("读取 GameplayCardData 的意图列表示例（%d 张）" % n)
    log("=" * 72)

    reader = get_reader()
    res = script.exports_sync.sample(n) if hasattr(script.exports_sync, "sample") else None
    if res is None:
        log("探针未提供 sample()，请先补上；以下是 selfcheck 结果")
    else:
        log("扫到 GameplayCardData 实例 %s 个，其中 %d 张有意图"
            % (res.get("scanned"), len(res.get("cards") or [])))
        for c in res.get("cards") or []:
            log("-" * 72)
            raw_name = c.get("name") or ""
            zh = reader.card_name(raw_name) if reader else raw_name
            log("牌名: %r（%s）  ptr=%s" % (raw_name, zh, c.get("ptr")))
            if reader:
                summ = reader.summary(c.get("intents") or [], cost=c.get("cost", -1))
                if summ["text"]:
                    log("  效果: %s" % summ["text"])
                log("  折算: 伤害 %d / 护甲 %d / 治疗 %d / 抽牌 %d / 精力 %d   评分 %.1f"
                    % (summ["deal"], summ["shield"], summ["heal"], summ["draw"],
                       summ["energy"], reader.score(summ)))
                if summ["unknown"]:
                    log("  未识别意图: %s" % ", ".join(summ["unknown"]))
            for it in c.get("intents") or []:
                log("   %-24s x%-4s 目标=%-11s mult=%-11s override=%-3s adv=%s armour(n/s)=%s/%s"
                    % (it.get("intent") or ('<' + (it.get("guid") or '')[:8] + '>'),
                       it.get("number"), it.get("allowedTargets"), it.get("mult"),
                       it.get("targetOverride"), it.get("adv"),
                       it.get("negateArmour"), it.get("skipArmour")))

        demo_planner(reader, res.get("cards") or [])

    # 真机战况 → 推演（敌人血量/精力/手牌全部读自内存）
    show_battle(script, reader)

    log("")
    log("JS 错误 %d 条" % len(errors))
    try:
        script.unload()
        sess.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
