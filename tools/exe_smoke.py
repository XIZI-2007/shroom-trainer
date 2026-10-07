"""真机冒烟：把打包好的 exe 拉起来，确认窗口起得来、尺寸对、能拉伸、能截图，再关掉。

必须在**同一条命令内**完成「启动 + 等待 + 检查 + 拉伸 + 截图 + 关闭」：exe 是被当前
shell 生出来的子进程，父命令一结束就可能被回收，分开两步会查不到进程。

坑：
- PyInstaller onefile 的 exe 会把真正的应用跑在**子进程**里（bootloader 父进程只是等待），
  所以按 ``Popen`` 拿到的 pid 去找窗口是找不到的，得按窗口标题匹配。
- 屏幕抓图在 150% 缩放下返回的是物理像素，验收要按缩放折算回逻辑尺寸。
"""
import ctypes
import ctypes.wintypes as wt
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "dist", "ShroomTrainer.exe")
SHOT = os.path.join(HERE, "exe_shot.png")
SHOT_TALL = os.path.join(HERE, "exe_shot_tall.png")
SHOT_SHEET = os.path.join(HERE, "exe_shot_sheet.png")
SHOT_SHEET_DRAG = os.path.join(HERE, "exe_shot_sheet_drag.png")
SHOT_THEME = os.path.join(HERE, "exe_shot_theme.png")
# 截图默认不写：交付只留最终 exe，中间产物别堆一地。
# 需要肉眼核对时：SHROOM_UI_SHOT=1 python tools/exe_smoke.py
WRITE_SHOT = os.environ.get("SHROOM_UI_SHOT") == "1"
# 必须用**面板的完整标题**：早先只按 "Shroom" 模糊匹配，结果抓到了游戏自己的窗口
# "Shroom and Gloom"，而游戏卡死时那个窗口根本不响应 —— grabWindow 直接挂住，
# 后续断言全是拿 237x39 的鬼窗口在量。面板和游戏同名前缀，这里不能省。
TITLE_KEY = "Shroom & Gloom by-XIZI"
WAIT_S = 40
SWP_NOMOVE, SWP_NOZORDER = 0x0002, 0x0004
GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x00000008

fails = []


def check(name, ok, info=""):
    print(("  [OK]  " if ok else "  [FAIL]") + f" {name} {info}")
    if not ok:
        fails.append(name)


