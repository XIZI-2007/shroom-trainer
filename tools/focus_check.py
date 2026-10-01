"""真机验证：点面板不抢游戏焦点（WS_EX_NOACTIVATE + WM_MOUSEACTIVATE）。

为什么单开一个脚本：这件事**必须真点鼠标**才有意义 ——
`QTest.mouseClick` 发的是 Qt 合成事件，走不到系统的激活判定那一步，
在离屏自检里怎么测都是"绿"的假通过。所以这里：
  · 起一个真的面板窗口（真 QApplication，不是 offscreen）；
  · 起一个**独立进程**的假游戏窗口（纯 Win32 窗口），把它设为前台；
  · 用 mouse_event 真的按鼠标，然后读 `GetForegroundWindow()` 以及
    **假游戏线程的 hwndActive / hwndFocus**（GetGUIThreadInfo）看有没有被抢走。

⚠️ 假游戏**必须是另一个进程**：早先用同进程的 QWidget 当替身，测出来全绿，
   但用户拿真游戏实测照样掉焦点 —— 同进程时前台根本不会真的切走，测了个寂寞。

⚠️ 这个替身场景**不能替代真机**：真游戏怎么反应只有真游戏知道。所以除了前台，
   这里还断言 **WS_EX_NOACTIVATE 样式真的挂上了** —— 那才是真正起作用的那层。

⚠️ 会短暂移动用户的光标并占用几秒（结束前还原光标位置）。
⚠️ 坐标必须是**物理**像素：Qt 的 mapToGlobal 给的是逻辑坐标，本机
   devicePixelRatio=1.5，拿逻辑坐标去 SetCursorPos 会偏到左上角（踩过）。

用法：python tools/focus_check.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

if sys.platform == "win32":
    # 必须早于 QApplication：否则 Qt 的坐标体系会和 Win32 对不上
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from PyQt6.QtWidgets import QApplication                     # noqa: E402

import trainer_gui as G                                      # noqa: E402

u = ctypes.windll.user32
k = ctypes.windll.kernel32
# ⚠️ 64 位下必须声明 argtypes，否则 HWND 被截断成 32 位 → access violation
u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u.SetWindowPos.restype = ctypes.c_int
u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
u.SetForegroundWindow.restype = ctypes.c_int
u.GetForegroundWindow.restype = ctypes.c_void_p
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
u.GetForegroundWindow.restype = ctypes.c_void_p
k.GetCurrentThreadId.restype = wt.DWORD

fails = []


def check(name, ok, info=""):
    print(("  [OK]  " if ok else "  [FAIL]") + f" {name} {info}")
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------- 假游戏（独立进程）
# 纯 Win32 窗口，不依赖 Qt：起得来就行，我们只关心它的**前台/焦点状态**。
# 它自己**抢不回前台**（进程不是前台进程时 SetForegroundWindow 会被系统拒），
# 所以留了一条 stdin 通道：收到 "FOCUS" 就自己把自己调到前台 —— 由测试进程先用
# AllowSetForegroundWindow 授权，这条路才走得通。
_FAKE_GAME = r'''
import ctypes, ctypes.wintypes as wt, sys, threading
u = ctypes.windll.user32
k = ctypes.windll.kernel32
u.DefWindowProcW.restype = ctypes.c_longlong
u.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint,
                             ctypes.c_void_p, ctypes.c_void_p)
@WNDPROC
def proc(h, m, w, l):
    return u.DefWindowProcW(h, m, w, l)

class WC(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]

hInst = k.GetModuleHandleW(None)
wc = WC()
wc.lpfnWndProc = proc
wc.hInstance = hInst
wc.lpszClassName = "ShroomTrainerFakeGame"
u.RegisterClassW(ctypes.byref(wc))
# WS_OVERLAPPEDWINDOW = 0x00CF0000：有标题栏有边框，和真游戏一样是个正常人窗口
hwnd = u.CreateWindowExW(0, "ShroomTrainerFakeGame", "Shroom and Gloom",
                         0x00CF0000, 1210, 640, 430, 300, None, None, hInst, None)
u.ShowWindow(hwnd, 9)
print(hwnd, flush=True)

def reader():
    for line in sys.stdin:
        if line.strip() == "FOCUS":
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            print("OK", flush=True)

threading.Thread(target=reader, daemon=True).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg))
    u.DispatchMessageW(ctypes.byref(msg))
'''


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", wt.RECT)]


def thread_state(hwnd):
    """某窗口所属线程的 (hwndActive, hwndFocus)。"""
    tid = u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    gi = GUITHREADINFO()
    gi.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not u.GetGUIThreadInfo(tid, ctypes.byref(gi)):
        return 0, 0
    return gi.hwndActive or 0, gi.hwndFocus or 0


app = QApplication(sys.argv)
RATIO = app.primaryScreen().devicePixelRatio()

panel = G.Panel()
panel.show()

# stand-in「游戏」：独立进程，和面板错开，免得互相遮挡
game_proc = subprocess.Popen([sys.executable, "-u", "-c", _FAKE_GAME],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, bufsize=1)
ghwnd = 0
for _ in range(80):
    line = game_proc.stdout.readline().strip()
    if line:
        ghwnd = int(line)
        break
if not ghwnd:
    print("[X] 假游戏窗口没起来")
    game_proc.kill()
    sys.exit(3)

for _ in range(30):
    app.processEvents()
    time.sleep(0.05)

phwnd = int(panel.winId())
# 面板置顶：stand-in 在它下面，点击才会落在面板上
G.set_window_topmost(phwnd, True)

pt = wt.POINT()
u.GetCursorPos(ctypes.byref(pt))
saved = (pt.x, pt.y)


def physical(widget):
    """控件中心的**物理**屏幕坐标。"""
    c = widget.mapToGlobal(widget.rect().center())
    return int(c.x() * RATIO), int(c.y() * RATIO)


def panel_rect():
    r = wt.RECT()
    u.GetWindowRect(ctypes.c_void_p(phwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _within(widget):
    """控件中心是否落在面板窗口内（点击前的前置校验）。"""
    x, y = physical(widget)
    l, t, r, b = panel_rect()
    return l <= x <= r and t <= y <= b


def click_on(widget):
    """点某个控件的中心，并**先确认那一点真的落在面板窗口内**。

    不确认的话，控件若被滚到视口外，算出来的坐标就在窗口外 —— 那一点会点到
    别的窗口上，断言看到"前台变了"还会以为是"抢焦点失败"，实际是根本没点到面板
    （踩过：面板默认只有 350 高，牌库卡和遗忘卡都在折线以下）。
    """
    x, y = physical(widget)
    l, t, r, b = panel_rect()
    if not (l <= x <= r and t <= y <= b):
        raise AssertionError(
            f"控件 {type(widget).__name__} 的中心 {(x, y)} 不在面板窗口 "
            f"{(l, t, r, b)} 内 —— 它多半被滚出视口了，测试要先把它露出来")
    click_at(x, y)
    return x, y


def click_at(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.28)
    u.mouse_event(0x0002, 0, 0, 0, 0)     # LEFTDOWN
    time.sleep(0.06)
    u.mouse_event(0x0004, 0, 0, 0, 0)     # LEFTUP
    settle()


def settle(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.03)


def refocus_game():
    """把 fake-game 弄回前台。两条路，先轻后重。

    **首选**：我们的进程此刻多半就是前台（面板刚显示 / 刚被点），有资格调
    `AllowSetForegroundWindow(目标 pid)` 授权，再让目标进程自己 `SetForegroundWindow`。

    **兜底**：我们要是不在前台，上面那条会被前台锁直接拒掉（实测：连试 8 次全失败，
    脚本只能退出去）。这时用经典的 `AttachThreadInput` —— 把自己挂到**当前前台线程**上，
    系统就把我们视作它的一部分，`SetForegroundWindow` 这才被放行。用完必须 detach
    （否则两个线程的输入队列被绑在一起，后续鼠标事件会互相串）。
    """
    try:
        u.AllowSetForegroundWindow.argtypes = [wt.DWORD]
        u.AllowSetForegroundWindow(game_proc.pid)
        game_proc.stdin.write("FOCUS\n")
        game_proc.stdin.flush()
        game_proc.stdout.readline()
    except Exception:
        pass
    if u.GetForegroundWindow() == ghwnd:
        settle()
        return

    fg = u.GetForegroundWindow() or 0
    try:
        my_tid = k.GetCurrentThreadId()
        fg_tid = u.GetWindowThreadProcessId(ctypes.c_void_p(fg), None) if fg else 0
        if fg and fg_tid and fg_tid != my_tid:
            u.AttachThreadInput(my_tid, fg_tid, True)
            try:
                u.SetForegroundWindow(ctypes.c_void_p(ghwnd))
            finally:
                u.AttachThreadInput(my_tid, fg_tid, False)
        else:
            u.SetForegroundWindow(ctypes.c_void_p(ghwnd))
    except Exception:
        pass
    settle()


print(f"=== 点面板不抢游戏焦点 ===  devicePixelRatio={RATIO}")
print(f"    面板 hwnd={phwnd}  fake-game hwnd={ghwnd}（独立进程 pid={game_proc.pid}）")
# ★ 最关键的一条：真正兜住的是**窗口样式**，不是 WM_MOUSEACTIVATE 的返回值。
#   样式没挂上，后面就算全绿也照样在真机上丢焦点。
check("★ 面板真的挂上了 WS_EX_NOACTIVATE（这才是真机上起作用的那层）",
      G.window_noactivate(phwnd), f"exstyle 含 NOACTIVATE={G.window_noactivate(phwnd)}")
# 前置：必须先把替身推到前台，否则后面「游戏保住了前台」全是拿一个**本来就不是前台**
# 的窗口在比 —— 一路假绿。抢不到是**环境**问题（用户桌面前台被别的窗口占着，
# Windows 的前台锁定会拒绝非前台进程 SetForegroundWindow），不是面板的 bug，
# 但也不能当成通过：直接停下并说清楚。
# 重试是必要的：面板刚 show 的那一瞬我们进程未必有授权资格，过几百毫秒通常就有了。
_fg_ok = False
for _i in range(8):
    refocus_game()
    if u.GetForegroundWindow() == ghwnd:
        _fg_ok = True
        break
    time.sleep(0.3)
check("前置：fake-game 拿到了前台（抢不到 → 本脚本此刻验不了焦点）",
      _fg_ok, f"fg={u.GetForegroundWindow()} 期望={ghwnd}")
if not _fg_ok:
    print("[X] 环境不允许把替身窗口推到前台（前台锁定）。这不是面板的问题，"
          "但此刻所有焦点断言都没有意义。\n"
          "    请点一下面板、让本终端所在窗口保持前台后重跑。")
    game_proc.kill()
    game_proc.wait(timeout=5)
    sys.exit(3)

# 拉高到一屏装下所有卡片，牌库卡 / 遗忘卡才在折线以上，点得到
panel.resize(576, 1000)
settle(20)
check("前置：拉高后牌库卡在窗口内（否则后面的点击会落到别的窗口上）",
      _within(panel.deck_card), str(physical(panel.deck_card)))

# ---- 用例 0：灵敏度自检（**测这个测试本身有没有鉴别力**）----
# 故意摘掉样式再点一次。实测结果（很重要，记下来免得下次又理解错）：前台不是变成
# 面板，而是**变成 NULL** —— 游戏被注销了前台，却没有任何窗口接手。
# 早期一次性真机 diag 在旧版上量到的就是同一个现象：
#   `前台=None`，面板 exstyle 里没有 NOACTIVATE。
# 也就是说用户报的「点面板游戏暂停」= Unity 收到失活 → OnApplicationFocus(false)，
# 而不是"面板把焦点抢过去了"。所以断言写成"游戏**丢了**前台"。
panel._panel_noactivate(False)
settle(6)
refocus_game()
_g0a, _g0f = thread_state(ghwnd)
click_on(panel.deck_card)
# 注意 `or 0`：GetForegroundWindow 的 restype 是 c_void_p，返回 0 时拿到的是 None，
# 不归一的话打印出来是 None，断言里 `== 0` 也永远不成立（踩过）。
_stolen = u.GetForegroundWindow() or 0
print(f"    摘样式后点击：前台 {ghwnd} -> {_stolen}"
      f"（NULL={_stolen == 0} 面板={_stolen == phwnd}）")
check("★ 灵敏度：摘掉样式后点击**确实**会让游戏丢前台（能复现用户报的 bug）",
      _stolen != ghwnd,
      f"fg={_stolen}（0=NULL）游戏={ghwnd}；样式为"
      f"{'True' if G.window_noactivate(phwnd) else 'False'}"
      if _stolen != ghwnd else "摘了样式也抢不走前台 → 本测试无鉴别力，后面的绿灯不作数")
panel._panel_noactivate(True)
settle(6)
check("恢复：重新挂回样式", G.window_noactivate(phwnd))

# ---- 用例 1：点牌库卡（最常用的交互，纯本地 UI）----
refocus_game()
_ga_before, _gf_before = thread_state(ghwnd)
_before = panel.deck_card.isOn()
_xy = click_on(panel.deck_card)
check("★ 点牌库卡：卡片真的响应了（点击没被吃掉）",
      panel.deck_card.isOn() != _before,
      f"{_before} -> {panel.deck_card.isOn()}")
check("★ 点牌库卡：游戏**保住**前台（没被抢焦点）",
      u.GetForegroundWindow() == ghwnd,
      f"fg={u.GetForegroundWindow()} 面板={phwnd}")
_ga_after, _gf_after = thread_state(ghwnd)
check("★ 点牌库卡：游戏线程的 active / focus 也没被夺走（不只是前台没变）",
      (_ga_after, _gf_after) == (_ga_before, _gf_before),
      f"active {_ga_before}->{_ga_after}  focus {_gf_before}->{_gf_after}")
check("★ 点牌库卡之后样式还在（没被哪次点击顺手摘掉）",
      G.window_noactivate(phwnd), f"NOACTIVATE={G.window_noactivate(phwnd)}")

# ---- 用例 2：样式恒挂着 —— 点面板任何一处都不该把它摘掉 ----
# 原来这条测的是「点能量输入框要临时摘掉样式才打得进字」；无限精力功能整体移除后，
# 面板上再无输入控件，那个例外也没了。改成正向断言：连点几处，样式必须始终在。
refocus_game()
for _tgt, _nm in ((panel.cap_god, "无敌胶囊"), (panel.cap_top, "置顶胶囊"),
                  (panel.forget_card, "遗忘卡")):
    click_on(_tgt)
    settle(10)
    check(f"★ 点{_nm}之后样式仍在（已无「输入框例外」可摘）",
          G.window_noactivate(phwnd), f"NOACTIVATE={G.window_noactivate(phwnd)}")
    check(f"★ 点{_nm}之后游戏仍在前台", u.GetForegroundWindow() == ghwnd,
          f"fg={u.GetForegroundWindow()}")

# ---- 用例 2b：仪表盘按钮（开合运行状态页）也不该抢焦点 ----
# 这条单列，因为它是**唯一会改变面板整体版式**的控件：滑入一个新页面。
# 如果哪天有人图省事在开合时抢一下前台「好让页面收到键盘事件」，或给状态页挂上
# 会激活窗口的样式，这里就会红。
refocus_game()
_ga_sheet, _gf_sheet = thread_state(ghwnd)
_was_open = panel.isSheetOpen()          # 自检可能已把它开着，先归一
if _was_open:
    click_on(panel.b_dash)
    settle(20)
check("前置：开合前状态页是关的", not panel.isSheetOpen(), f"open={panel.isSheetOpen()}")
click_on(panel.b_dash)
settle(24)                               # 等滑入动画（SHEET_MS≈260ms）走完
check("★ 点仪表盘按钮：状态页真的打开了（点击没被吃掉）",
      panel.isSheetOpen(), f"open={panel.isSheetOpen()}")
check("★ 点仪表盘按钮：游戏**保住**前台", u.GetForegroundWindow() == ghwnd,
      f"fg={u.GetForegroundWindow()} 面板={phwnd}")
check("★ 点仪表盘按钮：游戏线程 active / focus 也没被夺走",
      thread_state(ghwnd) == (_ga_sheet, _gf_sheet),
      f"active/focus {(_ga_sheet, _gf_sheet)} -> {thread_state(ghwnd)}")
check("★ 点仪表盘按钮之后样式仍在（滑入页面没有顺手摘掉 NOACTIVATE）",
      G.window_noactivate(phwnd), f"NOACTIVATE={G.window_noactivate(phwnd)}")
# 再点一次收起，免得状态页盖住后面的用例（滚轮 / 底部按钮都在它下面）
click_on(panel.b_dash)
settle(24)
check("★ 再点一次：状态页收起、游戏仍在前台",
      (not panel.isSheetOpen()) and u.GetForegroundWindow() == ghwnd,
      f"open={panel.isSheetOpen()} fg={u.GetForegroundWindow()}")

# 拉回最小高度：滚轮用例需要有东西可滚
panel.resize(576, 350)
settle(20)

# ---- 用例 3：滚轮（面板没被激活时也要能滚）----
# ⚠️ 前置：面板默认**不是** always-on-top（置顶是要用户点开关的），桌面上别的窗口
#    随时可能正好盖住它。被盖住时滚轮打在别人身上 —— 现象是 scroll 纹丝不动、
#    fg 变成一个陌生 hwnd（本仓库实测撞到过）。所以这里测试期间临时置顶，
#    并校验"光标下的窗口就是面板"，免得测了个别窗口还以为是面板的问题。
G.set_window_topmost(phwnd, True)
settle(16)
refocus_game()
panel._scroll_to(0, smooth=False)
settle()
_xy = physical(panel.viewport)
u.SetCursorPos(*_xy)
time.sleep(0.25)
u.WindowFromPoint.argtypes = [wt.POINT]
u.WindowFromPoint.restype = ctypes.c_void_p
_hit = u.WindowFromPoint(wt.POINT(*_xy))
check("前置：光标位置上的窗口就是面板（否则本用例测的是别的窗口）",
      _hit == phwnd, f"WindowFromPoint={_hit} 面板={phwnd}")
for _ in range(5):
    u.mouse_event(0x0800, 0, 0, -120, 0)      # MOUSEEVENTF_WHEEL 下滚
    time.sleep(0.08)
settle()
check("★ 面板上滚轮能滚动（滚动条位置变了）",
      panel._scroll > 0, f"scroll={panel._scroll:.0f}")
check("★ 滚轮也没抢走游戏前台", u.GetForegroundWindow() == ghwnd,
      f"fg={u.GetForegroundWindow()}")

# ---- 用例 4：面板上的按钮（底部「启动游戏」是 NoFocus 按钮）----
refocus_game()
_xy = physical(panel.b_detach)     # 断开：未连接时是安全的空操作
click_at(*_xy)
check("★ 点底部按钮不抢焦点", u.GetForegroundWindow() == ghwnd,
      f"fg={u.GetForegroundWindow()}")
check("★ 点底部按钮后 fake-game 线程状态也没变",
      thread_state(ghwnd) == (_ga_before, _gf_before),
      f"{thread_state(ghwnd)} vs {(_ga_before, _gf_before)}")

G.set_window_topmost(phwnd, False)      # 收尾：把测试期间临时加的置顶撤掉
u.SetCursorPos(saved[0], saved[1])
panel.close()
game_proc.kill()
game_proc.wait(timeout=5)
app.processEvents()

print("\n=== 结果 ===")
if fails:
    print(f"失败 {len(fails)} 项: {fails}")
    sys.exit(1)
print("全部通过")
