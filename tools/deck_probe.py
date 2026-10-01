# -*- coding: utf-8 -*-
"""
deck_probe.py —— 牌库顺序标定探针的 Python 端（只读）

用法：
    python deck_probe.py [分钟数]        # 标定采样：等游戏起来，400ms 一采，自动对账
    python deck_probe.py --agent         # 实测出货文件 src/agent.js 的 deck() RPC

流程：
  1. 轮询等待 "Shroom and Gloom.exe" 启动
  2. attach 并加载 deck_probe.js（只读，不挂钩）
  3. 每 400ms 采一次快照，牌库/手牌发生变化时打印对账信息
  4. 采样写入 _probe_log.jsonl，便于事后分析
"""

import json
import os
import sys
import time
from datetime import datetime

import frida

PROC = "Shroom and Gloom.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
JS_PATH = os.path.join(HERE, "deck_probe.js")
LOG_PATH = os.path.join(HERE, "_probe_log.jsonl")
SAMPLE_MS = 400

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


def wait_process(timeout_s=900):
    dev = frida.get_local_device()
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            for p in dev.enumerate_processes():
                if p.name.lower() == PROC.lower():
                    log(f"找到进程 pid={p.pid}，attach 中…")
                    return dev.attach(p.pid)
        except Exception as e:
            log(f"枚举进程失败：{e}")
        time.sleep(2)
    raise SystemExit("超时：未找到游戏进程，请先启动游戏")


def short(names, limit=40):
    if names is None:
        return "-"
    if len(names) <= limit:
        return "[" + ", ".join(names) + "]"
    return "[" + ", ".join(names[:limit]) + f", …(+{len(names) - limit})]"


def describe_move(old, new):
    """判断牌库减少的牌是在头部、尾部还是中间"""
    if old is None or new is None:
        return None
    if len(new) >= len(old):
        return None
    n = len(old) - len(new)
    # 头部取牌：原列表去掉最前 n 张后，剩余部分应与新列表完全一致
    if old[n:] == list(new):
        return ("头部", old[:n])
    # 尾部取牌：原列表最前 len(new) 张应与新列表一致
    if old[:len(new)] == list(new):
        return ("尾部", old[len(new):])
    # 逐元素找第一个不同点，粗略报告
    removed = []
    j = 0
    for x in old:
        if j < len(new) and x == new[j]:
            j += 1
        else:
            removed.append(x)
    return ("中间/乱序", removed)


def agent_rpc_probe(rounds=5):
    """用**出货的那份 src/agent.js** 实测 deck() RPC（不是探针版）。

    探针只证明「这套结构读得出来」；这里证明「打包进 exe 的这份代码真能读出来」——
    两者字段虽同源，但出货文件里多了缓存、异常兜底、UI 需要的计数，得单独跑一遍。
    """
    js = open(os.path.join(os.path.dirname(HERE), "src", "agent.js"), encoding="utf-8").read()
    sess = wait_process(timeout_s=180)
    script = sess.create_script(js)
    script.load()
    errs = []
    script.on("message", lambda m, d: errs.append(m))
    log("init: " + json.dumps(script.exports_sync.init(), ensure_ascii=False)[:400])
    for i in range(rounds):
        d = script.exports_sync.deck() or {}
        names = d.get("draw") or []
        log(f"[{i}] ok={d.get('ok')}  抽牌堆 {len(names)} 张 {short(names, 12)}  "
            f"弃牌={d.get('discard')} 消耗={d.get('exhaust')} "
            f"手牌={d.get('hand')}/{d.get('handSize')}  warn={d.get('warning')!r}")
        time.sleep(1.2)
    log(f"JS 错误 {len(errs)} 条" if errs else "无 JS 错误")
    log("肉眼核对：抽牌堆第 1 项 = 下一张要抽的牌（打出牌后它应依次减少）")
    try:
        script.unload()
        sess.detach()
    except Exception:
        pass


def card_props_probe():
    """一次性：dump 若干张 GameplayCardData 的 Properties / 描述，标定费用属性名。

    只在**没有**面板挂着游戏的时候用（探针只读、不挂钩，但同一进程里跑两套 frida
    的 il2cpp 调用依然不保险）。用法：python tools/deck_probe.py --cardprops
    """
    with open(JS_PATH, encoding="utf-8") as f:
        src = f.read()
    sess = wait_process(timeout_s=180)
    script = sess.create_script(src)
    script.load()
    script.on("message", lambda m, d: log("JS 错误：" + str(m.get("description"))[:300]))
    for _ in range(40):
        try:
            if script.exports_sync.diag().get("ready"):
                break
        except Exception:
            pass
        time.sleep(0.25)
    res = script.exports_sync.cardprops(8)
    if res.get("err"):
        log("失败：" + res["err"])
    for c in res.get("cards") or []:
        log("=" * 60)
        log("牌名: " + repr(c.get("name")))
        for p in c.get("props") or []:
            if "_assetName" in p:
                log("   资产名: " + repr(p["_assetName"]))
            else:
                log(f"   属性 {p.get('prop')!r} = {p.get('number')} (max {p.get('max')})")
        log("   CustomDescription   : " + repr(c.get("customDesc")))
        log("   CustomDescriptionLoc: " + repr(c.get("customDescLoc")))
    try:
        script.unload()
        sess.detach()
    except Exception:
        pass