if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title_of(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def visible_windows():
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = title_of(hwnd)
            if t:
                out.append((hwnd, t, pid_of(hwnd)))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def is_topmost(hwnd):
    """读窗口真实 exstyle —— 不看界面，只看系统怎么说。"""
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    return bool(user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def cleanup():
    # taskkill 在中文 Windows 上按 GBK 输出，别让解码把 reader 线程打挂
    subprocess.run(["taskkill", "/F", "/IM", "ShroomTrainer.exe"],
                   capture_output=True, text=True, encoding="utf-8", errors="ignore")


cleanup()   # 先清掉可能残留的旧实例，免得误判
print(f"[1] 启动 {EXE}")
t0 = time.time()
env = dict(os.environ)
env.pop("SHROOM_AUTO_ATTACH", None)
env["SHROOM_AUTO_TOP"] = "1"      # 让面板自己打开「窗口置顶」，好在外部验证真生效
# ⚠️ 把皮肤钉在浅色：面板会读「上次选的皮肤」，机器上要是存过 dark，启动就是暗的 ——
#    而 §7/§8 的判据是「底部像素红分量 < 220」（浅色面板底 r≈245 / 液体色 r≈157-183）。
#    暗色下面板底 r≈21，那条断言会**无声地假通过**（暗色下什么都 < 220）。
#    皮肤读取本身在 ui_check 里做了 round-trip 验证，这里只关心界面行为，钉死即可。
env["SHROOM_THEME"] = "light"
proc = subprocess.Popen([EXE], cwd=ROOT, env=env)

target = None
deadline = t0 + WAIT_S
while time.time() < deadline:
    time.sleep(0.6)
    if proc.poll() is not None:
        print(f"    bootloader 已退出 code={proc.returncode}（子进程接了活）")
    for hwnd, t, pid in visible_windows():
        if TITLE_KEY in t:
            target = (hwnd, t, pid)
            break
    if target:
        break

if not target:
    print("[X] 超时未见窗口。当前可见窗口样本：")
    for hwnd, t, pid in visible_windows()[:15]:
        print(f"      hwnd={hwnd} pid={pid} title={t!r}")
    cleanup()
    sys.exit(3)

hwnd, title, pid = target
print(f"[2] 窗口 ok  用时={time.time() - t0:.1f}s  hwnd={hwnd}  pid={pid}  title={title!r}")

# ---- [2c] 打包内容核对 ----
# onefile 运行时会把 datas 解到 %TEMP%\_MEIxxxx，趁进程活着直接核对解出来的 agent.js。
# 打包时漏掉或带了旧版 agent.js，界面正常但功能静默失效 —— 这里必须拦住。
import glob

# 只认**本次启动之后**才出现/刷新的解包目录：%TEMP% 里常留着一堆陈旧 _MEI*，
# 早先就是捡到旧副本，导致"打包内容核对"比的是上一版文件、结论还显示一致。
# 所以用启动时刻过滤，并显式报出用的是哪个目录。
_mei_dir = os.path.join(os.environ.get("TEMP", "/tmp"))
_fresh = [d for d in glob.glob(os.path.join(_mei_dir, "_MEI*"))
          if os.path.getmtime(d) >= t0 - 3]
print(f"     本次启动后的解包目录 {len(_fresh)} 个")


def packed_copy(fname):
    hits = [os.path.join(d, fname) for d in _fresh
            if os.path.isfile(os.path.join(d, fname))]
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


for fname in ("agent.js", "i18n_cards.json"):
    src_txt = open(os.path.join(ROOT, "src", fname), encoding="utf-8").read()
    hit = packed_copy(fname)
    check(f"exe 把 {fname} 带进去了", hit is not None, str(hit))
    if hit:
        packed = open(hit, encoding="utf-8", errors="replace").read()
        # 注意：这里比的是**字符数**（中文 UTF-8 是多字节，字节数对不上很正常）
        check(f"打包的 {fname} 与源码逐字节一致（没打旧包）", packed == src_txt,
              f"{len(packed)} 字符 vs {len(src_txt)} 字符")

src_js = open(os.path.join(ROOT, "src", "agent.js"), encoding="utf-8").read()
_js = packed_copy("agent.js")
if _js:
    packed = open(_js, encoding="utf-8", errors="replace").read()
    check("打包的 agent.js 含牌库读取（deckSnapshot + pickDeck + deck RPC）",
          "function deckSnapshot" in packed and "function pickDeck" in packed
          and "deck()" in packed,
          f"deckSnapshot={'function deckSnapshot' in packed} "
          f"pickDeck={'function pickDeck' in packed}")
    check("★ 打包的 agent.js 含第二版的费用/效果读取（cardInfo + cardProperties + cardCost）",
          "function cardInfo" in packed and "function cardProperties" in packed
          and "function cardCost" in packed and "COST_PROP_NAMES" in packed,
          f"cardInfo={'function cardInfo' in packed} "
          f"cardProperties={'function cardProperties' in packed} "
          f"cardCost={'function cardCost' in packed}")
    check("★ deckSnapshot 走的是带费用/效果的 listOfInfos（不是旧的 listOfNames）",
          "listOfInfos(deck, '_normalShuffledDrawCards')" in packed,
          "listOfInfos" if "listOfInfos" in packed else "NOT FOUND")
    # 遗忘手牌：必须打进去的是 **List 直改**那一版。
    # 如果打包里还留着 NativeFunction 直调 RemoveCard 的老实现，真机点「遗忘」会崩，
    # 所以这里同时断言"新版在"和"旧版不在" —— 两条都要过才算数。
    check("★ 打包的 agent.js 含遗忘功能（forgetList + forgetCard + listRemoveAt）",
          "function forgetList" in packed and "function forgetCard" in packed
          and "function listRemoveAt" in packed,
          f"forgetList={'function forgetList' in packed} "
          f"forgetCard={'function forgetCard' in packed} "
          f"listRemoveAt={'function listRemoveAt' in packed}")
    check("★ 遗忘走的是 List 直改（不再 NativeFunction 硬调 RemoveCard —— 那会崩）",
          "listRemoveAt(listPtr, hit)" in packed
          and "new NativeFunction(m.ptr, 'void', ['pointer', 'pointer', 'uint8', 'int'])" not in packed,
          "List直改" if "listRemoveAt(listPtr, hit)" in packed else "NOT FOUND")
    check("★ 打包的 agent.js 暴露了 forgetList / forget 两个 RPC",
          "forgetList()" in packed and "forget(ptrHex, stackKey)" in packed,
          f"forgetListRpc={'forgetList()' in packed} forgetRpc={'forget(ptrHex, stackKey)' in packed}")
    # 「无限精力」必须彻底删干净：不只是界面没按钮，agent 侧连钩子、状态位、set_Energy
    # 的解析目标都不能留（留一个就说明删漏了，将来会以"读不到配置"之类的形式返场）。
    check("★ 无限精力已彻底移除（agent.js 里无 infEnergy / setEnergy / energyValue）",
          not any(s in packed for s in ("infEnergy", "setEnergy", "energyValue",
                                        "M_Player_set_Energy")),
          str([s for s in ("infEnergy", "setEnergy", "energyValue", "M_Player_set_Energy")
               if s in packed]))
    check("★ 状态卡仍能读到精力（移除的是「锁定精力」，不是「显示精力」）",
          "OFF.energy" in packed and "energySoftCap" in packed
          and "energy: en" in packed,
          "ok")

user32.SetForegroundWindow(hwnd)
time.sleep(1.5)

print(f"[2b] 窗口置顶：exstyle 里 topmost={is_topmost(hwnd)}")
check("exe 里「窗口置顶」真的生效(读 exstyle)", is_topmost(hwnd),
      f"topmost={is_topmost(hwnd)}")

# [2c] 「点面板不抢游戏焦点」必须**同时**有两层：
#   ① WM_MOUSEACTIVATE → MA_NOACTIVATE（拦在消息里）
#   ② WS_EX_NOACTIVATE 窗口样式（系统级，真机上真正兜住的是这一层）
# 只做 ① 在替身窗口下看着是好的，真机实测照样丢焦点（用户报过一次），所以这里
# 把「必须挂着样式」钉死；同时必须带 WS_EX_APPWINDOW —— 带了 NOACTIVATE 的窗口
# 默认会从任务栏消失，不带 APPWINDOW 就等于把任务栏按钮弄丢了。
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
_ex = user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE)
print(f"[2c] exstyle={_ex:#x}  NOACTIVATE={bool(_ex & WS_EX_NOACTIVATE)}  "
      f"APPWINDOW={bool(_ex & WS_EX_APPWINDOW)}")
check("★ 面板挂着 WS_EX_NOACTIVATE（用户点击不激活它 = 游戏不掉焦点）",
      bool(_ex & WS_EX_NOACTIVATE), f"exstyle={_ex:#x}")
check("★ 同时带 WS_EX_APPWINDOW（把 NOACTIVATE 弄丢的任务栏按钮要回来）",
      bool(_ex & WS_EX_APPWINDOW), f"exstyle={_ex:#x}")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect, QSettings
app = QApplication(sys.argv)

x, y, w, h = rect_of(hwnd)
pix = app.primaryScreen().grabWindow(int(hwnd))
if WRITE_SHOT:
    pix.save(SHOT)
scale = pix.width() / 576.0
lw, lh = pix.width() / scale, pix.height() / scale
print(f"[3] 启动尺寸：逻辑 {lw:.0f}x{lh:.0f}（缩放 {scale:.2f}）截图 {pix.width()}x{pix.height()}")
check("启动逻辑尺寸 576x350", abs(lw - 576) <= 2 and abs(lh - 350) <= 2, f"{lw:.0f}x{lh:.0f}")

# ---- 纵向拉伸：加高 350 逻辑像素，验证真的能拉 ----
grow = int(350 * scale)
print(f"[4] 纵向拉伸 +350 逻辑像素（{grow} 物理）")
user32.SetWindowPos(hwnd, None, 0, 0, 0, h + grow, SWP_NOMOVE | SWP_NOZORDER)
time.sleep(1.8)
x2, y2, w2, h2 = rect_of(hwnd)
print(f"     拉伸前 {w}x{h}  ->  拉伸后 {w2}x{h2}")
check("高度确实变大了", h2 - h >= grow * 0.9, f"Δh={h2 - h} 期望≈{grow}")
check("宽度没被一起改", abs(w2 - w) <= 2, f"{w2} vs {w}")

pix2 = app.primaryScreen().grabWindow(int(hwnd))
if WRITE_SHOT:
    pix2.save(SHOT_TALL)
lw2, lh2 = pix2.width() / scale, pix2.height() / scale
print(f"     拉伸后逻辑尺寸 {lw2:.0f}x{lh2:.0f}  截图 {pix2.width()}x{pix2.height()}")
check("拉伸后宽度仍是 576", abs(lw2 - 576) <= 2, f"{lw2:.0f}")
check("拉伸后高度 >= 690", lh2 >= 690, f"{lh2:.0f}")
check("拉伸之后仍然置顶(没被 SetWindowPos 冲掉)", is_topmost(hwnd),
      f"topmost={is_topmost(hwnd)}")

# ---- 缩回去，确认不是单向的 ----
user32.SetWindowPos(hwnd, None, 0, 0, 0, h, SWP_NOMOVE | SWP_NOZORDER)
time.sleep(1.5)
x3, y3, w3, h3 = rect_of(hwnd)
print(f"[5] 缩回最小高度：{w3}x{h3}")
check("缩回后高度恢复", abs(h3 - h) <= 4, f"{h3} vs {h}")


# ---- [6] 运行状态页：黑盒端到端 ----
# 为什么放在 exe_smoke 里而不是只靠 ui_check：ui_check 是在**源码**里断言，
# 它证明不了"这个功能真的进了打包产物"。这里只看画面变没变 —— 点标题栏左侧的
# 仪表盘按钮，截图必须明显不同（新页面盖上来），再点一次必须回到原样。
# 坐标来自布局常数（内容边距 20/16，仪表盘按钮 30x30 排在标题左侧、垂直居中），
# 用 ClientToScreen 换算到物理像素 —— 直接拿 rect_of 会在有边框时整体偏掉一个边框宽。
# ⚠️ 按钮尺寸一改（`DashButton.SIZE`），这里的常数必须跟着改，否则点击落空。
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = ctypes.c_void_p


def dash_xy():
    org = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(org))
    # 20(左内边距) + 15(按钮半宽)，16(上内边距) + 15(按钮半高)
    return org.x + int(35 * scale), org.y + int(31 * scale)


