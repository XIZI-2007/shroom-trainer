#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""forget_test.py —— 遗忘功能真机验证（会真的删牌！）

用法：
    python tools/forget_test.py --list           # 只列可忘的牌，不删
    python tools/forget_test.py --forget <idx>   # 删掉 forgetList 里第 idx 张（危险）

⚠️ 跑之前先**断开辅助面板**。
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_JS = os.path.join(HERE, "..", "src", "agent.js")


def find_pid():
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Shroom and Gloom.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "ignore")
    except Exception:
        return None
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("shroom"):
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--forget", type=int, default=None)
    ap.add_argument("--diag", action="store_true", help="打印世界管理器/Services 诊断")
    ap.add_argument("--watch", type=int, default=0, help="轮询等待牌库出现（秒）")
    ap.add_argument("--wait", type=int, default=30)
    args = ap.parse_args()

    import frida

    pid = None
    t0 = time.time()
    while time.time() - t0 < args.wait:
        pid = find_pid()
        if pid:
            break
        time.sleep(1.5)
    if not pid:
        print("找不到游戏进程")
        return 2
    print("游戏 PID = %d" % pid)

    src = open(AGENT_JS, encoding="utf-8").read()
    session = frida.attach(pid)
    script = session.create_script(src)
    logs = []
    script.on("message", lambda m, d: logs.append(m))
    script.load()
    for m in logs:
        if m.get("type") == "error":
            print("!! js error:", m.get("description"), m.get("stack", "")[:400])

    init = script.exports_sync.init()
    print("init:", json.dumps(init, ensure_ascii=False))
    if not init.get("ok"):
        return 3

    try:
        if args.diag:
            d = script.exports_sync.diag()
            print("\n===== diag =====")
            print("worldMgrField=%s servicesField=%s" % (d.get("worldMgrField"), d.get("servicesField")))
            print("worldMgrs:", d.get("worldMgrs"))
            svcs = d.get("services") or []
            print("services(%d):" % len(svcs), svcs)
            print("有 S_WorldStateManager:", "S_WorldStateManager" in svcs)

        lst = script.exports_sync.forget_list()

        # --watch：轮询等牌库出现（用户可能还在主菜单）
        if args.watch and not (lst.get("cards") or []):
            print("牌库还没出现，最多等 %d 秒…" % args.watch)
            t0 = time.time()
            while time.time() - t0 < args.watch:
                time.sleep(2)
                lst = script.exports_sync.forget_list()
                if lst.get("cards"):
                    break

        print("\n===== forgetList =====")
        print("ok=%s canForget=%s deckType=%s(%s) total=%d" % (
            lst.get("ok"), lst.get("canForget"), lst.get("deckType"),
            lst.get("deckTypeName"), lst.get("total")))
        print("methodRva=%s rvaOk=%s" % (lst.get("methodRva"), lst.get("methodRvaOk")))
        print("counts:", lst.get("counts"), "warning:", lst.get("warning"), "err:", lst.get("err"))
        cards = lst.get("cards") or []
        for c in cards[:40]:
            print("  [%2d] %-12s %-26s cost=%-4s ptr=%s" % (
                c["idx"], c["stackLabel"], c["name"], c["cost"], c["ptr"][:14]))
        if len(cards) > 40:
            print("  ... 共 %d 张" % len(cards))

        if args.forget is not None:
            tgt = None
            for c in cards:
                if c["idx"] == args.forget:
                    tgt = c
                    break
            if not tgt:
                print("\n没找到 idx=%d" % args.forget)
                return 4
            print("\n===== 准备遗忘 [%d] %s (%s) =====" % (tgt["idx"], tgt["name"], tgt["stackLabel"]))
            r = script.exports_sync.forget(tgt["ptr"], tgt["stack"])
            print("结果:", json.dumps(r, ensure_ascii=False))
            time.sleep(0.4)
            lst2 = script.exports_sync.forget_list()
            print("\n===== 遗忘后的 forgetList =====")
            print("total=%d counts=%s" % (lst2.get("total"), lst2.get("counts")))
            names = [c["name"] for c in (lst2.get("cards") or [])]
            print("目标牌是否还在:", tgt["name"] in names)
    finally:
        try:
            script.unload()
            session.detach()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
