#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""forget_probe.py —— 「遗忘手牌」功能真机标定（只读）

用法：
    python tools/forget_probe.py              # 全部标定（P1-P4）
    python tools/forget_probe.py --fields     # 只跑字段偏移
    python tools/forget_probe.py --methods    # 只跑方法指针
    python tools/forget_probe.py --decks      # 只跑牌库候选
    python tools/forget_probe.py --stacks 12  # 只跑四堆内容

⚠️ 跑之前请先**断开辅助面板**（并最好让游戏停在营地/战斗中）：
   两套 frida il2cpp 钩子互踩有把游戏卡死的实测记录。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
JS_PATH = os.path.join(HERE, "forget_probe.js")


def find_pid():
    import subprocess
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


def hr(title):
    print("\n" + "=" * 68)
    print("  " + title)
    print("=" * 68)


def dump(obj, indent=0):
    print(json.dumps(obj, ensure_ascii=False, indent=indent or 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", action="store_true")
    ap.add_argument("--methods", action="store_true")
    ap.add_argument("--decks", action="store_true")
    ap.add_argument("--stacks", type=int, default=None)
    ap.add_argument("--wait", type=int, default=30, help="等待游戏启动的秒数")
    args = ap.parse_args()

    import frida

    script_src = open(JS_PATH, encoding="utf-8").read()

    pid = None
    t0 = time.time()
    while time.time() - t0 < args.wait:
        pid = find_pid()
        if pid:
            break
        time.sleep(1.5)
    if not pid:
        print("找不到游戏进程 Shroom and Gloom.exe；请先把游戏开起来。")
        return 2

    print("游戏 PID = %d" % pid)
    session = frida.attach(pid)
    script = session.create_script(script_src)
    logs = []
    script.on("message", lambda m, d: logs.append(m))
    script.load()

    if logs:
        hr("脚本日志")
        for m in logs:
            print(json.dumps(m, ensure_ascii=False, indent=2, default=str))

    st = script.exports_sync.init()
    print("init 返回:", json.dumps(st, ensure_ascii=False, indent=2, default=str))
    if not st.get("ok"):
        print("init 失败")
        return 3

    # 收集并已加载说明没有脚本错误
    errs = [m for m in logs if m.get("type") == "error"]
    for e in errs[:5]:
        print("!! js error:", e.get("description"))

    try:
        if args.fields:
            hr("P1  M_Deck<T> 字段偏移")
            r = script.exports_sync.fields()
            dump(r)
        elif args.methods:
            hr("P2  方法指针")
            r = script.exports_sync.methods()
            dump(r)
        elif args.decks:
            hr("P3  牌库候选（营地 / 战斗）")
            r = script.exports_sync.decks()
            dump(r)
        elif args.stacks is not None:
            hr("P4  四堆内容")
            r = script.exports_sync.stacks(args.stacks)
            dump(r)
        else:
            hr("全部标定")
            r = script.exports_sync.probe()
            dump(r)
    finally:
        try:
            script.unload()
            session.detach()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