def click_phys(px, py):
    user32.SetCursorPos(int(px), int(py))
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(1.2)          # 滑入动效 260ms，留足余量


def img_diff(a, b, step=4):
    """两张截图的像素差异比例（0~1）。采样 step 像素，够用又不用逐像素跑。"""
    ia, ib = a.toImage(), b.toImage()
    w = min(ia.width(), ib.width())
    hh = min(ia.height(), ib.height())
    tot = dif = 0
    for yy in range(0, hh, step):
        for xx in range(0, w, step):
            ca, cb = ia.pixel(xx, yy), ib.pixel(xx, yy)
            tot += 1
            if (abs((ca >> 16 & 255) - (cb >> 16 & 255))
                    + abs((ca >> 8 & 255) - (cb >> 8 & 255))
                    + abs((ca & 255) - (cb & 255))) > 30:
                dif += 1
    return dif / max(1, tot)


_bx, _by = dash_xy()
_hit = user32.WindowFromPoint(wt.POINT(int(_bx), int(_by)))
check("前置：仪表盘按钮位置上的窗口就是面板（否则点了个寂寞）",
      _hit == hwnd, f"WindowFromPoint={_hit} 面板={hwnd}")


def sheet_shown():
    """黑盒读「状态页是不是开着」：取状态页**最左边那条 8px 窄边**上的点 ——
    开着时它是页面的液体色（r≈157），关着时那里是面板自己的浅色底（r≈247）。

    ⚠️ 取样点曾经是「(30, 窗口高-40)」，加控制台卡之后**被卡片盖住**了 ——
    卡片是浅色玻璃（r≈240），于是不管开没开都读成"关着"，
    表现为 §6 一连 5 次点击都"没打开"、后面所有开/合断言整体错位一位。
    现在钉在 x≈3 的那条窄边上：页内任何卡片都够不到那里（卡片从 x=8 起）。
    ⚠️ 高度取 200 而不是"窗口高-40"：面板最矮 350 时这个点也稳定落在视口内部，
    不受"窗口被拉成多高"影响。
    """
    _im = app.primaryScreen().grabWindow(int(hwnd)).toImage()
    return _im.pixelColor(int(3 * scale), int(200 * scale)).red() < 220


def ensure_sheet(want, tries=5):
    """把状态页**驱到**目标状态，返回最终是否达成（并打印用掉几次点击）。

    ⚠️ 为什么不写成"点一下就当它翻转了"：在**测试进程**里注入的鼠标事件，开头几次
    会被系统吞掉 —— 实测 §6 连续两次 `click_phys` 之后，截图与基准图**逐像素相同**
    （diff=0.0%）、黑盒判据也仍是"关着"；而同一份代码到 §7 就一次点成了。
    这是"注入事件 + 面板此刻不是前台窗口"的组合效应（topmost ≠ foreground），
    真实用户点击不会遇到：面板挂着 `WS_EX_NOACTIVATE`，压根不参与激活。
    所以这里按**黑盒判据**驱动、顺带把被吞的点击吃掉；把次数打出来，
    万一哪天变成"点多少次都不开"，日志里一眼就能看见。
    """
    used = 0
    for _ in range(tries):
        if sheet_shown() == want:
            break
        click_phys(*dash_xy())
        used += 1
    _now = sheet_shown()
    print(f"     [ensure_sheet({want})] 用了 {used} 次点击 → "
          f"{'开着' if _now else '关着'}")
    return _now == want


# ⚠️ 先把光标挪到按钮上、等 hover 生效再抓基准图。否则基准图是"没悬停"、而结束图是
#    "悬停"（点击后光标留在按钮上），按钮的白底差异会顶穿 <5% 的阈值
#    —— 按钮一放大就更明显（30px 时实测 5.2%，直接判 FAIL）。
user32.SetCursorPos(int(_bx), int(_by))
time.sleep(0.45)
check("前置：状态页此刻是关的（下面的开/合断言才有意义）", not sheet_shown())
_before_pix = app.primaryScreen().grabWindow(int(hwnd))
check("★ 点仪表盘按钮 → 新页面真的盖上来（黑盒判据驱到开着）", ensure_sheet(True))
_open_pix = app.primaryScreen().grabWindow(int(hwnd))
if WRITE_SHOT:
    _open_pix.save(SHOT_SHEET)
_r_open = img_diff(_before_pix, _open_pix)
print(f"[6] 点仪表盘按钮：画面差异 {_r_open:.1%}")
check("★ 而且画面真的变了（不是只有内部状态在变）", _r_open > 0.15,
      f"diff={_r_open:.1%}")
check("★ 再点一次 → 页面滑走、回到原样（黑盒判据驱到关着）", ensure_sheet(False))
_close_pix = app.primaryScreen().grabWindow(int(hwnd))
_r_close = img_diff(_before_pix, _close_pix)
print(f"     再点一次收起：与画面初始差异 {_r_close:.1%}")
check("★ 收起来之后画面确实回到原样", _r_close < 0.05, f"diff={_r_close:.1%}")

