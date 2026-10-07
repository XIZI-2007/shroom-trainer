# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""连接诊断：绕过 GUI，直接走 GameTrainer 的 attach 链路，把真实报错打出来。

用法（游戏进到存档后再跑）：
  "<python>" tools/diag_attach.py
"""
import os
import sys
import json
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import frida
import trainer_core as tc

print("=" * 60)
print("1. frida 版本:", frida.__version__)

print("2. 找游戏目录…")
d = tc.find_game_dir()
print("   ->", d)
print("   agent.js:", tc.AGENT_JS, "存在:", os.path.isfile(tc.AGENT_JS))
print("   i18n:", tc.CARD_I18N_JSON, "存在:", os.path.isfile(tc.CARD_I18N_JSON))

print("3. 枚举进程…")
dev = frida.get_local_device()

# ⚠️ Frida 的 enumerate_processes 在本机会被过滤（只看到 ~120/293），游戏在其中会漏掉。
#    这里同时列出来做**对照**，但判定一律走 tc.list_processes()（Win32 快照）。
try:
    fprocs = dev.enumerate_processes()
    print("   frida 枚举:", len(fprocs), "个")
    fhits = [p for p in fprocs if "shroom" in p.name.lower() or "gloom" in p.name.lower()]
    print("   frida 里的游戏:", [(p.pid, p.name) for p in fhits] or "（无 —— 正是被过滤的证据）")
except Exception as e:
    print("   frida 枚举失败:", e)

wprocs = tc.list_processes()
print("   Win32 快照:", len(wprocs), "个")
hits = [(pid, full) for pid, full in wprocs
        if "shroom" in os.path.basename(full).lower() or "gloom" in os.path.basename(full).lower()]
print("   Win32 里的游戏:", hits)
if not hits:
    print("   ✗ 没找到游戏进程 —— 先把游戏启动到存档里")
    sys.exit(2)

print("4. 精确匹配 GAME_EXE =", repr(tc.GAME_EXE))
exact = [pid for pid, full in wprocs if os.path.basename(full).lower() == tc.GAME_EXE.lower()]
print("   精确命中:", exact)
if not exact:
    print("   ✗ 名字对不上！实际进程名在上面的 hits 里，需要改 GAME_EXE")
    sys.exit(3)

print("4b. find_pid()（生产代码走的就是这条）…")
print("   ->", tc.GameTrainer().find_pid())

print("5. 尝试 attach + load script…")
t = tc.GameTrainer()
try:
    pid = t.attach()
    print("   ✓ attach 成功 pid =", pid)
except Exception as e:
    print("   ✗ attach 失败:", type(e).__name__, e)
    traceback.print_exc()
    sys.exit(4)

print("6. 调 init()…")
try:
    print("   init ->", t.script.exports_sync.init())
except Exception as e:
    print("   ✗ init 失败:", type(e).__name__, e)
    traceback.print_exc()
    sys.exit(5)

print("7. 读 status()…")
try:
    st = t.status()
    print("   status ->", st)
except Exception as e:
    print("   ✗ status 失败:", type(e).__name__, e)
    traceback.print_exc()
    sys.exit(6)

print("8. 挂钩健康检查（hook 全 OK 才算真连上）…")
hooks = (st or {}).get("hooks") or {}
bad = [k for k, v in hooks.items() if v == "FAILED"]
for k, v in hooks.items():
    print("   %-12s %s" % (k, v))
if bad:
    print("   ✗ 以下挂钩失败（无敌/不消耗精力会失效）:", bad)
    try:
        d = t.script.exports_sync.diag()
        print("   trampoline:", json.dumps(d.get("trampoline", {}), ensure_ascii=False))
        print("   resolved  :", json.dumps(d.get("resolved", {}), ensure_ascii=False))
    except Exception as e:
        print("   diag 失败:", e)
    print("   → 若 trampoline 里 hooked=True 且 destInModule=False，多为上一次残留 hook。")
    print("     **重启游戏**即可恢复（游戏进程内存里被改坏的序言无法从代码侧复原）。")
    sys.exit(7)

print("=" * 60)
print("全部通过 —— 链路本身没问题，挂钩 5/5 OK")
t.detach()