def main():
    if "--cardprops" in sys.argv:
        card_props_probe()
        return
    if "--agent" in sys.argv:
        agent_rpc_probe()
        return
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    with open(JS_PATH, encoding="utf-8") as f:
        src = f.read()

    sess = wait_process()
    script = sess.create_script(src)

    js_errors = []

    def on_message(msg, data):
        if msg.get("type") == "error":
            js_errors.append(msg)
            log("JS 错误：" + str(msg.get("description"))[:400])
        elif msg.get("type") == "send":
            log("JS: " + str(msg.get("payload"))[:400])

    script.on("message", on_message)
    script.load()
    log("脚本已加载")

    # 等 ready
    diag = None
    for _ in range(40):
        try:
            diag = script.exports_sync.diag()
            if diag.get("ready"):
                break
        except Exception as e:
            log(f"diag 失败：{e}")
        time.sleep(0.5)
    log("初始化信息：" + json.dumps(diag, ensure_ascii=False))

    logf = open(LOG_PATH, "w", encoding="utf-8")
    prev = None
    prev_line = ""
    last_state_note = ""
    t_end = time.time() + minutes * 60
    deck_not_found_noted = False

    while time.time() < t_end:
        try:
            cur = script.exports_sync.snapshot()
        except Exception as e:
            log(f"snapshot 失败：{e}")
            time.sleep(1.0)
            continue

        logf.write(json.dumps({"t": ts(), "snap": cur}, ensure_ascii=False) + "\n")
        logf.flush()

        if not cur.get("ok"):
            note = cur.get("err") or "未知状态"
            if note != last_state_note:
                log(f"待机：{note}")
                last_state_note = note
            if not deck_not_found_noted:
                deck_not_found_noted = True
            time.sleep(SAMPLE_MS / 1000.0)
            continue

        deck = cur.get("deck")
        hand = cur.get("hand")

        line = json.dumps({
            "d": deck["draw"] if deck else None,
            "dc": deck["discardsCount"] if deck else None,
            "ec": deck["exhaustedCount"] if deck else None,
            "h": hand["cards"] if hand else None,
            "hs": hand["handSize"] if hand else None,
        }, ensure_ascii=False)

        if line != prev_line:
            # 首次
            if prev is None:
                log("首次快照")
                if deck:
                    log(f"  牌库实例 {deck['ptr']} ({deck['klass']}) DeckType={deck['deckType']}")
                    log(f"  抽牌堆 {deck['drawCount']} 张：{short(deck['draw'])}")
                    log(f"  弃牌堆 {deck['discardsCount']} 张  消耗堆 {deck['exhaustedCount']} 张  "
                        f"置旁 {deck['setAsideCount']} 张  牌库顶覆盖 {deck['topOfDrawPileCount']} 张")
                if hand:
                    log(f"  手牌 {hand['count']}/{hand['handSize']}：{short(hand['cards'])}")
                log("  字段偏移：" + json.dumps(cur.get("offsets", {}), ensure_ascii=False))
            else:
                log("—" * 30)
                pd = (prev or {}).get("deck")
                ph = (prev or {}).get("hand")
                if deck and pd:
                    if deck["draw"] != pd["draw"]:
                        mv = describe_move(pd["draw"], deck["draw"])
                        if mv:
                            log(f"牌库 {len(pd['draw'])} → {deck['drawCount']} 张  "
                                f"移除位置={mv[0]}  移除={short(mv[1])}")
                        else:
                            log(f"牌库 {len(pd['draw'])} → {deck['drawCount']} 张（数量未减，可能整体洗牌）")
                        log(f"  新牌库顺序：{short(deck['draw'])}")
                    if deck["discardsCount"] != pd["discardsCount"]:
                        log(f"弃牌堆 {pd['discardsCount']} → {deck['discardsCount']}  当前={short(deck['discards'])}")
                    if deck["exhaustedCount"] != pd["exhaustedCount"]:
                        log(f"消耗堆 {pd['exhaustedCount']} → {deck['exhaustedCount']}")
                    if deck["topOfDrawPileCount"] != pd["topOfDrawPileCount"]:
                        log(f"牌库顶覆盖 {pd['topOfDrawPileCount']} → {deck['topOfDrawPileCount']}  "
                            f"{short(deck['topOfDrawPile'])}")
                if hand and ph:
                    if hand["cards"] != ph["cards"]:
                        added = [c for c in hand["cards"] if c not in ph["cards"]]
                        removed = [c for c in ph["cards"] if c not in hand["cards"]]
                        log(f"手牌 {ph['count']} → {hand['count']}  "
                            f"新增={short(added)}  移除={short(removed)}")
                if hand and ph and hand["handSize"] != ph["handSize"]:
                    log(f"手牌上限 {ph['handSize']} → {hand['handSize']}")

            prev = cur
            prev_line = line

        time.sleep(SAMPLE_MS / 1000.0)

    logf.close()
    log(f"采样结束，日志：{LOG_PATH}")
    if js_errors:
        log(f"期间有 {len(js_errors)} 条 JS 错误")
    try:
        script.unload()
        sess.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