# ---- [6b] 仪表盘指针转一整圈：**从屏幕帧里读出指针指向**（纯黑盒） ----
# ui_check 里那条是"源码自检"，证明不了这个动效真的进了打包产物；而"两张截图不一样"
# 这种判据在这里没用 —— 点一下同时会改按钮的 `_on` 底色，像素差归因不到指针上。
# 所以换成读**角度**：指针是圆环内的唯一墨（表盘弧在更外面 7.5 逻辑像素、轴点在里面
# 1.7），在半径 [2.2, 5.3] 的环里取"偏离底色最远"的像素，它的方位角就是指针指向。
# ⚠️ 不能用"最暗像素"：黑夜主题下指针比页面底**亮**（底色 L≈76 / 指针 L≈484）。
def _needle_angle(pix):
    """整窗截图里仪表盘指针的指向（度，0 = 3 点钟、逆时针为正）。读不到返回 None。"""
    im = pix.toImage()
    r0, r1 = 2.2 * scale, 5.3 * scale
    pts, cnt = [], {}
    for yy in range(int(_icy - r1) - 1, int(_icy + r1) + 2):
        for xx in range(int(_icx - r1) - 1, int(_icx + r1) + 2):
            if not (0 <= xx < im.width() and 0 <= yy < im.height()):
                continue
            if not (r0 <= math.hypot(xx - _icx, yy - _icy) <= r1):
                continue
            v = im.pixel(xx, yy)
            cnt[v] = cnt.get(v, 0) + 1
            pts.append((xx, yy, v))
    if not pts:
        return None
    bg = max(cnt, key=cnt.get)

    def _ch(v, sh):
        return (v >> sh) & 255

    _bx2, _by2, _bv = max(
        pts, key=lambda t: sum(abs(_ch(t[2], s) - _ch(bg, s)) for s in (16, 8, 0)))
    return math.degrees(math.atan2(_icy - _by2, _bx2 - _icx)) % 360.0


_org = wt.POINT(0, 0)
user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(_org))
_icx, _icy = _bx - _org.x, _by - _org.y      # 按钮中心在图内的物理坐标
_a0 = _needle_angle(_before_pix)
# ⚠️ 不能借 `click_phys`：它内部自带 `sleep(1.2)`，返回时 260ms 的旋转早就结束了，
#    连抓的每一帧都是"转完"的样子 —— 中间那几帧根本采不到。自己发点击、立刻连抓。
user32.SetCursorPos(int(_bx), int(_by))
time.sleep(0.35)                              # 和基准图一样处于悬停态
user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.04)
user32.mouse_event(0x0004, 0, 0, 0, 0)
_angles = []
for _ in range(8):
    _angles.append(_needle_angle(app.primaryScreen().grabWindow(int(hwnd))))
    time.sleep(0.03)
time.sleep(1.2)
_a1 = _needle_angle(app.primaryScreen().grabWindow(int(hwnd)))
print(f"[6b] 指针角度：点击前 {_a0}  中途 "
      f"{[None if a is None else round(a) for a in _angles]}  转完后 {_a1}")
check("前置：读到了指针（环里那点墨就是指针 —— 读不到说明取样位置算错了）",
      _a0 is not None and _a1 is not None, f"{_a0} / {_a1}")
if _a0 is not None and _a1 is not None:
    check("★ 点击前指针停在原位方向（≈55°）", abs(_a0 - 55.0) <= 14.0, f"{_a0:.0f}°")
    _far = [a for a in _angles if a is not None
            and abs((a - _a0 + 180) % 360 - 180) >= 60.0]
    check("★ 屏幕中途帧里指针真的扫到了别的方向（打进去的包里这个动效真的在跑）",
          len(_far) >= 1, f"偏离 ≥60° 的帧 {len(_far)}/{len(_angles)}，"
                          f"样例 {[round(a) for a in _far[:3]]}")
    check("★ 转完一圈后指针回到原位方向（≈55°）—— 回到原位，不是停在半圈",
          abs(_a1 - 55.0) <= 14.0, f"{_a1:.0f}°")
check("前置：这一节的点击把状态页翻到了开着（§6b 从关态点的）", sheet_shown())
ensure_sheet(False)                           # 把状态恢复成 §6 结束时的样子

# ---- [7] 连续拖拽窗口：状态页必须每次都跟着重算，不能卡在旧几何 ----
# 用户实测遇到过：拉窗口时状态卡下面多出一道"分界线"、颜色也不对 —— 根因是布局
# 重入时那次 resize 被无声丢掉（见 Panel._do_layout）。真机拖拽是一串**连续** resize，
# 离屏自检发不出来（那边是一次次配 qWait），所以只能在这里做：快速连发多次
# SetWindowPos 制造重入，然后看**页面底部**是不是还是液体色。
# 若状态页卡在旧几何，底部露出的会是面板背景（#f5f6f9，r≈245），
# 而液体色的 r 只有 157~183 —— 用 r 值区分即可，不必精确比对颜色。
ensure_sheet(True)                       # 重新开页
for _h in (620, 700, 780, 720, 660, 740):
    user32.SetWindowPos(hwnd, None, 0, 0, 0, int(_h * scale), SWP_NOMOVE | SWP_NOZORDER)
    time.sleep(0.12)                     # 快节奏 → 必然制造布局重入
def sheet_bottom_px(im):
    """状态页**底部**的像素 —— 取最左那条 8px 窄边（x≈3），不取正中。

    ⚠️ 以前取「底部正中」：状态页加进第三张卡（打法建议）之后卡片往下挪了一格，
       在矮窗口下那个点正好落在**控制台卡面**上（浅玻璃 r≈250），于是不管页面铺没铺满
       都读成"面板背景" ⇒ 假 FAIL（exe_smoke 实测踩到）。
    取窄边既避开所有卡片（页内卡片一律从 x=8 起），又仍然验的是"页面铺到底"这件事：
    页面若短一截/没跟着窗口重算，露出来的就是面板底色。
    """
    return im.pixelColor(int(3 * scale), max(0, im.height() - int(40 * scale)))


time.sleep(1.2)
check("前置：状态页确实开着（否则下面那条量到的不是页面）", sheet_shown())
_drag_pix = app.primaryScreen().grabWindow(int(hwnd))
if WRITE_SHOT:
    _drag_pix.save(SHOT_SHEET_DRAG)
_di = _drag_pix.toImage()
_dw, _dh = _di.width(), _di.height()
# 取底部最左窄边（见 sheet_bottom_px 的说明：正中会被卡面吃掉）
_probe = sheet_bottom_px(_di)
print(f"[7] 连续拖拽后 尺寸物理 {_dw}x{_dh}  底部窄边像素 {_probe.name()}")
check("★ 连续拖拽后状态页仍铺满（底部是液体色，不是面板背景）",
      _probe.red() < 220,
      f"{_probe.name()}（面板背景 r≈245 / 液体色 r≈157-183）")

# ---- [8] 状态页打开时滚鼠标滚轮（用户报的第二条触发路径） ----
# 滚轮会驱动 `_apply_scroll → _update_veils`，而帘子是靠 raise_() 才盖得住滚动内容的
# —— 早先它连状态页一起盖，用户看到页底多出一条横贯的浅色带。
# ⚠️ 缩到内容超出视口的高度才有得滚、底帘子才会出现；滚轮要真的滚到面板上，
#    所以先**关掉状态页**滚一次做前置（页开着时滚动区被盖住，画面不会变，验不出来）。
user32.SetWindowPos(hwnd, None, 0, 0, 0, int(430 * scale), SWP_NOMOVE | SWP_NOZORDER)
time.sleep(1.0)
ensure_sheet(False)                      # 关掉状态页，露出滚动区
_rx, _ry, _rw, _rh = rect_of(hwnd)
user32.SetCursorPos(_rx + _rw // 2, _ry + _rh // 2)
time.sleep(0.4)
_a_pix = app.primaryScreen().grabWindow(int(hwnd))
for _ in range(3):
    user32.mouse_event(0x0800, 0, 0, -120, 0)     # MOUSEEVENTF_WHEEL 下滚
    time.sleep(0.25)
time.sleep(1.0)
_b_pix = app.primaryScreen().grabWindow(int(hwnd))
check("前置：滚轮真的滚到了这个面板（否则下面的断言只是空过）",
      img_diff(_a_pix, _b_pix) > 0.02, f"diff={img_diff(_a_pix, _b_pix):.1%}")

ensure_sheet(True)                       # 重新打开状态页
for _ in range(3):
    user32.mouse_event(0x0800, 0, 0, -120, 0)
    time.sleep(0.25)
time.sleep(1.2)
check("前置：状态页确实开着（否则下面那条量到的不是页面）", sheet_shown())
_wh_pix = app.primaryScreen().grabWindow(int(hwnd))
_wi = _wh_pix.toImage()
_wr = sheet_bottom_px(_wi)
print(f"[8] 在状态页上滚滚轮后 底部窄边像素 {_wr.name()}")
check("★ 滚轮之后页底仍是液体色（帘子没盖到状态页上面来）",
      _wr.red() < 220, f"{_wr.name()}（面板背景 r≈245 / 液体色 r≈157-183）")
user32.SetCursorPos(0, 0)

# ---- [9] 白天/黑夜开关：黑盒端到端（含"从图标中心扩散"这件事本身）----
# 断言分三段，缺一不可：
#   ① 中途一帧里**新旧主题同时存在**（右上角已暗、左下角还浅）→ 这就是"圆形扩散"，
#      而不是"整窗一起变色"。扩散只有 360ms，所以点完连抓几帧，只要有一帧符合即可。
#   ② 收尾整窗变暗 + 偏好写进了配置（跨进程读注册表验证，不是读内存）。
#   ③ 再点一次能切回浅色（证明是双向开关，不是一次性）。
# 坐标：576 - 20(右内边距) - 15(按钮半宽) = 541，y 与仪表盘按钮同高 = 16+15 = 31。
# ⚠️ 按钮尺寸一改（ThemeButton.SIZE），这里的常数必须跟着改。
_LX, _LY = 541, 31
_pref0 = str(QSettings("XIZI", "ShroomTrainer").value("theme", "light"))
# ⚠️ §8 结束时状态页是**开着**的 —— 取样点 (10,340) 会落在状态页的液体色上
#    （r≈157），"前置：切换前是浅色"那条会直接红。先按黑盒判据把页面驱到关。
ensure_sheet(False)
time.sleep(0.6)


def theme_xy():
    org = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(org))
    return org.x + int(_LX * scale), org.y + int(_LY * scale)


def _pix_at(pix, lx, ly):
    """抓到的整窗截图里、面板**逻辑坐标** (lx, ly) 处的颜色。"""
    im = pix.toImage()
    return im.pixelColor(int(min(im.width() - 1, lx * scale)),
                         int(min(im.height() - 1, ly * scale)))


def theme_click():
    tx, ty = theme_xy()
    user32.SetCursorPos(int(tx), int(ty))
    time.sleep(0.4)                       # 先让 hover 生效（和仪表盘按钮那边同理）
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


_tx, _ty = theme_xy()
_thit = user32.WindowFromPoint(wt.POINT(int(_tx), int(_ty)))
check("前置：主题开关位置上的窗口就是面板（否则点了个寂寞）",
      _thit == hwnd, f"WindowFromPoint={_thit} 面板={hwnd}")
_light_pix = app.primaryScreen().grabWindow(int(hwnd))
_bg_before = _pix_at(_light_pix, 10, 340)
print(f"[9] 切换前 左下角像素 {_bg_before.name()}（应是浅色面板底）")
check("前置：切换前面板是浅色（否则后面那条「变暗」验不出来）",
      _bg_before.red() > 200, _bg_before.name())

theme_click()
_mid = []
for _ in range(6):                        # 360ms 的扩散，边抓边找"新旧同时存在"的那一帧
    _f = app.primaryScreen().grabWindow(int(hwnd))
    _mid.append((_pix_at(_f, 500, 31), _pix_at(_f, 10, 340)))
    time.sleep(0.035)
_ok_mid = [(a, b) for a, b in _mid if a.red() < 110 and b.red() > 200]
print(f"     中途帧 (右上角r, 左下角r): {[(a.red(), b.red()) for a, b in _mid]}")
check("★ 扩散是真的从图标中心铺开（中途有帧：右上已变暗、左下还是浅的）",
      len(_ok_mid) >= 1,
      f"合格帧 {len(_ok_mid)}/{len(_mid)}")
time.sleep(0.9)
_dark_pix = app.primaryScreen().grabWindow(int(hwnd))
if WRITE_SHOT:
    _dark_pix.save(SHOT_THEME)
_bg_dark = _pix_at(_dark_pix, 10, 340)
_diff_theme = img_diff(_light_pix, _dark_pix)
print(f"     切到黑夜：左下角 {_bg_dark.name()}  整窗差异 {_diff_theme:.1%}")
check("★ 整窗真的换成了黑夜（不是只有局部变了）",
      _bg_dark.red() < 80 and _diff_theme > 0.40,
      f"{_bg_dark.name()} diff={_diff_theme:.1%}")
# 单独量一下**开关那块 30×30**：只有图标变了才说明"太阳 → 月亮"，
# 整窗差异没法证明这一点（背景一变整窗都会差 40%+）。
_icon_rect = QRect(int((_LX - 15) * scale), int((_LY - 15) * scale),
                   int(30 * scale), int(30 * scale))
_icon_diff = img_diff(_light_pix.copy(_icon_rect), _dark_pix.copy(_icon_rect), step=1)
check("★ 开关图标本身真的换了（只量那 30×30：太阳 → 月亮）",
      _icon_diff > 0.05, f"图标区 diff={_icon_diff:.1%}")
_saved = str(QSettings("XIZI", "ShroomTrainer").value("theme", ""))
check("★ 偏好已写进配置（跨进程读注册表，不是读面板内存）", _saved == "dark",
      f"QSettings theme={_saved!r}")

theme_click()
time.sleep(0.9)
_back_pix = app.primaryScreen().grabWindow(int(hwnd))
_bg_back = _pix_at(_back_pix, 10, 340)
print(f"     再点一次：左下角 {_bg_back.name()}  与初始差异 {img_diff(_light_pix, _back_pix):.1%}")
check("★ 再点一次切回白天（双向开关，不是一次性的）",
      _bg_back.red() > 200 and img_diff(_light_pix, _back_pix) < 0.08,
      f"{_bg_back.name()} diff={img_diff(_light_pix, _back_pix):.1%}")
QSettings("XIZI", "ShroomTrainer").setValue("theme", _pref0)   # 把机器上的偏好还原
user32.SetCursorPos(0, 0)

# ---- [10] 控制台的三态分段控件：黑盒端到端 ----
# ui_check 里那些是在源码/离屏渲染上断言的，**证明不了这个功能进了打包产物**，
# 也证明不了"真的能用鼠标点"。这里全程只看屏幕像素 + 注册表：
#   ① 在状态页里把指示器那颗药丸找出来（找得到 = 控制台卡和分段控件都在）；
#   ② 点目标格 → 药丸必须真的滑过去（黑盒判据驱动，注入点击被吞也不怕）；
#   ③ 顺带验注册表：这个选择**真的落盘了**，下次开面板还认。
from collections import Counter

_GLOW_MODES = ("color", "mono", "off")
_pref_glow0 = str(QSettings("XIZI", "ShroomTrainer").value("glowMode", "color"))
_GLOW_BAND = (150, 430)          # 控制台卡所在的逻辑 y 带（面板最矮 350 时也在窗口内）
# 分段控件是**右对齐**的（`ConsoleCard` 里 `addStretch(1)` 在它前面），右内缘 =
# 面板宽 576 - 状态页窄边 8 - 卡内右内边距 20 = 548，**与字体无关**。
_GLOW_RIGHT = 548.0
_GLOW_PAD = 3.0                  # 与 `GlowSegmented.PAD` 对齐


def _pill_box(pix):
    """把分段控件的指示器药丸从屏幕像素里找出来，返回逻辑矩形 (l, t, r, b)；没有则 None。

    ⚠️ 判据分两步，缺一不可（两版都踩过）：
    ① **先取众数当"强调色"**。整个面板里不止一样东西是蓝的 —— 单色荧光
       `GLOW_MONO #7CABC6`（红=124）就贴着卡片边缘画了一圈。用「蓝比红多」这种粗判据
       会把荧光一起算进来，bbox 被撑成**卡片内容区**（宽 520，而药丸只有 51），
       据此外推的目标点落到控件**外面**，点多少下都切不动（实测连点 8 次注册表纹丝不动）。
       众数就稳：药丸是实心填充（实测 1878 px），荧光是一圈稀薄笔画（单个颜色 ≤92 px）。
    ② 再按**众数 ±40 曼哈顿**做紧匹配，并要求每行有 ≥20 逻辑 px 的**连续** run。
       圆角两端是抗锯齿的，紧匹配天然只拿到药丸内芯，但「取所有命中行里最宽的那条 run」
       能把真宽度还原出来（圆角只吃掉上/下各一个半径，中段那几行是满宽）。
    """
    im = pix.toImage()
    y0, y1 = int(_GLOW_BAND[0] * scale), min(im.height(), int(_GLOW_BAND[1] * scale))
    loose = Counter()
    for yy in range(y0, y1):
        for xx in range(im.width()):
            v = im.pixel(xx, yy)
            if (v & 255) - ((v >> 16) & 255) > 45 and ((v >> 16) & 255) < 140:
                loose[((v >> 16) & 255, (v >> 8) & 255, v & 255)] += 1
    if not loose:
        return None
    ar, ag, ab = loose.most_common(1)[0][0]

    rows = []
    for yy in range(y0, y1):
        run = best = 0
        bs = be = -1
        for xx in range(im.width()):
            v = im.pixel(xx, yy)
            if (abs(((v >> 16) & 255) - ar) + abs(((v >> 8) & 255) - ag)
                    + abs((v & 255) - ab)) <= 40:
                run += 1
                if run > best:
                    best, bs, be = run, xx - run + 1, xx
            else:
                run = 0
        if best >= int(20 * scale):
            rows.append((yy, bs, be))
    if len(rows) < int(8 * scale):
        return None
    return (min(r[1] for r in rows) / scale, rows[0][0] / scale,
            max(r[2] for r in rows) / scale, rows[-1][0] / scale)


def _registry_glow():
    """功能层的唯一 oracle：**跨进程**读注册表（不是读面板内存）。

    为什么拿它当 oracle 而不是"算出格心点一下"：注入的鼠标点击开头几次可能被系统吞掉
    （见 `ensure_sheet` 的说明），"点过了"不等于"点到了"；而注册表是功能层的事实。
    """
    _m = str(QSettings("XIZI", "ShroomTrainer").value("glowMode", "color"))
    return _m if _m in _GLOW_MODES else "color"


def _click_logical(lx, ly):
    _o = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(_o))
    click_phys(_o.x + int(lx * scale), _o.y + int(ly * scale))


def _seg_x(idx, seg_w):
    """第 `idx` 格的**格心**（面板逻辑 x）。

    推导：控件右缘 = `_GLOW_RIGHT`（= 548）；控件宽 = `3*w + 2*PAD` ⇒ 左缘 = 548-3w-2PAD；
    格心 = 左缘 + PAD + (idx+0.5)w = **`548 - PAD - (2.5-idx)*w`**。
    注意这里只剩一个 PAD（`-2PAD + PAD`），写成 `-2*PAD` 会整体左偏 3px。
    ✱ 公式里没有任何"当前选中哪一格"，所以起始格位是哪个都不影响正确性。
    """
    return _GLOW_RIGHT - _GLOW_PAD - (2.5 - idx) * seg_w


def _pill_left_of(idx, seg_w):
    """第 `idx` 格的指示器**左缘**（面板逻辑 x）。

    药丸画在控件局部 `PAD + i*w + 0.5` 处 ⇒ 面板 x = `548 - PAD - (3-idx)*w + 0.5`。
    """
    return _GLOW_RIGHT - _GLOW_PAD - (3.0 - idx) * seg_w + 0.5


def _drive_glow(target, tries=6):
    """按**注册表**当 oracle，把荧光模式驱到 `target`。返回 (是否达成, 用掉几次点击)。"""
    used = 0
    while used < tries:
        if _registry_glow() == target:
            return True, used
        _pb = _pill_box(app.primaryScreen().grabWindow(int(hwnd)))
        if _pb is None:
            return False, used
        _segw = (_pb[2] - _pb[0] + 1) + 1        # 药丸宽 = seg_w - 1
        _want = _seg_x(_GLOW_MODES.index(target), _segw)
        _off = (0, -8, 8, -16, 16)[used % 5]     # 被吞/判宽偏了就往两边让一点
        _y = (_pb[1] + _pb[3]) / 2
        print(f"      [drive] 药丸 x={_pb[0]:.0f} 宽={_pb[2] - _pb[0] + 1:.0f}"
              f"（seg_w≈{_segw:.0f}）目标格心 {_want:.0f} → 点 ({_want + _off:.0f}, {_y:.0f})")
        _click_logical(_want + _off, _y)
        used += 1
    return _registry_glow() == target, used


def _set_glow(target):
    """开状态页 → 点目标格 → 关状态页。返回 (是否达成, 用掉几次点击)。"""
    ensure_sheet(True)
    time.sleep(0.35)
    _ok, _n = _drive_glow(target)
    ensure_sheet(False)
    time.sleep(0.45)
    return _ok, _n


ensure_sheet(True)
time.sleep(0.5)
_pill0 = _pill_box(app.primaryScreen().grabWindow(int(hwnd)))
_i0, _segw0 = _GLOW_MODES.index(_registry_glow()), 52.0
check("★ 状态页里能看到控制台的分段控件（按强调色找到那颗药丸）",
      _pill0 is not None, f"药丸 = {_pill0}")
if _pill0 is not None:
    _segw0 = (_pill0[2] - _pill0[0] + 1) + 1
    _want0 = _pill_left_of(_i0, _segw0)
    print(f"[10] 药丸 {tuple(round(v) for v in _pill0)} → seg_w ≈ {_segw0:.0f}；"
          f"注册表 {_GLOW_MODES[_i0]!r} 对应左缘应≈{_want0:.0f}，实测 {_pill0[0]:.0f}")
    check("★ 指示器现在停的位置 = 注册表说的那一格（视觉与功能层此刻一致，"
          "后面比位移才立得住）",
          abs(_pill0[0] - _want0) <= 10,
          f"实测 {_pill0[0]:.0f} vs 推算 {_want0:.0f}（差 {_pill0[0] - _want0:+.0f}）")

ensure_sheet(False)
time.sleep(0.4)
_ok_off, _n_off = _set_glow("off")
check("★ 点「关闭」→ 功能层真的切过去了（跨进程读注册表当判据）",
      _ok_off and _registry_glow() == "off",
      f"glowMode={_registry_glow()!r} 用了 {_n_off} 次点击")

ensure_sheet(True)
time.sleep(0.45)
_pill2 = _pill_box(app.primaryScreen().grabWindow(int(hwnd)))
if _pill0 is not None and _pill2 is not None:
    _moved, _need = _pill2[0] - _pill0[0], (2 - _i0) * _segw0
    check("★ 药丸真的滑到了第三格（位移正好是「差几格×段宽」，不是只有内部状态变了）",
          abs(_moved - _need) <= 8 and abs(_pill2[2] - (_GLOW_RIGHT - _GLOW_PAD)) <= 8,
          f"左缘 {_pill0[0]:.0f} -> {_pill2[0]:.0f}（位移 {_moved:+.0f}，应 {_need:+.0f}）；"
          f"右缘 {_pill2[2]:.0f} 应 ≈{_GLOW_RIGHT - _GLOW_PAD:.0f}")
ensure_sheet(False)
time.sleep(0.4)

_ok_col, _n_col = _set_glow("color")
check("★ 再点回「彩色」→ 又切回去了（三态双向可达，不是单向锁死）",
      _ok_col and _registry_glow() == "color",
      f"glowMode={_registry_glow()!r} 用了 {_n_col} 次点击")
ensure_sheet(True)
time.sleep(0.45)
_pill0b = _pill_box(app.primaryScreen().grabWindow(int(hwnd)))
if _pill2 is not None and _pill0b is not None:
    check("★ 药丸滑回第一格（往左正好两个段宽）",
          abs((_pill0b[0] - _pill2[0]) + 2 * _segw0) <= 8,
          f"左缘 {_pill2[0]:.0f} -> {_pill0b[0]:.0f}"
          f"（位移 {_pill0b[0] - _pill2[0]:+.0f}，应 {-2 * _segw0:+.0f}）")

# ---- [11] 荧光真的关得掉（纯看画面）----
# 判据落在**顶部胶囊那条边**上：荧光就是贴着描边往内铺 9px 的一圈，改没改这里最直接。
# 三层对照缺一不可：
#   ① 前置：光标移开 vs 悬停，画面必须变（证明悬停真的进去了、取样带真的在胶囊上）；
#   ② 关闭 vs 彩色：贴边那圈差异明显（功能真的改画面）；
#   ③ 关闭模式连抓两张几乎相同（没有逐帧动效 ⇒ 这条测量本身稳，②的差异归于荧光）。
_CAP_BAND = QRect(int(26 * scale), int(59 * scale), int(516 * scale), int(60 * scale))
_CAP_HOVER = (288, 90)          # 顶部胶囊（窗口置顶）的中间偏上，落在取样带里


def _shot_at(lx, ly, wait=0.6):
    _o = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(_o))
    user32.SetCursorPos(_o.x + int(lx * scale), _o.y + int(ly * scale))
    time.sleep(wait)
    return app.primaryScreen().grabWindow(int(hwnd))


ensure_sheet(False)
time.sleep(0.4)
# 先把滚动归零：顶部胶囊必须正好落在上面那个取样带里。
# ⚠️ 滚轮给的是"光标下的窗口"（Windows 默认行为），所以先把光标放到面板中间的胶囊上；
#    §8 用的是同一条路，实测有效。
_shot_at(*_CAP_HOVER, wait=0.2)
for _ in range(12):
    user32.mouse_event(0x0800, 0, 0, 120, 0)      # +120 = 向上滚
    time.sleep(0.12)
time.sleep(1.0)

_shot_away = _shot_at(560, 690, wait=0.5)          # 面板右下角空白处（不在任何卡上）
_shot_col_1 = _shot_at(*_CAP_HOVER)
_shot_col_2 = _shot_at(*_CAP_HOVER)
_d_away = img_diff(_shot_away.copy(_CAP_BAND), _shot_col_1.copy(_CAP_BAND), step=2)
_d_col_self = img_diff(_shot_col_1.copy(_CAP_BAND), _shot_col_2.copy(_CAP_BAND), step=2)
print(f"[11] 彩色：移开 vs 悬停 {_d_away:.1%}；连抓两张 {_d_col_self:.1%}")

_set_glow("off")
check("前置：这一节开始时荧光确实是「关闭」", _registry_glow() == "off",
      f"glowMode={_registry_glow()!r}")
_shot_off_1 = _shot_at(*_CAP_HOVER)
_shot_off_2 = _shot_at(*_CAP_HOVER)
_d_off_self = img_diff(_shot_off_1.copy(_CAP_BAND), _shot_off_2.copy(_CAP_BAND), step=2)
_d_off_vs_col = img_diff(_shot_off_1.copy(_CAP_BAND), _shot_col_1.copy(_CAP_BAND), step=2)
print(f"[11] 关闭：连抓两张 {_d_off_self:.1%}；关闭 vs 彩色 {_d_off_vs_col:.1%}")
check("★ 前置：取样带真的落在胶囊上、悬停真的进去了（移开 vs 悬停画面会变）",
      _d_away > 0.02, f"{_d_away:.1%}")
check("★ 关掉荧光后悬停画面的**贴边那一圈**确实变了（荧光真的能关）",
      _d_off_vs_col > 0.06, f"{_d_off_vs_col:.1%}")
check("★ 灵敏度对照：关着的时候连抓两张几乎同一张（没有逐帧动效 = 这条测量本身稳）",
      _d_off_self < 0.02, f"{_d_off_self:.1%}")
check("★ 彩色模式连抓两张会变（差异来自会动的荧光，不是静态底色）",
      _d_col_self > _d_off_self + 0.02,
      f"彩色 {_d_col_self:.1%} vs 关闭 {_d_off_self:.1%}")

ensure_sheet(True)
time.sleep(0.4)
_drive_glow("color")               # 还原成彩色，别把机器上的设置改坏
ensure_sheet(False)
QSettings("XIZI", "ShroomTrainer").setValue("glowMode", _pref_glow0)
user32.SetCursorPos(0, 0)
time.sleep(0.4)

# ---- [12] 打法建议卡（滚动区最后一张）：黑盒端到端 ----
# 这条是补上的：之前**没有任何黑盒覆盖**（grep 打法/plan = 0 命中）。
# ui_check 证明得了"源码里长这样"，证明不了"真点得开、真画得出来"。
# 打法卡是**整卡点击开关**（同牌库卡），所以黑盒判据用「点前后画面差异」。
print("[12] 打法建议卡：黑盒端到端（滚动到底 → 点开/点关）")


def scroll_to_bottom(times=24):
    """把滚动区别到最底（打法卡是滚动区最后一张）。

    ⚠️⚠️ 光标**必须放在滚动视口里**才能滚 —— 第一版图省事用了 `dash_xy()`
       （那是标题栏上控制台按钮的位置），滚轮事件被标题栏吃掉、`diff=0.0%`，
       于是"滚到底"这一步静默失效、点到了空白处（症状：点击前后只差 0.3%）。
       视口中心 = 左内边距 20 + 半宽 268 → 逻辑 x≈288；纵向上缘 64 往下一点取 200。
    """
    _org = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(_org))
    user32.SetCursorPos(_org.x + int(288 * scale), _org.y + int(200 * scale))
    time.sleep(0.25)
    for _ in range(times):
        user32.mouse_event(0x0800, 0, 0, -120, 0)      # MOUSEEVENTF_WHEEL 下滚
        time.sleep(0.08)
    time.sleep(0.8)


def plan_candidates():
    """打法卡的候选点击点：卡面内的几个 y（从视口底往上扫）。

    ⚠️ 不写死单个坐标：卡片高度自适应（收起 70 / 展开更高），而"卡底距窗口底"
       还隔着内容底衬（CONTENT_PAD_B）和滚动余量 —— 写死一个 y 会出现
       "展开点得动、收起点不动"（实测踩过）。所以给一组候选，按黑盒判据驱到目标态。
    ⚠️ x 取 250：卡片横跨 20~544（面板坐标），250 稳稳在卡面里。
    """
    _org = wt.POINT(0, 0)
    user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(_org))
    _wh = wt.RECT()
    user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(_wh))
    return [(_org.x + int(250 * scale), _org.y + _wh.bottom - int(_dy * scale))
            for _dy in (44, 58, 72, 30, 86)]


def shot_at_bottom():
    """滚到最底 + 把光标挪到面板外，再抓一帧。

    ⚠️ 三次抓图的**光标位置必须一致且不在面板上**，否则差异里会混进按钮 hover
       的荧光/白底变化，归因不到"打法卡开没开"上（点击后光标本来就停在卡上）。
    """
    scroll_to_bottom()
    user32.SetCursorPos(0, 0)
    time.sleep(0.5)
    return app.primaryScreen().grabWindow(int(hwnd))


def drive_plan(want_open, tries=6):
    """按**黑盒判据**把打法卡驱到目标开合态，返回 (用掉的点击次数, 与收起基准的差异)。

    判据：抓一帧，与「收起基准帧」比 —— 差异 >1% 即视为开着（展开会多出牌格/摘要行）。
    ⚠️ 同 §6 的 ensure_sheet：注入的点击开头几次可能被系统吞掉，所以是"驱到"而不是
       "点一下就当它翻转了"。
    """
    used = 0
    d = 0.0
    for _p in plan_candidates():
        for _ in range(2):
            _cand = app.primaryScreen().grabWindow(int(hwnd))
            d = img_diff(_plan_off, _cand)
            if (d > 0.01) == bool(want_open):
                return used, d
            click_phys(*_p)
            used += 1
        if used >= tries:
            break
    return used, img_diff(_plan_off, app.primaryScreen().grabWindow(int(hwnd)))


# 先把窗口压回 350 逻辑高，保证"必须滚动才能看到打法卡"这个前提成立
user32.SetWindowPos(hwnd, None, 0, 0, 0, int(350 * scale), SWP_NOMOVE | SWP_NOZORDER)
time.sleep(1.2)

_plan_off = shot_at_bottom()
_px12, _py12 = plan_candidates()[0]
_hit12 = user32.WindowFromPoint(wt.POINT(int(_px12), int(_py12)))
check("前置：打法卡卡面上的点确实落在面板上", _hit12 == hwnd,
      f"WindowFromPoint={_hit12} 面板={hwnd}")

# 滚动这一步本身也要立得住（否则下面的点击是在"没滚下去"的画面上做的）
user32.SetCursorPos(int(_bx), int(_by))
time.sleep(0.5)
for _ in range(24):
    user32.mouse_event(0x0800, 0, 0, 120, 0)           # 滚回顶部
    time.sleep(0.08)
user32.SetCursorPos(0, 0)
time.sleep(0.6)
_top = app.primaryScreen().grabWindow(int(hwnd))
_plan_off = shot_at_bottom()
_r_scroll = img_diff(_top, _plan_off)
print(f"[12] 滚动区到底 vs 顶：画面差异 {_r_scroll:.1%}")
check("★ 滚动区真的能滚（否则下面点的是没滚下去的那一屏）",
      _r_scroll > 0.05, f"diff={_r_scroll:.1%}")

_used12, _r_plan = drive_plan(True)
print(f"[12] 点开打法卡：用了 {_used12} 次点击 → 画面差异 {_r_plan:.1%}")
check("★ 点一下打法卡真的会展开（画面变了、不是只有内部状态在变）",
      _r_plan > 0.01, f"diff={_r_plan:.1%}（点了 {_used12} 次）")

# 反向：再驱回收起态（正向只证明"新的在"，反向才证明"真的能关"）
_used12b, _r_back = drive_plan(False)
print(f"[12] 再点收起：用了 {_used12b} 次点击 → 与收起初始差异 {_r_back:.1%}")
check("★ 再点一次能收回去（双向开关，不是单向锁死）",
      _r_back < 0.01, f"diff={_r_back:.1%}（点了 {_used12b} 次）")

user32.SetCursorPos(0, 0)
time.sleep(0.4)

cleanup()
time.sleep(1.0)
still = [t for _, t, _ in visible_windows() if TITLE_KEY in t]
check("关掉后无残留窗口", still == [], str(still))

print("\n=== 结果 ===")
if fails:
    print(f"失败 {len(fails)} 项: {fails}")
    sys.exit(1)
print("SMOKE OK")
