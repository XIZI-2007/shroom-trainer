# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""Shroom & Gloom 辅助面板 —— PyQt6 图形界面

材料：页面底 = 低饱和柔光斑 + 颗粒；容器 = 真毛玻璃（预模糊背景裁切采样）+ 预渲染软阴影。
动效：胶囊液面从左向右填满（含到位荡漾），从右向左抽空；所有动画只改自绘状态，不触发布局。

设计约束：无 emoji、低饱和配色（S < 40%）、浅色苹果风、单次动效 <= 400ms。
"""
import json
import math
import os
import queue
import sys
import tempfile
import ctypes

import numpy as np

from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QPropertyAnimation, QVariantAnimation,
                          QEasingCurve, pyqtProperty, QRect, QRectF, QPoint, QPointF, QTimer)
from PyQt6.QtGui import (QPainter, QColor, QFont, QFontMetrics, QShortcut, QKeySequence,
                         QImage, QPixmap, QPainterPath, QLinearGradient, QRadialGradient,
                         QConicalGradient, QBrush, QPen)
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                             QPushButton, QGraphicsDropShadowEffect,
                             QGraphicsOpacityEffect, QSizePolicy)

from trainer_core import GameTrainer, find_game_dir, load_card_i18n

# ---------------- 窗口置顶（Win32 SetWindowPos，PinWin 用的就是这一套） ----------------
# 为什么不用 Qt 的 `setWindowFlag(WindowStaysOnTopHint)`：那改的是窗口样式，Qt 会
# **先把可见窗口隐藏、销毁并重建原生窗口再显示** —— 屏幕闪一下，焦点、输入法、
# 无边框/圆角状态都会丢。SetWindowPos 只是原地改 Z 序，无闪烁、不影响尺寸和焦点。
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0010, 0x0200
_TOP_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER


def set_window_topmost(hwnd, on):
    """把窗口插到 topmost 层（on=True）或摘回普通层（on=False）。成功返回 True。"""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_int
        return bool(u.SetWindowPos(ctypes.c_void_p(int(hwnd)),
                                   ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
                                   0, 0, 0, 0, _TOP_FLAGS))
    except Exception:
        return False


def window_is_topmost(hwnd):
    """读回窗口真实的置顶状态（自检用）：GWL_EXSTYLE & WS_EX_TOPMOST。

    别信界面上那个开关 —— `SetWindowPos` 是可能失败的（被其它工具抢占 Z 序、
    窗口已销毁等），只有读 exstyle 才知道真生效了没有。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x00000008
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        return bool(u.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE) & WS_EX_TOPMOST)
    except Exception:
        return False


# ---------------- 点面板不抢游戏焦点（WS_EX_NOACTIVATE + WM_MOUSEACTIVATE） ----------------
# 需求：玩游戏时点面板，游戏不能掉焦点（Unity 收到 OnApplicationFocus(false) 会暂停）。
#
# 两层保险，**缺一不可**：
#
#   ① WM_MOUSEACTIVATE：窗口在被点、且当前非激活时，系统先来问一句「要不要激活你」，
#      返回值就是答案：
#        MA_ACTIVATE(1)          → 激活（游戏失去焦点）
#        MA_NOACTIVATE(3)        → **不激活，但鼠标消息照常投递**，按钮照常响应 ★
#        MA_NOACTIVATEANDEAT(4)  → 不激活且**吃掉**这次点击，按钮不响应 —— 别用这个
#
#   ② WS_EX_NOACTIVATE 窗口样式：**系统级**的「用户点击这个窗口不激活它」，
#      在激活判定发生之前就拦住了，谁都绕不过去。
#
# ⚠️ 只做 ① 在**真实游戏下实测无效**（早期用一次性真机探针量到：点击送到了面板、面板
#    也确实没变前台，但游戏照样丢焦点 → 掉暂停）。替身窗口下 ① 看着是好的，真机上不够
#    —— 所以两个都上。这条结论推翻过一次探针的乐观判断，别再删 ②。
#
# 代价：带 WS_EX_NOACTIVATE 的窗口默认**不上任务栏** → 必须同时补 WS_EX_APPWINDOW
# 把任务栏按钮要回来（tools/exe_smoke.py 有断言钉住这两条）。
#
# 面板上所有控件都是 NoFocus，没有任何需要键盘输入的地方 —— 所以样式**恒挂着**，
# 点面板任何地方（含标题栏以外的所有区域）都不会夺走游戏的焦点。
# （曾经有个例外："点输入框要打字" 才临时摘掉样式 + 抢前台；无限精力功能整体移除后，
#   那个例外连同输入控件、焦点状态机一起删掉了。）
#
# 已知限制：点**标题栏**（非客户区）仍会激活面板 —— 原生窗口框架的行为，拦不住。
WM_MOUSEACTIVATE = 0x0021
MA_ACTIVATE, MA_NOACTIVATE = 1, 3

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000     # 用户点击本窗口时不激活它
WS_EX_APPWINDOW = 0x00040000      # 强制上任务栏（抵消上面那条的副作用）
WS_EX_TOPMOST = 0x00000008
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020


def _win_long_api():
    """取 user32 并把 Get/SetWindowLongW 的签名声明好（64 位下不声明会截断 HWND）。"""
    u = ctypes.windll.user32
    u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = ctypes.c_int
    return u


def _noact_exstyle(ex, on):
    """算出「挂 / 摘 WS_EX_NOACTIVATE」之后应该是什么 exstyle。

    单拎成纯函数是为了能离线自检（不碰真 HWND）：
      on=True  → 补 NOACTIVATE + APPWINDOW（NOACTIVATE 会把窗口从任务栏摘掉，
                所以必须同时用 APPWINDOW 把它按回去）
      on=False → 只摘 NOACTIVATE，**APPWINDOW 留着**：摘的那一瞬是用户要打字，
                 没必要顺带去动任务栏，而且留着 APPWINDOW 没有任何可见副作用。
    """
    return ((ex | WS_EX_NOACTIVATE | WS_EX_APPWINDOW) if on
            else (ex & ~WS_EX_NOACTIVATE))


# ---------------- 标题栏 Desktop Acrylic：查过了，**不采用** ----------------
# 结论（第 42 轮实测，Win11 build 26200）：系统自带 Desktop Acrylic 在**本面板上没用**。
# 不是 API 不可用 —— `DwmSetWindowAttribute(hwnd, 38, DWMSBT_TRANSIENTWINDOW(3))` 系统照收，
# 标题栏也真去采样下层并模糊（实测垫 20 逻辑px 黑白竖条，被完全抹平）。
#
# 卡在哪：**Acrylic 只在窗口处于前台（活动）时透明**。一旦窗口失活，DWM 按设计回退成
# 不透明实色（浅色档 #CACACA / 深色档 #5A5A5A），而且这个回退是**粘性**的：
# 重下发属性、off→on、换主题都救不回来，只有 hide→show（重新获得前台）能恢复。
# 而本面板的立身之本就是「点面板不抢游戏焦点」（WS_EX_NOACTIVATE + MA_NOACTIVATE）
# ⇒ 真实使用中**永远是非前台** ⇒ 标题栏只会是那两块脏灰实色，
# 比默认标题栏（跟随系统主题：浅 #FCFCFC / 深 #222222）更差，且完全看不到下层。
#
# 顺带查清的两件事：
#  · 客户区（Qt 的 redirection surface）**不吃 backdrop** —— 只有挂
#    WS_EX_NOREDIRECTIONBITMAP 才行，而那会让窗口没有 surface，QPainter 画的全看不见
#    （QtWidgets 用不了）；`DwmExtendFrameIntoClientArea(-1,-1,-1,-1)` 在 Win8+ 已无客户区玻璃，
#    `DWMWA_USE_HOSTBACKDROPBRUSH(17)` + `GCLP_HBRBACKGROUND=-1` 在本机也填不出客户区。
#  · `DWMWA_USE_IMMERSIVE_DARK_MODE(20)` 是**独立可用**的：只下发它就能让标题栏跟随
#    **我们的**主题（浅 #FCFCFC / 深 #222222），不跟随系统 —— 现在没设，所以浅色皮肤下
#    标题栏是跟着**系统**（本机深色）走的深色带，与浅色面板不搭。**这一条已施工**（下方）。
#    详见 .workbuddy/memory/2026-09-28.md 第 42 轮。

# ---------------- 标题栏跟随**我们的**主题（唯一采用的一条） ----------------
# 不设这个属性时标题栏跟着**系统**的 App 模式走：本机是深色 ⇒ 切到浅色皮肤后，
# 浅色面板顶上仍旧压一条 #282828 的深色带，非常不搭。设了就跟我们的皮肤一致。
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def _dwm_api():
    """取 dwmapi 并把签名声明好（不声明的话 64 位下句柄会被截断）。"""
    d = ctypes.windll.dwmapi
    d.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_uint]
    d.DwmSetWindowAttribute.restype = ctypes.c_long
    return d


def set_window_caption_dark(hwnd, dark):
    """把窗口标题栏设成深色档（True）/ 浅色档（False）。返回是否真的设上了。

    老系统（缺这个属性）或属性被拒时返回 False —— 那就维持"跟随系统主题"的默认行为，
    属于**静默降级**，调用方不必写分支；返回 False 只用于自检断言。

    ⚠️ 这个属性记在 **HWND** 上：窗口一重建（show / DPI 变化）就得重新下发。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        v = ctypes.c_int(1 if dark else 0)
        hr = _dwm_api().DwmSetWindowAttribute(
            ctypes.c_void_p(int(hwnd)), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(v), 4)
        return hr == 0
    except Exception:
        return False


def set_window_noactivate(hwnd, on):
    """给窗口挂 / 摘 WS_EX_NOACTIVATE（挂的时候顺带补 WS_EX_APPWINDOW 保任务栏按钮）。

    只改**已经变了**的位：`on` 时若样式本来就在、`off` 时若本来就不在，直接返回，
    省掉一次 SetWindowPos（每次调用都会让系统重算非客户区，能省则省）。
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        u = _win_long_api()
        h = ctypes.c_void_p(int(hwnd))
        ex = u.GetWindowLongW(h, GWL_EXSTYLE)
        want = _noact_exstyle(ex, on)
        if want == ex:
            return True
        # APPWINDOW 是刚加的位时才需要 SWP_FRAMECHANGED（让任务栏立刻重读窗口样式）；
        # 单纯开关 NOACTIVATE 是即时生效的，不必动非客户区，免得窗口抖一下。
        need_frame = bool(want & WS_EX_APPWINDOW) and not (ex & WS_EX_APPWINDOW)
        u.SetWindowLongW(h, GWL_EXSTYLE, want)
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        if need_frame:
            flags |= SWP_FRAMECHANGED
        u.SetWindowPos(h, None, 0, 0, 0, 0, flags)
        return True
    except Exception:
        return False


def window_noactivate(hwnd):
    """读回 WS_EX_NOACTIVATE 是否真的挂上了（自检用，别信界面）。"""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        u = _win_long_api()
        return bool(u.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE)
                    & WS_EX_NOACTIVATE)
    except Exception:
        return False


class _WinMsg(ctypes.Structure):
    """Win32 MSG 的前几个字段。nativeEvent 里只读 message 判消息类型。

    **必须与真实内存布局对齐**：hwnd / wParam / lParam 在 64 位下都是 8 字节，
    所以用 c_void_p 而不是 c_uint —— 用错了 message 之后的字段全部错位。
    """

    _fields_ = [("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long)]


# ---------------- 牌名汉化 ----------------
# 从游戏自己的 Unity Localization 串表里抽出来的（tools/loc_build.py 生成）。
# 读不到就退化成英文原名，不影响功能。
_CARD_ZH = load_card_i18n()["by_name"]
# 大小写不敏感索引：串表里 `TOASTY`/`SPICY TOASTY` 等 16 条是全大写，而游戏运行时
# 给的显示名是 `Toasty`（首字母大写）→ 直接查表查不到，界面上就漏成英文。
# 表内无同名冲突（已验证），所以小写键做兜底是安全的。
_CARD_ZH_CI = {}
for _k, _v in _CARD_ZH.items():
    _CARD_ZH_CI.setdefault(_k.lower(), _v)

# 牌库判别诊断：内存里同时躺着营地/战斗好几份牌库，"挑错了"是最容易复发的毛病。
# 打开开关（SET SHROOM_DECK_DIAG=1）会把每次读到的候选列表写到 %TEMP%，
# 平时完全无副作用，也不会往交付目录里丢文件。
DECK_DIAG = os.environ.get("SHROOM_DECK_DIAG") == "1"
DECK_DIAG_PATH = os.path.join(tempfile.gettempdir(), "shroom_deck_diag.json")


def card_zh(name):
    """英文牌名 → 中文；查不到原样返回。

    先精确查，再忽略大小写查（`Toasty` → `TOASTY` 的「烤菇」）。
    """
    if not name:
        return name
    hit = _CARD_ZH.get(name)
    if hit is not None:
        return hit
    return _CARD_ZH_CI.get(str(name).lower(), name)


def _html_esc(s):
    """塞进 QLabel 富文本前转义。牌名里有 & < > 会把富文本吃坏。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------- 主题调色板（白天 / 黑夜） ----------------
# 两套皮肤共用**同一组角色名**。绘制代码读的全是模块级变量，所以换肤 = 把这组变量
# 整体换掉（`use_palette()`）+ 重建 QSS 下发 —— QSS 是 import 时算死的一整串字符串，
# 不重建它就永远是老配色（见 `build_qss`）。
#
# 取色原则（用户明确要求 / finesse-ui 的 craft floor）：
#   · 两套皮肤的 HSV 饱和度都压在 40% 以下；
#   · 不用纯黑纯白 —— 黑夜底是带蓝灰偏的 #15171A 系，正文是 #E8EAED 而不是 #FFF；
#   · 黑夜的强调色比浅色**更亮但更不艳**（#6E93AB，S≈36%，浅色那支是 39%）：
#     深底上蓝不提亮就读不出来，提亮的同时把饱和度再压一点才不会发荧光；
#   · 黑夜的背景光斑**比底色亮**（不是更暗）：毛玻璃要有可扩散的明暗层次才读得出
#     是玻璃，全黑一片就退化成一块死板。
# 例外：警示色 / 成功色是语义色，用户要它"一眼认得出"。浅色里就已经是
# C_WARN #A8703F（S≈62%）、C_OK #4E8A6B（S≈43%）—— 黑夜这两支的饱和度**都比浅色低**，
# 所以两套皮肤之间是自洽的，只是这两支不参与 40% 那条线。
LIGHT = dict(
    # 文字 / 线条
    C_TEXT="#1D1D1F", C_SUB="#7C7C82", C_LINE="rgba(28, 38, 52, 0.07)",
    # 强调色（主按钮 / 开启状态字 / 悬停图标）
    C_ACCENT="#4A7C9B", C_ACCENT_D="#3F6C88", C_ACCENT_P="#38607A",
    C_COST="#5C7284",           # 费用字样（低饱和蓝灰 S≈26%，压在 40% 红线内）
    C_ON_ACCENT="#FFFFFF",      # 铺在强调色上的字
    # 胶囊液面
    C_LIQ_L="#C2D6E4", C_LIQ_R="#D6E3ED", C_LIQ_EDGE="#A9C4D8",
    # 运行状态页底色：胶囊液体那套色**再深一档**（用户看过后要求"背景再深一点"；
    # 同样是低饱和蓝（S≈34%，比胶囊液面的 39% 还低），只把明度压下去 L 0.83→0.71 / 0.86→0.79）
    C_SHEET_L="#9DBACF", C_SHEET_R="#B7CBDB",
    SHEET_CARD_FILL=None,       # 浅色：状态卡照旧走毛玻璃（页面底比状态页浅，天然浮起来）
    # 语义色
    C_OK="#4E8A6B", C_WARN="#A8703F", C_WARN_H="#96632F", C_WARN_P="#8A5A2A",
    C_DOT_OFF="#C9C9CE", C_STATE_OFF="#9A9AA0",
    # 禁用态
    C_DIS_FG="#DCE4EA", C_DIS_BG="#8FA9BA",
    # 幽灵按钮（浅白玻璃底那三个）
    C_GHOST_BG="rgba(255,255,255,0.66)", C_GHOST_BD="rgba(255,255,255,0.85)",
    C_GHOST_BG_H="rgba(255,255,255,0.92)", C_GHOST_BG_P="rgba(236,240,244,0.95)",
    C_GHOST_FG_DIS="#A9B4BC", C_GHOST_BD_DIS="rgba(255,255,255,0.62)",
    # 玻璃卡片的"白纱"厚度：卡片 = 模糊过的背景 + 这么多白 + 发丝边。
    # 浅色靠"更白"表达层级，黑夜靠"更亮一点点"表达，数值差一个数量级是正常的。
    C_VEIL_RGB=(255, 255, 255), VEIL_CAP=0.62, VEIL_CARD=0.60, VEIL_CARD_OPEN=0.68,
    VEIL_HOVER_STEP=0.10,
    # 发丝描边（悬停时收紧变实）。透明度搬到材质层（HAIR_PANEL_A / HAIR_INNER_A /
    # HAIR_HOVER_A），这里只留**颜色**：规格是 1px 半透明白描边，但浅色底上白线等于没画，
    # 所以浅色用深发丝 —— 变的只是颜色，透明度比值与规格一致。
    C_HAIR_RGB=(28, 38, 52),
    TOP_HL_A=235,                                      # 卡片顶部那道内高光
    C_HOVER_A=170,                                     # 圆形图标按钮的悬停底盘
    # 背景
    BG_STOPS=("#F9FAFC", "#F6F8FA", "#F3F6F9"),
    C_PAGE_FALLBACK="#F4F6F8",
    BLOBS=(
        (0.14, 0.05, 0.52, "#E4EBF3"),
        (0.93, 0.26, 0.46, "#E3EDE7"),
        (0.52, 0.70, 0.50, "#E6E9F3"),
        (0.09, 0.58, 0.42, "#E7EBF2"),
        (0.80, 0.94, 0.46, "#F0E9E0"),
    ),
    # 其它
    C_SBAR_RGB=(74, 124, 155),
    C_SHADOW_RGB=(44, 56, 70), SHADOW_MUL=1.0,
    GLOW_BOOST=1.0,
    # 单色荧光：强调色 (#4A7C9B) 的**提亮档**。淡底上发光得比底亮才读得出"在发光"，
    # 直接拿强调色当光会看起来像一道描边（S≈37%，仍在 40% 那条红线内）
    GLOW_MONO="#7CABC6",
    # 控制台分段控件的轨道底：比卡片底略深一点点，靠发丝边定形（不是一块水泥灰）。
    # 用 (RGB, alpha) 而不是 "rgba(...)" 字符串 —— 那是 QSS 语法，**QColor 不认**，
    # 自绘的地方必须给数值。同族的还有 C_HAIR_RGB / HAIR_A。
    C_SEG_TRACK_RGB=(28, 38, 52), C_SEG_TRACK_A=16,
)

DARK = dict(
    C_TEXT="#E8EAED", C_SUB="#9BA1A8", C_LINE="rgba(255, 255, 255, 0.07)",
    C_ACCENT="#6E93AB", C_ACCENT_D="#82A6BC", C_ACCENT_P="#5C8098",
    C_COST="#8FA2B0",
    C_ON_ACCENT="#F2F5F7",
    # 液面：暗底上液面要比底色**亮**才看得出"填满了"，所以整体抬到 #2E4050 一带
    C_LIQ_L="#2E4050", C_LIQ_R="#3B4E5E", C_LIQ_EDGE="#54697A",
    C_SHEET_L="#232C33", C_SHEET_R="#2C363E",
    # 黑夜：状态页比页面底**亮**，所以状态卡必须反过来比状态页更亮才读得出层级。
    # 材质改了：以前这里铺的是**不透明实色** `#36424B`，违反 PromptCard 规格的原则 1
    # （"只有一种材质语言，不混用实色卡片"）和原则 6（"必须真正透出被覆盖的背景"）。
    # 现在改成同一支颜色的**半透明 tint**（α205 ≈ 80%）：仍然把卡面抬到比状态页亮一档，
    # 但底下的页面渐变能透上来一点点，卡读起来还是玻璃而不是一块色板。
    SHEET_CARD_FILL=(54, 66, 75, 205),
    C_OK="#5F9E82", C_WARN="#B08A63", C_WARN_H="#C39A72", C_WARN_P="#9C7A57",
    C_DOT_OFF="#5C6269", C_STATE_OFF="#7E858C",
    C_DIS_FG="#8A9299", C_DIS_BG="#3A4249",
    # 黑夜的"幽灵按钮"就是一层很薄的提亮：暗底上 0.66 的白会直接炸成一块亮斑
    C_GHOST_BG="rgba(255,255,255,0.07)", C_GHOST_BD="rgba(255,255,255,0.11)",
    C_GHOST_BG_H="rgba(255,255,255,0.12)", C_GHOST_BG_P="rgba(255,255,255,0.05)",
    C_GHOST_FG_DIS="#6A7178", C_GHOST_BD_DIS="rgba(255,255,255,0.06)",
    C_VEIL_RGB=(255, 255, 255), VEIL_CAP=0.055, VEIL_CARD=0.055, VEIL_CARD_OPEN=0.075,
    VEIL_HOVER_STEP=0.035,
    # 暗色用**白**发丝 —— 与 PromptCard 规格同色（规格就是深色玻璃 + 白描边）；
    # 透明度同样搬到材质层，这里只留颜色。
    C_HAIR_RGB=(255, 255, 255),
    TOP_HL_A=26,
    C_HOVER_A=26,
    BG_STOPS=("#171A1D", "#141619", "#121417"),
    C_PAGE_FALLBACK="#16181B",
    BLOBS=(
        (0.14, 0.05, 0.52, "#233039"),
        (0.93, 0.26, 0.46, "#1F2C28"),
        (0.52, 0.70, 0.50, "#242C3A"),
        (0.09, 0.58, 0.42, "#222B33"),
        (0.80, 0.94, 0.46, "#2A2621"),
    ),
    C_SBAR_RGB=(150, 180, 200),
    # 暗底上投影要更狠才读得出"浮起"，而纯黑投影在暗底上是无声的 —— 抬 peak 补偿
    C_SHADOW_RGB=(0, 0, 0), SHADOW_MUL=2.0,
    GLOW_BOOST=1.25,
    # 单色荧光：暗底上直接用强调色本身就够了（它比卡片底亮，天然像光）
    GLOW_MONO="#6E93AB",
    C_SEG_TRACK_RGB=(255, 255, 255), C_SEG_TRACK_A=18,
)

THEMES = {"light": LIGHT, "dark": DARK}
THEME_ORG, THEME_APP = "XIZI", "ShroomTrainer"
DECK_DESC_PX = 10           # 牌库卡里小字（「还有 N 张」）的字号；QSS 的 px 在富文本里不继承

# ---- 悬停荧光：沿描边绕行的一圈多色极光 ----
# 发光感靠「亮核 + 内晕」堆出来，而不是靠把饱和度拉满 —— 这样在浅色玻璃上
# 依然读得出是"在发光"，又不会把整块面板的克制调性掀翻。
NEON_RING = (
    (0.00, (126, 214, 232)),   # 青
    (0.22, (150, 180, 240)),   # 雾蓝
    (0.45, (192, 158, 234)),   # 淡紫
    (0.68, (236, 156, 198)),   # 藕粉
    (0.88, (240, 194, 148)),   # 暖杏
    (1.00, (126, 214, 232)),   # 回到起点，绕行一圈无缝
)
GLOW_FPS_MS = 33               # 30fps：够顺，又不至于让 CPU 空转
GLOW_SPIN = 3.6                # 每帧绕行角度 ≈108°/s → 绕一圈约 3.4s
GLOW_BREATH = 0.085            # 呼吸相位步长（周期 74 帧 ≈ 2.4s，与绕行不同步）
# 语义色 / 光斑 / 投影颜色都已搬进上面的主题调色板（原先写死在这一段）

# ---- 荧光模式（控制台卡在三个状态之间切） ----
# "color" = 上面那一圈多色极光；"mono" = 跟主题走的强调色（见各主题的 GLOW_MONO）；
# "off"   = 完全不画，并且**连 30fps 的定时器都不起**（不显示的东西不渲染）。
GLOW_MODE = "color"
GLOW_MODES = ("color", "mono", "off")
GLOW_MODE_LABELS = {"color": "彩色", "mono": "单色", "off": "关闭"}

# 单色模式的绕行色环：同一个色相，靠**明暗**沿环起伏。
# ⚠️ 不能把颜色一统了事 —— 圆锥渐变里颜色若处处相同，绕行就彻底看不出来，
#    动效会从"极光在扫"退化成"一圈死光"。这里用亮→暗→亮的彗尾，转起来依然读得出。
GLOW_MONO_SWEEP = (
    (0.00, 1.00),
    (0.34, 0.30),
    (0.62, 0.12),
    (1.00, 1.00),      # 与 0.00 同值，绕行一圈无缝
)


def glow_ring_stops():
    """当前荧光模式下的绕行色环：`[(位置, (r,g,b), 亮度系数)]`。

    彩色模式每个停靠点亮度恒 1；单色模式统一色相、只让亮度起落。
    `GLOW_MONO` 由 `use_palette()` 推进模块全局，所以这里必须**运行时读**。
    """
    if GLOW_MODE == "mono":
        c = QColor(GLOW_MONO)
        rgb = (c.red(), c.green(), c.blue())
        return [(p, rgb, m) for p, m in GLOW_MONO_SWEEP]
    return [(p, rgb, 1.0) for p, rgb in NEON_RING]

APPID = "3271280"
GAME_EXE = "Shroom and Gloom.exe"

# ---- 阴影参数 ----
# 卡片宽 528 而面板只 576，左右边距各 24px —— 阴影 sigma 一旦放大，它的「尾巴」
# 就会一路铺满整块面板，上下又被相邻卡片切出直边，整条读起来就是一块带棱角的灰板。
# 实测对比（mask alpha）：
#   sigma=28,peak=0.22 -> 下缘往下 27/24/20/17（20px 只衰减 1.6 倍，几乎是平台），
#                         左端 24px 处仍有 alpha=8 -> 横贯全宽的灰带。
#   sigma=12,peak=0.20 -> 下缘往下 24/17/10/6（14px 衰减 4 倍），边距 24px 处 alpha=1。
# 所以「悬停=浮起」不再靠加大 sigma，而是同一形状下移 + 略强，形状始终贴着卡片。
# ---- 玻璃材质：PromptCard 规格（本文件里关于"材质"的唯一权威参数）----
# 来源：Chrome 扩展 **PromptCard - Image to Prompt** 弹窗的深色毛玻璃材质
# （规格原文：面底 rgba(18,18,16,.55) / blur(40px) saturate(140%) / 1px rgba(255,255,255,.12)
#   描边 / 圆角 20·14·999 三级 / 阴影 0 24px 60px rgba(0,0,0,.45) /
#   内层卡 rgba(255,255,255,.04) + 1px rgba(255,255,255,.08) 描边 / 无实线分隔 /
#   文字只有白+透明度 / 动效 180–220ms）。
#
# ⚠️ 用户 2026-09-28 的要求是「**只学材质**，强调色和具体功能动效都不变」。所以这里
#    搬过来的是**材质的结构与可测参数**，不是它的配色：规格那套是"深色玻璃 + 白字"，
#    而本应用有浅/暗两套皮肤、默认是苹果浅色风（用户长期硬约束）。逐条对应关系：
#   · 模糊/饱和度 —— **照搬**。本应用面板底是合成渐变 + 柔光斑，本来就没有可辨识内容，
#     所以"透光但不可辨识"天然成立；把盒半径从 7 抬到 18（三次 ≈ 高斯 σ≈30）让光斑更糊，
#     再加上 saturate(140%) —— 后者才是材质"有色相、不发灰"的关键。
#   · 圆角三级 —— **照搬**：R_SURFACE 20 / R_INNER 14 / pill = 高:2。不允许出现第四种。
#   · 1px 半透明描边 —— **照搬数字**（面板 .12 / 内层 .08），只有**颜色**随主题换：
#     暗色用白（规格原样），浅色必须用深发丝，否则浅底上的白描边等于没画。
#   · 大范围软阴影 —— **无法照搬**，这是布局决定的硬约束：卡片 528 宽而面板只 576，
#     左右各 24px 沟槽；σ 一大阴影尾巴就铺满整面板、又被相邻卡切出直边，
#     实测 σ=28 时 24px 外仍有 alpha=8（就是一条横贯全宽的灰带）。
#     取"贴着卡片的大范围软阴影"作为最接近的替代（见 SHADOW_*），并留出脱离感（dy 加大）。
#   · 内层卡"超薄填充 + 靠描边分层" —— 主题相关：暗色按规格走（见 SHEET_CARD_FILL 改成
#     **半透明** tint，不再是不透明实色 —— 规格原则 1/6 明确禁止实色卡片）；
#     浅色反过来必须靠"更白"分层，因为浅底上 .04 的白等于没有。
#   · 无实线分隔 —— 本应用没有实线分隔（`C_LINE` 已无引用），天然满足。
#   · 动效 180–220ms —— 不动（用户要求），且现状全部 ≤400ms 的上限。
MAT_BLUR_R = 18               # 盒式模糊半径（跑三次 ≈ 高斯 σ≈30）
MAT_SAT = 1.40                # 饱和度增强 1.40×  = 规格的 saturate(140%)
MAT_GRAIN = 0.10              # 卡内颗粒不透明度（卡内要比卡外更"干净"）
R_SURFACE = 20.0              # 最外层玻璃面圆角（规格 20px）
R_INNER = 14.0                # 嵌在另一个玻璃面**里面**的卡圆角（规格 14px）
# 描边透明度：面板 .12 / 内层 .08（规格原值 ×255）。浅色用深发丝，同一个比值，
# 但整体压低约 25% —— 深色线画在浅底上比白线画在暗底上"更显眼"，不压会跳出来。
HAIR_PANEL_A = {"light": 24, "dark": 31}
HAIR_INNER_A = {"light": 16, "dark": 20}
HAIR_HOVER_A = {"light": 30, "dark": 46}      # 悬停时收紧一点：用手碰到它了
# 投影**颜色**与强度系数在主题里（C_SHADOW_RGB / SHADOW_MUL）：暗底上纯黑投影很无声，
# 需要抬 peak 才读得出"浮起"；而浅底上用纯黑投影会脏，用的是带蓝灰偏的 (44,56,70)。
SHADOW_BASE = dict(sigma=12.0, dy=6.0, peak=0.16)
SHADOW_HOVER = dict(sigma=14.0, dy=9.0, peak=0.13)

def build_qss():
    """按**当前**调色板生成样式表。

    为什么是函数而不是模块常量：主题要能在运行时切（标题栏右上角那个太阳/月亮开关），
    而 QSS 是一整串 import 时就算死的字符串 —— 不重新生成再 `setStyleSheet` 下发，
    换肤就只换了"自己画出来"的部分，所有控件文字会永远停在老配色。
    """
    return f"""
QLabel {{ color: {C_TEXT}; background: transparent; }}
QLabel#title {{ font-size: 17px; font-weight: 700; color: {C_TEXT}; }}
QLabel#rowTitle {{ font-size: 13.5px; font-weight: 600; color: {C_TEXT}; }}
QLabel#rowDesc {{ font-size: 11px; color: {C_SUB}; }}
QLabel#cardTitle {{ font-size: 12.5px; font-weight: 700; color: {C_SUB}; }}
QLabel#statKey {{ font-size: 12px; color: {C_SUB}; }}
QLabel#statVal {{ font-size: 12.5px; font-weight: 600; color: {C_TEXT}; }}
QLabel#deckItem {{ font-size: 11.5px; color: {C_TEXT}; }}
QLabel#deckWarn {{ font-size: 11px; color: {C_WARN}; }}
/* 牌库顺序 · 横排：序号小字 + 牌名深色 + 费用淡字，都压在 11.5px 一档，
   靠颜色分层而不是靠字号，横排才不会高一块矮一块。 */
QLabel#deckIdx {{ font-size: 10.5px; color: {C_SUB}; }}
QLabel#deckName {{ font-size: 11.5px; color: {C_TEXT}; }}
QLabel#deckCost {{ font-size: 11.5px; color: {C_COST}; }}
QLabel#deckMore {{ font-size: 10.5px; color: {C_SUB}; }}
/* 打法建议 · 每步的目标：「→ ① 1/5」。
   ⚠️ 必须**亮色正文**（C_TEXT），不能用 deckMore 那种小灰字 —— 用户实测反馈
   「箭头和血量标识显示为灰色，我需要白色更显眼」。字号也抬半档 + 加粗，
   横排五格时这一行仍然读得清。 */
QLabel#planTgt {{ font-size: 11.5px; font-weight: 600; color: {C_TEXT}; }}
/* 遗忘卡：滚轮选卡区。高亮行的牌名放大半档 + 深色，靠字号+颜色双重分层，
   不靠背景块 —— 背后是毛玻璃，一块实底色反而显得廉价。 */
QLabel#forgetIdle {{ font-size: 11.5px; color: {C_SUB}; }}
QLabel#forgetName {{ font-size: 11.5px; color: {C_TEXT}; }}
QLabel#forgetNameOn {{ font-size: 13px; font-weight: 600; color: {C_TEXT}; }}
QLabel#forgetMeta {{ font-size: 11.5px; color: {C_COST}; }}
QLabel#forgetCount {{ font-size: 11px; color: {C_OK}; }}
QLabel#forgetNote {{ font-size: 10.5px; color: {C_SUB}; }}
QLabel#forgetWarn {{ font-size: 11px; color: {C_WARN}; }}
QLabel#forgetArm {{ font-size: 11px; color: {C_WARN}; }}
/* 卡内两个按钮：矮胶囊，半径=高/2，与底部按钮同族圆角语言 */
QPushButton#fgGo {{
    background: {C_ACCENT}; border: 1px solid {C_ACCENT}; color: {C_ON_ACCENT};
    border-radius: 15px; font-size: 12px; font-weight: 600; padding: 0px 16px;
}}
QPushButton#fgGo:hover {{ background: {C_ACCENT_D}; }}
QPushButton#fgGo:pressed {{ background: {C_ACCENT_P}; }}
QPushButton#fgGo:disabled {{ color: {C_DIS_FG}; background: {C_DIS_BG}; border-color: {C_DIS_BG}; }}
QPushButton#fgGo[armed="true"] {{ background: {C_WARN}; border-color: {C_WARN}; }}
QPushButton#fgGo[armed="true"]:hover {{ background: {C_WARN_H}; }}
QPushButton#fgCancel {{
    background: {C_GHOST_BG}; border: 1px solid {C_GHOST_BD};
    color: {C_TEXT}; border-radius: 15px; font-size: 12px; font-weight: 600; padding: 0px 16px;
}}
QPushButton#fgCancel:hover {{ background: {C_GHOST_BG_H}; }}
QPushButton#fgCancel:pressed {{ background: {C_GHOST_BG_P}; }}
/* 收起态的入口按钮：白玻璃底 + 强调色字，比 fgCancel 明确、又比 fgGo 克制 ——
   它是"打开一个界面"，不是"执行破坏性操作"，不该抢成蓝色主按钮。 */
QPushButton#fgOpen {{
    background: {C_GHOST_BG}; border: 1px solid {C_GHOST_BD};
    color: {C_ACCENT_D}; border-radius: 15px; font-size: 12px; font-weight: 600;
    padding: 0px 16px;
}}
QPushButton#fgOpen:hover {{ background: {C_GHOST_BG_H}; color: {C_ACCENT}; }}
QPushButton#fgOpen:pressed {{ background: {C_GHOST_BG_P}; color: {C_ACCENT_D}; }}
QPushButton#fgOpen:disabled {{ color: {C_GHOST_FG_DIS}; border-color: {C_GHOST_BD_DIS}; }}
QLabel#conn {{ font-size: 12px; color: {C_SUB}; }}
QLabel#state {{ font-size: 12px; font-weight: 600; color: {C_STATE_OFF}; }}
QLabel#msg {{ font-size: 11px; color: {C_SUB}; }}

QPushButton#act {{
    background: {C_GHOST_BG};
    border: 1px solid {C_GHOST_BD};
    border-radius: 21px;          /* 按钮固定高 42 -> 半径 = 高/2 才是胶囊 */
    font-size: 13px; font-weight: 600; color: {C_TEXT};
    padding: 0px 14px;
}}
QPushButton#act:hover {{ background: {C_GHOST_BG_H}; }}
QPushButton#act:pressed {{ background: {C_GHOST_BG_P}; }}

QPushButton#primary {{
    background: {C_ACCENT}; border: 1px solid {C_ACCENT}; color: {C_ON_ACCENT};
    border-radius: 21px;
    font-size: 13px; font-weight: 600;
    padding: 0px 14px;
}}
QPushButton#primary:hover {{ background: {C_ACCENT_D}; }}
QPushButton#primary:pressed {{ background: {C_ACCENT_P}; }}
QPushButton#primary:disabled {{ color: {C_DIS_FG}; background: {C_DIS_BG}; border-color: {C_DIS_BG}; }}
"""


def use_palette(name):
    """把主题 `name` 的调色板推进模块全局。**只改颜色变量，不碰任何控件。**

    绘制代码读的都是模块级变量（`QColor(C_TEXT)`、`BLOBS`、`C_SHEET_L`…），
    所以这一句就等于换肤；控件侧要做的另外两件事（重新下发 QSS、换背景/阴影贴图）
    在 `Panel.apply_theme()` 里，因为那些得知道"谁是面板"。
    """
    global THEME_NAME, QSS, HAIR_A, HAIR_A_H, HAIR_A_INNER
    pal = THEMES[name]
    THEME_NAME = name
    g = globals()
    for k, v in pal.items():
        g[k] = v
    # 描边透明度来自**材质层**（规格：面板 .12 / 内层 .08 / 悬停收紧），按主题取值。
    # 放在这里而不是各调色板里，是为了让"规格三级"集中在一处看得见、改得动。
    HAIR_A = HAIR_PANEL_A[name]
    HAIR_A_INNER = HAIR_INNER_A[name]
    HAIR_A_H = HAIR_HOVER_A[name]
    QSS = build_qss()
    return QSS


use_palette("light")


# ============================ 图像工具（只跑一次，结果缓存） ============================

def _box1(a, r, axis):
    """沿 axis 的一维盒式模糊（cumsum 实现，边缘复制）。"""
    n = a.shape[axis]
    k = 2 * r + 1
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    ap = np.pad(a, pad, mode="edge")
    cs = np.cumsum(ap, axis=axis, dtype=np.float32)
    zshape = list(cs.shape)
    zshape[axis] = 1
    cs = np.concatenate([np.zeros(zshape, np.float32), cs], axis=axis)
    hi = [slice(None)] * a.ndim
    hi[axis] = slice(k, k + n)
    lo = [slice(None)] * a.ndim
    lo[axis] = slice(0, n)
    return (cs[tuple(hi)] - cs[tuple(lo)]) / float(k)


def box_blur(a, r, passes=3):
    """三次盒式模糊 ≈ 高斯模糊。"""
    if r < 1:
        return a
    for _ in range(passes):
        a = _box1(a, r, 0)
        a = _box1(a, r, 1)
    return a


def _gray_array(img):
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    raw = np.frombuffer(bytes(ptr), np.uint8).reshape(h, img.bytesPerLine())
    return raw[:, :w].astype(np.float32)


def _argb_pixmap(w, h, rgba):
    img = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32)
    return QPixmap.fromImage(img.copy())


def shadow_mask(w, h, radius, sigma=15.0, dy=8, peak=0.24):
    """阴影的 alpha 掩膜（uint8 [H, W]）+ pad。make_shadow 与自检脚本共用。

    sigma 直接是标准差（像素）。三次盒式模糊的 σ ≈ 盒半径，所以 r 取 round(sigma)。

    以前写成 `box_blur(m, blur // 5)`，blur=26 -> σ≈5：只糊了 ±15px 的边，
    阴影紧贴形状成了一条深色硬边，拐角处看得一清二楚，观感就是「有棱角」。
    """
    r = max(2, int(round(sigma)))
    pad = int(3.0 * sigma) + 6
    W, H = int(w + pad * 2), int(h + pad * 2 + abs(dy))
    mi = QImage(W, H, QImage.Format.Format_Grayscale8)
    mi.fill(0)
    p = QPainter(mi)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(pad, pad + max(0, dy), w, h), radius, radius)
    p.end()

    m = np.clip(box_blur(_gray_array(mi), r, 3), 0, 255)
    return np.clip(m * peak, 0, 255).astype(np.uint8), pad


def make_shadow(w, h, radius, sigma=15.0, dy=8, color=(44, 56, 70), peak=0.24):
    """预渲染软阴影。返回 (QPixmap, pad)。"""
    a, pad = shadow_mask(w, h, radius, sigma, dy, peak)
    H, W = a.shape
    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[..., 0] = color[2]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[0]
    rgba[..., 3] = a
    return _argb_pixmap(W, H, rgba), int(pad)


def _zero_mean_texture(rng, h, w, lo=0, hi=18, dark=(56, 64, 78), light=(255, 255, 255)):
    """零均值纹理：压暗点与提亮点各半，模糊后互相抵消，不会让整体发灰。"""
    a = rng.integers(lo, hi + 1, (h, w))
    d = rng.random((h, w)) < 0.5
    rgb = np.empty((h, w, 3), np.uint8)
    for i in range(3):
        rgb[..., i] = np.where(d, dark[i], light[i])
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = a
    return out


def make_grain(size=96, seed=20260922):
    rng = np.random.default_rng(seed)
    return _argb_pixmap(size, size, _zero_mean_texture(rng, size, size, lo=0, hi=14))


def make_speckle(w, h, density=1.0 / 16.0, seed=4242, lo=3, hi=13):
    """细颗粒纹理：给模糊准备可扩散的细节。

    全分辨率直接生成，不做缩放 —— QImage.scaled 的平滑插值会把低 alpha 洗掉，
    纹理强度几乎归零（实测只有设定值的 1/10），毛玻璃就没东西可扩散了。
    """
    rng = np.random.default_rng(seed)
    tex = _zero_mean_texture(rng, h, w, lo=lo, hi=hi)
    keep = rng.random((h, w)) < density
    tex[..., 3] = np.where(keep, tex[..., 3], 0).astype(np.uint8)
    return _argb_pixmap(w, h, tex)


class Frost:
    """页面底 + 其模糊版本 + 颗粒。卡片靠裁切采样 blurred 实现真毛玻璃。"""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.grain = make_grain()

        # 1) 页面底：渐变 + 低饱和柔光斑 + 散点 + 颗粒（三样都随主题，见 BG_STOPS / BLOBS）
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, w * 0.6, h)
        g.setColorAt(0.0, QColor(BG_STOPS[0]))
        g.setColorAt(0.55, QColor(BG_STOPS[1]))
        g.setColorAt(1.0, QColor(BG_STOPS[2]))
        p.fillRect(0, 0, w, h, g)
        for fx, fy, fr, col in BLOBS:
            c = QColor(col)
            rg = QRadialGradient(QPointF(w * fx, h * fy), w * fr)
            c0 = QColor(c)
            c0.setAlpha(200)
            rg.setColorAt(0.0, c0)
            rg.setColorAt(0.55, QColor(c.red(), c.green(), c.blue(), 110))
            rg.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(w * fx, h * fy), w * fr, w * fr)
        # 两层不同种子/疏密的细颗粒叠一起，避免出现规整的重复纹路
        p.drawPixmap(QPoint(0, 0), make_speckle(w, h, density=1.0 / 16.0, seed=4242, lo=3, hi=13))
        p.drawPixmap(QPoint(0, 0), make_speckle(w, h, density=1.0 / 40.0, seed=777, lo=4, hi=16))
        p.end()
        self.bg = QPixmap.fromImage(img)

        # 2) 模糊版：三次盒式模糊（真高斯近似），只算一次
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        raw = np.frombuffer(bytes(ptr), np.uint8).reshape(h, img.bytesPerLine())
        arr = raw[:, : w * 4].reshape(h, w, 4).astype(np.float32)
        for ch in range(4):
            arr[..., ch] = box_blur(arr[..., ch], MAT_BLUR_R, 3)
        # 饱和度增强（PromptCard 材质的 `saturate(140%)`）：把每个通道往"自身亮度"的
        # 反方向推 40%。这是材质"透得出色相、不发灰"的关键 —— 纯模糊只会把颜色洗淡。
        # 只动 RGB、不动 A（背景图本来全不透明）。等价于线性 SATURATION 混合。
        _lum = arr[..., :3].mean(axis=2, keepdims=True)
        arr[..., :3] = np.clip(_lum + (arr[..., :3] - _lum) * MAT_SAT, 0, 255)
        self.blurred = _argb_pixmap(w, h, np.clip(arr, 0, 255).astype(np.uint8))


# ============================ 毛玻璃基类 ============================

class GlassBase(QWidget):
    """带悬停进度（0..1）的毛玻璃容器。阴影由 Panel 统一绘制，悬停只改绘制偏移。

    还带**液面填充**能力（`_liq_to` / `_paint_liquid`）：胶囊的「从左填满并荡漾」、
    牌库卡与遗忘卡开关时的整卡充斥，用的都是这一套 —— 三处共用一个进度量 `_liq_p`。
    液体只负责"底色"，卡里的文字是子控件，天生画在父控件之上，不会被盖住。
    """

    # 液面动效的时长（胶囊用了很久的取值，抽上来给所有会填液体的控件共用）
    LIQ_TRAVEL = 300        # 填满 / 抽空
    LIQ_SETTLE = 300        # 到位后那一记荡漾
    LIQ_WAVE = 5.0          # 液面前沿的基准波幅

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frost = None
        self.frost_root = None
        self._hover = 0.0
        self._hanim = QPropertyAnimation(self, b"hover", self)
        # 180ms：比"浮起"再柔一点，荧光的淡入淡出才看得出来，但仍在 400ms 内
        self._hanim.setDuration(180)
        self._hanim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hanim.finished.connect(self._on_hover_done)

        # --- 悬停荧光的状态 ---
        self._glow_ang = 0.0        # 锥形渐变的起始角（每帧绕行）
        self._glow_ph = 0.0         # 呼吸相位
        self._glow_on = False       # 自检用：荧光逐帧刷新是否在跑
        self._glow_t = QTimer(self)
        self._glow_t.setInterval(GLOW_FPS_MS)
        self._glow_t.timeout.connect(self._glow_step)

        # --- 液面填充的状态 ---
        self._liq_p = 0.0           # 液面前沿进度 0..1
        self._liq_amp = 1.0         # 波幅系数（到位后衰减，静止时没那么晃）
        self._liq_travel = QPropertyAnimation(self, b"liqProgress", self)
        self._liq_travel.finished.connect(self._on_liquid_done)
        self._liq_settle = QVariantAnimation(self)
        self._liq_settle.valueChanged.connect(self._on_liquid_settle)
        self._liq_ampanim = QPropertyAnimation(self, b"liqAmp", self)
        self._liq_ampanim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._liq_ampanim.setDuration(self.LIQ_SETTLE)
        self._liq_ampanim.setStartValue(1.0)
        self._liq_ampanim.setEndValue(0.45)

    # --- 圆角（材质三级制）---
    def _radius(self):
        """本玻璃面的圆角，供"圆角只有三级"的全树扫描断言读取。

        默认 R_SURFACE(20)：**直接贴在页面背景上**的玻璃面（牌库卡 / 遗忘卡 /
        状态页）。嵌在另一个玻璃面**里面**的卡要覆写成 R_INNER(14)；胶囊覆写成
        高/2（规格里 999px 那一级）。别再往这里塞第四种值 —— 那是规格的硬性约束 1。
        """
        return R_SURFACE

    # --- 液面填充：进度量 + 驱动 ---
    def getLiqProgress(self):
        return self._liq_p

    def setLiqProgress(self, v):
        self._liq_p = float(v)
        self.update()

    liqProgress = pyqtProperty(float, fget=getLiqProgress, fset=setLiqProgress)

    def getLiqAmp(self):
        return self._liq_amp

    def setLiqAmp(self, v):
        self._liq_amp = float(v)
        self.update()

    liqAmp = pyqtProperty(float, fget=getLiqAmp, fset=setLiqAmp)

    def isLiquidFilled(self):
        return self._liq_p > 0.5

    def hasLiquid(self):
        """当前有没有液面要画（0 就是没有 —— 不必开 clip / 建路径）。"""
        return self._liq_p > 0.001

    def _liq_to(self, on, animate=True):
        """把液面推到 `on`（True 填满 / False 抽空）。**只管液面**，不管业务状态。"""
        on = bool(on)
        self._liq_travel.stop()
        self._liq_settle.stop()
        self._liq_ampanim.stop()
        if not animate:
            self._liq_p = 1.0 if on else 0.0
            self._liq_amp = 1.0
            self.update()
            return
        self._liq_amp = 1.0
        if on:
            self._liq_travel.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._liq_travel.setDuration(self.LIQ_TRAVEL)
        else:
            # 抽空：中段最快、收尾柔，读起来像被吸走
            self._liq_travel.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._liq_travel.setDuration(self.LIQ_TRAVEL)
        self._liq_travel.setStartValue(self._liq_p)
        self._liq_travel.setEndValue(1.0 if on else 0.0)
        self._liq_travel.start()

    def _on_liquid_done(self):
        """填到位后液面回弹一下再静止；抽到底不荡漾。"""
        if self._liq_p < 0.5:
            return
        self._liq_settle.stop()
        self._liq_settle.setDuration(self.LIQ_SETTLE)
        self._liq_settle.setKeyValueAt(0.00, self._liq_p)
        self._liq_settle.setKeyValueAt(0.28, 0.942)
        self._liq_settle.setKeyValueAt(0.58, 1.000)
        self._liq_settle.setKeyValueAt(0.80, 0.982)
        self._liq_settle.setKeyValueAt(1.00, 1.000)
        self._liq_settle.start()
        self._liq_ampanim.start()

    def _on_liquid_settle(self, v):
        try:
            self.setLiqProgress(float(v))
        except (TypeError, ValueError):
            pass

    def _liquid_path(self, rect, radius):
        """液面前沿：两条正弦叠加，避免纯正弦的机械感。"""
        h, w = rect.height(), rect.width()
        front = w * self._liq_p
        amp = self.LIQ_WAVE * self._liq_amp
        phase = self._liq_p * 3.0
        path = QPainterPath()
        path.moveTo(-radius, -radius)
        path.lineTo(front, -radius)
        steps = 30
        for i in range(steps + 1):
            t = i / steps
            y = h * t
            off = (math.sin(t * math.tau * 1.30 + phase) * 0.72
                   + math.sin(t * math.tau * 3.10 + phase * 1.7) * 0.34)
            path.lineTo(front + amp * off, y)
        path.lineTo(-radius, h + radius)
        path.closeSubpath()
        return path

    def _paint_liquid(self, p, path, rect, radius):
        """液面：横向由深到浅的体感 + 上下光泽 + 弯月面（前沿隆起与细高光）。

        调用前请自行判断 `hasLiquid()`；这里只负责画，并且会自己 `save/restore`。
        """
        p.save()
        p.setClipPath(path)
        lp = self._liquid_path(rect, radius)

        gh = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gh.setColorAt(0.00, QColor(C_LIQ_L))
        gh.setColorAt(0.58, QColor(C_LIQ_R))
        gh.setColorAt(1.00, QColor(C_LIQ_R))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gh)
        p.drawPath(lp)

        # 液面光泽：上亮下暗，制造液体的厚度
        gv = QLinearGradient(0, rect.top(), 0, rect.bottom())
        gv.setColorAt(0.00, QColor(255, 255, 255, 120))
        gv.setColorAt(0.42, QColor(255, 255, 255, 24))
        gv.setColorAt(1.00, QColor(120, 150, 172, 26))
        p.setBrush(gv)
        p.drawPath(lp)

        # 弯月面：先铺一层宽而淡的白形成隆起，再压一道细高光
        edge = QColor(C_LIQ_EDGE)
        edge.setAlpha(150)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 5.0))
        p.drawPath(lp)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.0))
        p.drawPath(lp)
        p.setPen(QPen(edge, 1.0))
        p.drawPath(lp)
        p.restore()

    # --- 荧光：逐帧绕行 ---
    def _glow_step(self):
        self._glow_ang = (self._glow_ang + GLOW_SPIN) % 360.0
        self._glow_ph += GLOW_BREATH
        self.update()

    def _glow_run(self, on):
        """悬停时才逐帧重绘，移开立刻停。

        不悬停就一定要把定时器停掉 —— 否则 5 张卡片各跑一个 30fps 的循环在空转，
        白白吃 CPU（这也是"响应速度优先"的一部分：不显示的东西不渲染）。

        `off` 模式同理：荧光根本不画，就绝不许起表。
        """
        on = bool(on) and GLOW_MODE != "off"
        self._glow_on = bool(on)
        if on:
            if not self._glow_t.isActive():
                self._glow_t.start()
        else:
            self._glow_t.stop()
        self.update()

    def refresh_glow(self):
        """荧光模式变了之后让这张卡立刻反映新状态（起表/收表 + 重绘）。

        悬停进度还在的过程中切模式也要照顾到：`_hover > 0.001` 说明这一轮淡入淡出
        还没走完，表得继续跑，否则切换那一帧会卡在半亮。
        """
        self._glow_run(self._hover > 0.001)

    def _on_hover_done(self):
        # 淡出跑完（_hover 回到 0）才停表，保证退场那一帧是完整淡出的
        if self._hover <= 0.001:
            self._glow_run(False)

    def getHover(self):
        return self._hover

    def setHover(self, v):
        self._hover = float(v)
        self.update()
        # 阴影由父级 Panel.paintEvent 统一绘制；只 update() 自己，父级不会重绘，
        # 悬停换阴影的那一帧就永远画不出来（表现是阴影"冻"在原地、跟卡面对不上）。
        if self.frost_root is not None:
            self.frost_root.update()

    hover = pyqtProperty(float, fget=getHover, fset=setHover)

    def _hover_to(self, target):
        self._hanim.stop()
        self._hanim.setStartValue(self._hover)
        self._hanim.setEndValue(float(target))
        self._hanim.start()

    def enterEvent(self, e):
        self._glow_run(True)
        self._hover_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0)
        super().leaveEvent(e)

    def hideEvent(self, e):
        # 窗口被关掉/卡片被藏起来时不会再收到 leaveEvent，定时器要自己收摊
        self._glow_run(False)
        super().hideEvent(e)

    # --- 共用绘制 ---
    def _paint_glass(self, p, path, rect, radius, veil_base=0.58, solid=None):
        """`solid` 给「骑在另一个覆盖层上」的玻璃卡用（只有状态页那张卡加控制台卡）。

        为什么需要特例：`_paint_glass` 取的是**页面背景**那层模糊贴图。状态卡住在
        状态页里，浅色下页面底比状态页**浅**，卡片自然显得浮起来；黑夜下页面底比
        状态页**深**，同一套算法会让卡片比它所在的那一页还暗 —— 看着像被按下去。

        材质改动（PromptCard 规格）：这里**不再铺不透明实色**，改成半透明 tint
        （`SHEET_CARD_FILL` 现在带 alpha）。规格的原则 1「不混用实色卡片」和原则 6
        「面板必须真正透出被覆盖的背景」都要求它仍然是玻璃；α≈0.80 足够把卡面抬到
        比状态页亮一档，同时底下的页面渐变仍能透上来一点。
        """
        if solid is not None:
            p.save()
            p.setClipPath(path)
            # 支持两种写法：颜色名/hex 字符串，或 (r, g, b, a) 数值元组。
            # ⚠️ 不能写 "rgba(...)" —— 那是 QSS 语法，QColor 不认（踩过）。
            c = QColor(*solid) if isinstance(solid, (tuple, list)) else QColor(solid)
            p.fillRect(rect, c)
            p.restore()
            return
        if self.frost is None or self.frost_root is None:
            return
        p.save()
        p.setClipPath(path)
        o = self.mapTo(self.frost_root, QPoint(0, 0))
        # 控件可能被挪到父窗口之外（状态页从左外侧滑入时 x 是负的），源矩形一旦越界，
        # `drawPixmap` 的行为在 Qt 里是**未定义**的。这里把源矩形夹到贴图内，落在边缘上
        # ——frost 是模糊背景，边缘和近处的颜色本来就几乎一样，看不出差别。
        # （实测：夹与不夹，卡片上的取样点只差 2/255（#f9fafc vs #f7f9fb），所以这条
        #   纯属防未定义行为的加固，**不是**任何已报现象的主因。）
        sw, sh = rect.width(), rect.height()
        sx = min(max(0.0, float(o.x())), max(0.0, self.frost.w - sw))
        sy = min(max(0.0, float(o.y())), max(0.0, self.frost.h - sh))
        p.drawPixmap(QRectF(rect), self.frost.blurred, QRectF(sx, sy, sw, sh))
        # "白纱"厚度随主题（见 VEIL_* 的说明）：浅色靠更白表达层级，黑夜靠更亮一点点
        veil = veil_base + VEIL_HOVER_STEP * self._hover
        p.fillRect(rect, QColor(C_VEIL_RGB[0], C_VEIL_RGB[1], C_VEIL_RGB[2],
                                int(255 * veil)))
        # 只留一丝极淡的齿感：卡内应当比卡外「更干净」，霜感来自背景被扩散
        p.setOpacity(MAT_GRAIN)
        p.drawTiledPixmap(rect, self.frost.grain)
        p.setOpacity(1.0)
        p.restore()

    def _paint_edge(self, p, rect, radius, radius_b=None, inner=False):
        # 外侧发丝线（悬停时收紧一点，作为「用手碰到它了」的轻提示）
        # `radius_b` 给「上圆下平」的页面用（见 StatusSheet.RADIUS_B）；
        # 缺省时下角跟上角同半径，等于原来 `drawRoundedRect` 的行为。
        # `inner=True` 给「住在另一个玻璃面里面」的卡（状态卡 / 控制台卡）——
        # PromptCard 规格里描边分两档：面板 .12、内层 .08，内层更淡才不会跟外层面打架。
        rb = radius if radius_b is None else radius_b
        p.setBrush(Qt.BrushStyle.NoBrush)
        hr, hg, hb = C_HAIR_RGB
        a0 = HAIR_A_INNER if inner else HAIR_A
        p.setPen(QPen(QColor(hr, hg, hb, int(a0 + HAIR_A_H * self._hover)), 1))
        p.drawPath(round_path(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, rb))
        # 顶部内高光：制造玻璃厚度与立体感。
        # ⚠️ 黑夜要把它压到很淡（TOP_HL_A 26）：暗底上一道 235 的白会变成一条发光边。
        g = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.42)
        g.setColorAt(0.0, QColor(255, 255, 255, TOP_HL_A))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(g, 1.4))
        p.drawPath(round_path(QRectF(rect).adjusted(1.1, 1.1, -1.1, -1.1),
                              radius - 1, max(0.0, rb - 1)))

    def _glow_brush(self, rect, alpha):
        """沿描边绕行的渐变刷。alpha 一次给足，后面靠 alpha 控制淡入淡出。

        色环由 `glow_ring_stops()` 按当前荧光模式给：彩色是多色极光，
        单色是同一色相上的明暗绕行（见那里的说明）。
        """
        g = QConicalGradient(rect.center(), self._glow_ang)
        for pos, rgb, mult in glow_ring_stops():
            c = QColor(*rgb)
            c.setAlpha(max(0, min(255, int(alpha * mult))))
            g.setColorAt(pos, c)
        return QBrush(g)

    def _paint_glow(self, p, path, rect, radius):
        """悬停荧光：贴边一圈会绕行的极光描边 + 向内晕开的光晕。

        只在卡片**内部**画 —— `QWidget` 的绘制被裁在自己的 rect 里，往外画是白画
        （真想往外发光得像 EdgeVeil 那样另起一个叠加控件，代价不值）。所以发光感
        走「亮核 + 内晕」：一条细亮线定形，往里三层越宽越淡把光"摊"出来。
        """
        if GLOW_MODE == "off":
            return
        a = self._hover
        if a <= 0.01:
            return
        a *= 0.88 + 0.12 * math.sin(self._glow_ph)      # 轻微呼吸，别让它像贴纸
        # 黑夜要抬一点：同一圈荧光贴在深底卡片上会显得比浅底弱一档（GLOW_BOOST）
        a = min(1.0, a * GLOW_BOOST)
        p.save()
        p.setClipPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 由贴边向内的**一条连续衰减带**：用很多条 1.7px 细笔画叠出来，相邻笔画间距
        # 不到 1 个像素、单步 alpha 只差几个值 → 肉眼看不出台阶。
        # （早先按 3 层大笔画画（13/8/4.6px），每层是一块纯色，边界一眼就能数出来 —— 就是用户
        #  看到的"分层"。发光要么做成连续渐变，要么就干脆别做。）
        steps = 12
        span = 8.0                      # 光晕由贴边往里铺的深度（再深就从"描边发光"变成"整片泛白"）
        for k in range(steps):
            t = k / float(steps - 1)    # 0 = 最贴边，1 = 最内
            i = 0.9 + span * t
            fade = (1.0 - t) ** 2.4     # 平滑衰减，越往里越淡
            p.setPen(QPen(self._glow_brush(rect, int(255 * 0.95 * fade * a)), 1.8))
            p.drawRoundedRect(QRectF(rect).adjusted(i, i, -i, -i),
                              max(1.0, radius - i), max(1.0, radius - i))
        p.restore()


# ============================ 胶囊（整条即开关 + 液面填充） ============================

class Capsule(GlassBase):
    """毛玻璃胶囊。开：液面从左向右填满并到位荡漾；关：从右向左抽空。

    整条可点击，没有独立开关控件——状态完全由液面表达。
    """
    toggled = pyqtSignal(bool)
    LIQ_TRAVEL = 300
    LIQ_SETTLE = 300
    # 旧名字保留（自检与历史注释在用）；真正驱动动画的时长是上面那对 LIQ_*
    TRAVEL_ON = LIQ_TRAVEL
    TRAVEL_OFF = LIQ_TRAVEL
    SETTLE = LIQ_SETTLE

    def _radius(self):
        """pill 那一级（规格的 999px）：半径恒等于高的一半，与绘制代码同源。"""
        return self.height() / 2.0

    def __init__(self, title, desc, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._checked = False

        lay = QVBoxLayout(self)
        # 左内边距给得比右大：功能名与解释别贴着胶囊左边缘（右侧还有状态字要留位）
        lay.setContentsMargins(32, 13, 20, 13)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.lb_title = QLabel(title)
        self.lb_title.setObjectName("rowTitle")
        self.lb_desc = QLabel(desc)
        self.lb_desc.setObjectName("rowDesc")
        col.addWidget(self.lb_title)
        col.addWidget(self.lb_desc)
        top.addLayout(col)                       # 不抢伸缩：宽度取内容宽
        # 中间一个伸缩项把「功能名 / 解释」推向左侧，右端留给状态字。
        # （以前这里还支持往首行塞一个输入控件（extra_widget），供「无限精力」
        #   的数值框用；该功能整体移除后这个接口没有调用点了，一并删掉。）
        top.addStretch(1)

        self.lb_state = QLabel("已关闭")
        self.lb_state.setObjectName("state")
        self.lb_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top.addWidget(self.lb_state, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)

        # 统一 64：输入控件已并入首行，不再需要第三条行，三条胶囊高度也就一致了
        self.setFixedHeight(64)

    # --- 状态 ---
    def isChecked(self):
        return self._checked

    def setChecked(self, on, animate=True, emit=True):
        on = bool(on)
        changed = on != self._checked
        self._checked = on
        self._sync_label()
        # 液面的驱动（推进 / 荡漾 / 波幅衰减）全在 GlassBase：胶囊、牌库卡、遗忘卡
        # 三处填液体用的是同一套，进度量也是同一个 `_liq_p`。
        self._liq_to(on, animate=animate)
        if changed and emit:
            self.toggled.emit(on)
        return changed

    def toggle(self):
        return self.setChecked(not self._checked)

    def _sync_label(self):
        self.lb_state.setText("已开启" if self._checked else "已关闭")
        c = C_ACCENT_D if self._checked else C_STATE_OFF
        self.lb_state.setStyleSheet(f"QLabel#state {{ font-size:12px; font-weight:600; color:{c}; }}")

    # --- 事件 ---
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and \
                self.rect().contains(e.position().toPoint()):
            self.toggle()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # --- 绘制 ---
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # 1) 毛玻璃底
        self._paint_glass(p, path, rect, radius, veil_base=VEIL_CAP)

        # 2) 液面（画法在 GlassBase._paint_liquid，和牌库卡 / 遗忘卡共用）
        if self.hasLiquid():
            self._paint_liquid(p, path, rect, radius)

        # 3) 悬停荧光（画在液面之后：液面是不透明的，画在前面会被整片盖住）
        self._paint_glow(p, path, rect, radius)

        # 4) 玻璃边缘 + 顶部高光
        self._paint_edge(p, rect, radius)
        p.end()


# ============================ 其它控件 ============================

class Dot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._c = QColor(C_DOT_OFF)

    def setColor(self, c):
        self._c = QColor(c)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c)
        p.drawEllipse(QRectF(0, 0, 10, 10))
        p.end()


class DashButton(QWidget):
    """标题栏左侧的「仪表盘」按钮：点一下**从左往右**滑出运行状态页，再点一下收回去。

    图标是**代码矢量绘制**（表盘弧 + 指针 + 中心点）：不用 emoji、不用位图 ——
    位图在 150% 缩放下会糊，矢量可以随状态随手改颜色（idle / hover / 打开）。

    点下去指针还会**原地转整整一圈**回到原位（见 `spin_once`）—— 和状态页的滑入
    同一条时间轴，页面铺到位那一刻正好转满一圈。
    """

    clicked = pyqtSignal()

    SIZE = 30                # 用户要求"再大一点"（原 26）
    # 表盘弧半径 / SIZE。
    # ⚠️ 需求变更（用户 2026-10-01）：**仪表盘要比右侧主题开关的太阳图标小一点**，
    #    不再是"等大"。原来的等大取值是 0.387（推导见下），现改为 0.330。
    #    若以后想恢复等大，把 DIAL_R 改回 0.387 即可（`ui_check` §13g 的两条断言
    #    是**读这个常量**算期望值的，改完跑一遍就知道有没有对上）：
    #      太阳图标外径 = SIZE*0.385 + (SIZE/15)/2 = 0.4183*SIZE（半径）
    #      表盘外径     = DIAL_R*SIZE + (SIZE/16)/2  →  等大时 DIAL_R = 0.387
    # 表盘的外径按 SIZE 的比例算，所以改 SIZE 时比例关系自动保持。
    # ⚠️ 改这个值要同步 `ui_check` §13g 里"差异像素是否落在指针扫过的半径内"那条
    #    （它读的是 G.DashButton.DIAL_R，别退回去写死 0.25）。
    DIAL_R = 0.330
    # 指针转一圈的默认时长。真跑的时候由 `Panel._slide_sheet` 用**状态页那条动画的
    # duration** 覆盖掉（见 `spin_once` 的 `ms` 参数）—— 两条动画同时起跑、同样时长，
    # "页面弹出完成时刚好转满一圈"就是这件事的机械结果，而不是把两个 260 各写一遍
    # 去凑。将来谁改了 SHEET_MS，旋转自动跟着走，永远不会错位。
    SPIN_MS = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 面板里所有控件一律 NoFocus
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover = False
        self._on = False
        # 指针**已转过的圈数**：0 = 原位，1 = 转满一圈（画出来和 0 完全一样）。
        # 这是纯过渡量，动画一结束就归零（见 `_on_spin_done`），所以它只表示"当下这一刻
        # 转到了哪儿"，不是持久状态。
        self._spin = 0.0
        self._spin_anim = QPropertyAnimation(self, b"spin", self)
        self._spin_anim.setDuration(self.SPIN_MS)
        # OutCubic：和全项目一致 —— 单调减速、绝不回弹（回弹会让指针往回甩一下）。
        # 缓动**不会改变总角度**：末值恒为 ±1，转的就是整好一圈，终点即原位。
        self._spin_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._spin_anim.finished.connect(self._on_spin_done)

    def isOn(self):
        return self._on

    def click(self):
        """等价于 QPushButton.click()：发一次点击。

        自绘按钮没有 QAbstractButton 那套 API，但自检和程序内部都想"凭空点一下"
        （真鼠标事件要看坐标，测起来脆），所以补这个方法把接口对齐。
        """
        self.clicked.emit()

    def setOn(self, on):
        on = bool(on)
        if on != self._on:
            self._on = on
            self.update()

    # --- 指针旋转（`spin` 是给 QPropertyAnimation 驱动的自绘量，同 ThemeButton.flip） ---
    def getSpin(self):
        return self._spin

    def setSpin(self, v):
        self._spin = float(v)
        self.update()

    spin = pyqtProperty(float, fget=getSpin, fset=setSpin)

    def spin_once(self, ms=None, reverse=False):
        """点一下：指针**原地转整整一圈**回到原位。

        `ms` 由外面给 —— 传的是状态页那条滑入/滑出动画的时长。两条动画在同一个函数里
        前后脚 `start()`，时长也一样，于是**必然同时结束**：页面铺到位的那一帧，指针
        刚好转满 360°。「页面弹出完成时刚好旋转一圈」是这个同时性保证的，不是调出来的
        数字凑巧。

        `reverse`（收回页面时）让指针倒着转一圈 —— 在同一处"转出去、转回来"，和开合页
        这个动作有一一对应的物理关系，而不是两次都朝同一边甩。

        ⚠️ 每次都从 0 重来（不是从当前值接着转）：中途连点两下时，接着转的那半圈在
        页面到位时还没转满，"一圈"这个承诺就破了。
        """
        a = self._spin_anim
        a.stop()
        if ms:
            a.setDuration(int(ms))
        self.setSpin(0.0)
        a.setStartValue(0.0)
        a.setEndValue(-1.0 if reverse else 1.0)
        a.start()

    def _on_spin_done(self):
        """转完归零：±360° 和 0° 画出来逐像素相同，所以这一步**看不出任何跳变**，
        只是把过渡量收回干净的原点（下一次要从原位起转，而不是从 1 起转）。"""
        self.setSpin(0.0)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        # 底：圆形，和底部按钮的胶囊是同一套圆角语言（半径 = 高/2）
        if self._on:
            bg = QColor(C_ACCENT)
            bg.setAlphaF(0.16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, C_HOVER_A))
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))

        # 图标：上方开口的表盘弧 + 一根偏右上的指针 + 中心轴点
        col = QColor(C_ACCENT if (self._on or self._hover) else C_SUB)
        cx, cy = r.center().x(), r.center().y()
        # ⚠️ 图标尺寸按 `SIZE` 的**比例**算，不写死像素：不然改 SIZE 的时候只有底盘
        #    变大、表盘图标还是原样，看起来反而更空更小（踩过）。
        rad = self.SIZE * self.DIAL_R
        pen = QPen(col)
        pen.setWidthF(self.SIZE / 16.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Qt 角度单位是 1/16 度、逆时针为正、0° 在 3 点钟方向。
        # 200° 起、顺时针扫 220° → 从左下经正上方到右下，就是常见仪表盘的"碗"。
        p.drawArc(QRectF(cx - rad, cy - rad, rad * 2, rad * 2), 200 * 16, -220 * 16)
        L = rad * 0.74
        # 会转的**只有指针这一根线**：表盘弧和中心轴点都是旋转对称的（轴点是个圆，
        # 转了也看不出来），转它们等于白转。绕圆心转 `360° × _spin` 就是"扫一圈"。
        # Qt 的正角度是**顺时针**（y 轴朝下），和真实表盘的走针方向一致。
        # 指针长 0.185×SIZE < 半个控件，转到任何角度都还在按钮内，不会被裁。
        p.save()
        p.translate(cx, cy)
        p.rotate(360.0 * self._spin)
        p.translate(-cx, -cy)
        p.drawLine(QPointF(cx, cy), QPointF(cx + L * math.cos(math.radians(55)),
                                           cy - L * math.sin(math.radians(55))))
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        _ax = self.SIZE * 1.8 / 26.0          # 中心轴点也按比例（26 时是 1.8）
        p.drawEllipse(QPointF(cx, cy), _ax, _ax)
        p.end()


class ThemeButton(QWidget):
    """标题栏右上角的白天/黑夜开关：点一下，**图标自己翻面**（太阳 → 月亮）。

    和 DashButton 同一套语言：纯自绘、30×30、NoFocus、悬停浅底。图标同样按 `SIZE`
    的**比例**算 —— 写死像素的话改 SIZE 只有底盘变大、图标原地不动（DashButton 踩过）。

    翻转怎么做的：把坐标原点挪到圆心，然后**同时**做两件事 ——
      ① 横向压扁 `|cos(πt)|`：1 → 0 → 1，t=0.5 时图标侧对着你、宽度归零；
      ② 绕圆心转 `-180t`。
    于是太阳是"转着缩成一条线、再转着展开成月亮"，不是简单交叉淡入淡出。
    到 t≥0.5 才换画月亮：t=0.5 那一帧宽度本来就是 0，两个图标不会同时露面。
    """

    clicked = pyqtSignal()
    SIZE = 30                # 与 DashButton 对齐（标题栏两个圆形按钮同尺寸）
    FLIP_MS = 340            # <= 400ms 的硬约束内

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 面板里所有控件一律 NoFocus
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover = False
        self._night = False
        self._t = 0.0            # 0 = 太阳（白天）/ 1 = 月亮（黑夜）
        self._anim = QPropertyAnimation(self, b"flip", self)
        self._anim.setDuration(self.FLIP_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # --- 接口对齐 QPushButton / DashButton，供自检"凭空点一下" ---
    def click(self):
        self.clicked.emit()

    def isNight(self):
        return self._night

    def setNight(self, night, animate=True):
        night = bool(night)
        self._night = night
        end = 1.0 if night else 0.0
        self._anim.stop()
        if not animate:
            self.setFlip(end)
            return
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(end)
        self._anim.start()

    def getFlip(self):
        return self._t

    def setFlip(self, v):
        self._t = max(0.0, min(1.0, float(v)))
        self.update()

    flip = pyqtProperty(float, fget=getFlip, fset=setFlip)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def _paint_sun(self, p, cx, cy, col):
        """太阳：实心圆盘 + 8 根射线。（不用 emoji —— 跨设备渲染不一致、还自带高饱和色）"""
        s = self.SIZE
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), s * 0.155, s * 0.155)
        pen = QPen(col)
        pen.setWidthF(max(1.0, s / 15.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        r0, r1 = s * 0.265, s * 0.385
        for i in range(8):
            a = math.radians(i * 45.0)
            p.drawLine(QPointF(cx + r0 * math.cos(a), cy - r0 * math.sin(a)),
                       QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)))

    def _paint_moon(self, p, cx, cy, col):
        """月亮：一个大圆减去一个偏移的圆 —— 相减出来的月牙边缘是干净的。

        缺口往**右上**偏(相对圆心)，留下 3/4 的月牙在左下 —— 和太阳的圆盘共用同一个
        视觉重心，翻转过程中图标不会"跳"到别处。
        """
        s = self.SIZE
        r = s * 0.34
        full = QPainterPath()
        full.addEllipse(QPointF(cx, cy), r, r)
        bite = QPainterPath()
        # 缺口半径 0.84r、偏移 (0.56r, -0.49r)：月牙最厚处约 0.45r。
        # 再薄就只剩一道线了 —— 30px 的图标上细月牙读不出来（试过 0.92r/0.52r）。
        bite.addEllipse(QPointF(cx + r * 0.56, cy - r * 0.49), r * 0.84, r * 0.84)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPath(full.subtracted(bite))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, C_HOVER_A))
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        col = QColor(C_ACCENT_D if self._hover else C_SUB)
        cx, cy = r.center().x(), r.center().y()
        p.save()
        p.translate(cx, cy)
        p.scale(max(0.001, abs(math.cos(math.pi * self._t))), 1.0)
        p.rotate(-180.0 * self._t)
        p.translate(-cx, -cy)
        (self._paint_sun if self._t < 0.5 else self._paint_moon)(p, cx, cy, col)
        p.restore()
        p.end()


class RevealOverlay(QWidget):
    """主题切换的圆形揭示层：整个面板盖着**旧主题的整帧**，只有一个圆是"洞"。

    洞里露出来的是**活的、已经换成新主题**的面板 —— 所以扫到哪儿，哪儿就是真的
    新主题（文字、卡片、背景一起换），而不是"先滑一层色块、内容晚一拍才出来"。
    收尾时圆已覆盖全屏，遮罩一撤和新主题严丝合缝，**没有跳变**。

    为什么走"整帧快照"这条路：实测 `Panel.grab()`（含全部子控件）只要 4ms。
    让每个控件各按进度插值反而要改遍所有 paintEvent，还一定会漏掉某个。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 不抢鼠标：遮罩只在动画期间存在，期间用户点到的是底下那些真控件
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pm = None
        self._c = QPointF(0.0, 0.0)
        self._r = 0.0
        self.hide()

    def setup(self, pm, center, radius):
        self._pm = pm
        self._c = center
        self._rmax = float(radius)
        self._r = 0.0

    def set_radius(self, r):
        self._r = float(r)
        self.update()

    def paintEvent(self, e):
        if self._pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 只把圆**之外**糊住：圆内不动，露底下的新主题。
        # 用 setClipPath（而不是 QRegion 相减）：QRegion 是 1px 网格，圆的边缘会成锯齿。
        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        hole.addEllipse(self._c, self._r, self._r)
        p.setClipPath(outer.subtracted(hole))
        p.drawPixmap(0, 0, self._pm)
        p.end()


def round_path(rect, rt, rb):
    """四角**分别**取半径的圆角矩形：上两角 `rt`、下两角 `rb`（0 就是直角）。

    Qt 只给「四角同半径」的 `QPainterPath.addRoundedRect`，而状态页需要「上圆下平」：
    上沿在面板内部、收圆才好看；下沿与面板下沿**重合**，收圆就会在面板底角露出缝
    （见 StatusSheet.RADIUS_B 的说明）。角度按 Qt 约定：0° 在 3 点钟、逆时针为正。
    """
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    rt = max(0.0, min(float(rt), w / 2.0, h / 2.0))
    rb = max(0.0, min(float(rb), w / 2.0, h / 2.0))
    path = QPainterPath()
    path.moveTo(x + rt, y)                      # 左上角起点
    path.lineTo(x + w - rt, y)
    if rt > 0:
        path.arcTo(x + w - 2 * rt, y, 2 * rt, 2 * rt, 90.0, -90.0)      # 右上
    path.lineTo(x + w, y + h - rb)
    if rb > 0:
        path.arcTo(x + w - 2 * rb, y + h - 2 * rb, 2 * rb, 2 * rb, 0.0, -90.0)   # 右下
    path.lineTo(x + rb, y + h)
    if rb > 0:
        path.arcTo(x, y + h - 2 * rb, 2 * rb, 2 * rb, 270.0, -90.0)     # 左下
    path.lineTo(x, y + rt)
    if rt > 0:
        path.arcTo(x, y, 2 * rt, 2 * rt, 180.0, -90.0)                  # 左上
    path.closeSubpath()
    return path


class StatusSheet(GlassBase):
    """运行状态页：从**左往右**滑入，铺满标题栏以下的全部区域。

    为什么留标题栏：关闭方式就是「再点一次标题栏上的仪表盘按钮」，
    这一页自己没有关闭按钮 —— 按钮要是被盖住就没法关了。

    底色不是普通毛玻璃，而是**胶囊液面那套色的加深版**（左 C_SHEET_L → 右 C_SHEET_R）：
    用户要求「弹出的背景就是填充胶囊的液体的颜色」，看过之后又要求「再深一点」，
    于是把明度压下去、饱和度反而略降（更深但不更艳）。停靠位置取 0.0 / 1.0 均匀过渡
    —— 胶囊那条 0/0.58/1 的三档是为「液面前沿推进」调的，铺成一整页会显得
    左边变色太快、右边一大片死色。
    """

    def _radius(self):
        """页面**上**两角的圆角（下两角是直角，见 RADIUS_B —— 那不算第三个"级"，
        它是同一个 R_SURFACE 级因"下沿与面板下沿重合"而做的单边特例）。"""
        return self.RADIUS

    RADIUS = R_SURFACE       # **上**两角圆角（页面在面板内部，上沿收圆才不突兀）
    # **下**两角直角。曾经四角都是 18 —— 那时下角会和面板底角对不上：面板底角
    # 只有 ~4px 圆角（Windows 自己的窗口圆角），比页面小得多，于是左下 / 右下各露出
    # 一条浅色月牙。用户的原话是「蓝色背景完全包裹下方不要有空隙」，实测 150% 缩放下
    # 那道缝在 x=42 处高 9px（页面圆角 27 物理px，圆心 (62,465)，42 处正好差 9）。
    RADIUS_B = 0.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # 内容（状态卡）是手动 move 进来的，这里不建 layout —— 免得跟 move() 打架

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = self.RADIUS
        path = round_path(rect, radius, self.RADIUS_B)
        p.save()
        p.setClipPath(path)
        g = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        g.setColorAt(0.0, QColor(C_SHEET_L))
        g.setColorAt(1.0, QColor(C_SHEET_R))
        p.fillRect(rect, QBrush(g))
        p.restore()
        self._paint_edge(p, rect, radius, self.RADIUS_B)
        p.end()


# ============================ 控制台：荧光特效三态 ============================

class GlowSegmented(QWidget):
    """三态分段控件（彩色 / 单色 / 关闭）。

    自绘而不用三个 `QPushButton`：要的是"一块滑块滑过去"的观感，QSS 做不出来；
    而且三个独立按钮的圆角会跟卡片那套统一圆角语言打架。

    对外接口刻意对齐 `QPushButton` 的习惯（`currentMode()` / `setMode()` / `changed`），
    自检里就能像驱动普通按钮一样驱动它。
    """

    changed = pyqtSignal(str)
    H = 30                   # 固定高：轨道和指示器都按它算半径（H/2 = 全圆角）
    PAD = 3                  # 指示器与轨道内缘的间距
    SEG_MS = 300             # 滑块过渡（<= 400ms 硬约束）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # 自绘控件拿不到 QSS 的 font-size（QSS 只作用于 QWidget 的 font 属性，
        # 而这里是 QPainter 自己画的文字），所以字号显式设死，别指望继承。
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)
        self._labels = [GLOW_MODE_LABELS[m] for m in GLOW_MODES]
        # ⚠️ 两个下标必须分开：`_target_i` 是**逻辑**选择（谁被选中），`_i` 是**视觉**位置
        #    （指示器此刻画在哪）。合成一个的话，"面板反过来同步这个控件"就会变成
        #    递归 —— 动画刚起步时 `round(_i)` 还停在旧段，`setMode()` 会以为没生效、
        #    再发一次 `changed`，于是无限自我触发。这和让位那段 `_h_need` vs `height()`
        #    是同一个坑：**拿动画中间值当逻辑状态**。
        self._target_i = 0
        self._i = 0.0
        self._hover_i = -1       # 鼠标悬停在哪一段（-1 = 没有）
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.SEG_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._anim.finished.connect(self._on_anim_done)
        self.setMode(GLOW_MODE, animate=False, emit=False)
        self.setFixedWidth(self._seg_w() * len(self._labels) + self.PAD * 2)

    # --- 尺寸 ---
    def seg_width(self):
        fm = QFontMetrics(self.font())
        return max(52, max(fm.horizontalAdvance(s) for s in self._labels) + 24)

    # 旧名（内部用）
    def _seg_w(self):
        return self.seg_width()

    # --- 状态 ---
    def currentMode(self):
        """**逻辑**选择（不是动画中途的位置）。"""
        return GLOW_MODES[self._target_i]

    def currentIndex(self):
        return self._target_i

    def setMode(self, mode, animate=True, emit=True):
        """切到 `mode`（"color" / "mono" / "off"）。返回是否真的变了。

        可以放心被"选择变更的接收方"反过来调用（见 `Panel.set_glow_mode`）：
        逻辑下标先落地，所以第二次进来 `changed` 必然是 False，不会自激。
        """
        if mode not in GLOW_MODES:
            return False
        idx = GLOW_MODES.index(mode)
        changed = idx != self._target_i
        self._target_i = idx
        self._anim.stop()
        if not animate:
            self.setFloatIndex(float(idx))
        else:
            self._anim.setStartValue(self._i)
            self._anim.setEndValue(float(idx))
            self._anim.start()
        self.update()
        if changed and emit:
            self.changed.emit(mode)
        return changed

    def setFloatIndex(self, v):
        self._i = float(v)
        self.update()

    def _on_anim(self, v):
        self.setFloatIndex(float(v))

    def _on_anim_done(self):
        # 收尾对齐到逻辑下标：缓动末值理论上就是它，但别依赖浮点刚好落在整数上
        self.setFloatIndex(float(self._target_i))

    # --- 事件 ---
    def _index_at(self, pos):
        w = self.seg_width()
        i = int((pos.x() - self.PAD) // w)
        return max(0, min(len(self._labels) - 1, i))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setMode(GLOW_MODES[self._index_at(e.position().toPoint())])
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().toPoint())
        if i != self._hover_i:
            self._hover_i = i
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover_i = -1
        self.update()
        super().leaveEvent(e)

    # --- 绘制 ---
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        rad = self.H / 2.0
        w = float(self.seg_width())
        p.setPen(Qt.PenStyle.NoPen)

        # 1) 轨道：极浅的一层底 + 发丝边（和卡片的描边语言一致）
        tr = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(tr, rad, rad)
        p.setBrush(QColor(C_SEG_TRACK_RGB[0], C_SEG_TRACK_RGB[1], C_SEG_TRACK_RGB[2],
                          C_SEG_TRACK_A))
        p.drawPath(path)
        hr, hg, hb = C_HAIR_RGB
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(hr, hg, hb, HAIR_A), 1))
        p.drawPath(path)

        # 2) 指示器：当前段的实心强调色药丸。`_i` 是浮点 → 滑动过程自然连续。
        x = self.PAD + self._i * w
        pill = QRectF(x + 0.5, self.PAD + 0.5, w - 1.0, self.H - self.PAD * 2 - 1.0)
        pr = pill.height() / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C_ACCENT))
        pp = QPainterPath()
        pp.addRoundedRect(pill, pr, pr)
        p.drawPath(pp)

        # 3) 文字：选中段用反白，其余用次要色；悬停段往正文色靠半档。
        #    反白跟的是**逻辑**选择（`_target_i`）：指示器滑过去要 220ms，
        #    若按 `round(_i)` 反白，三格里从彩色跳到关闭会「途经单色亮一下」。
        p.setFont(self.font())
        act = self._target_i
        for i, s in enumerate(self._labels):
            cell = QRectF(self.PAD + i * w, 0, w, self.H)
            if i == act:
                col = QColor(C_ON_ACCENT)
            elif i == self._hover_i:
                col = QColor(C_TEXT)
            else:
                col = QColor(C_SUB)
            p.setPen(QPen(col))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, s)
        p.end()


class GlassPanel(GlassBase):
    """运行状态毛玻璃卡片：悬停时整卡上浮（阴影放大下移），卡内不重排。

    现在它住在独立的运行状态页（`StatusSheet`）里，高度由 `Panel._place_sheet_cards()`
    按内容算 —— 以前是留在滚动区里"吃剩余高度"的那一张，那个特例已经删掉。
    """

    MIN_H = 120             # 高度下限（见 _place_sheet_cards），防止内容异常少时卡片塌掉

    def _radius(self):
        """内层卡：住在状态页**里面**，取 R_INNER(14)，描边也走内层那一档。"""
        return R_INNER

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 15, 20, 15)
        lay.setSpacing(11)
        self._lay = lay          # 备查（状态卡现已撑满整页，高度不再按 sizeHint 算）

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_head = QLabel("运行状态")
        self.lb_head.setObjectName("cardTitle")
        head.addWidget(self.lb_head, 1)
        self.lb_hint = QLabel("")
        self.lb_hint.setObjectName("rowDesc")
        head.addWidget(self.lb_hint, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        self.val = {}
        for key, label in (("pid", "游戏进程"), ("hp", "血      量"),
                           ("en", "精      力"), ("blk", "拦截统计"),
                           ("hook", "挂钩状态")):
            r = QHBoxLayout()
            r.setContentsMargins(0, 0, 0, 0)
            k = QLabel(label)
            k.setObjectName("statKey")
            v = QLabel("—")
            v.setObjectName("statVal")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(k)
            r.addStretch(1)
            r.addWidget(v)
            lay.addLayout(r)
            self.val[key] = v
        # 尾部留一个 stretch：卡片高度是按内容定死的（见 Panel._place_sheet_cards），
        # 正常情况下用不上；但万一哪天高度被外部改大，没有它的话 QVBoxLayout 会把
        # 多余高度平均摊到每一行之间 —— 5 行被拉得老远、变成"仪表盘式分散"。
        # 留着当保险，代价是零。
        lay.addStretch(1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = R_INNER
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        self._paint_glass(p, path, rect, radius, veil_base=VEIL_CARD,
                          solid=SHEET_CARD_FILL)
        self._paint_glow(p, path, rect, radius)
        self._paint_edge(p, rect, radius, inner=True)
        p.end()


# ============================ 控制台卡片 ============================

class ConsoleCard(GlassBase):
    """控制台：目前只有一项 —— 卡片 / 胶囊的荧光特效模式（彩色 · 单色 · 关闭）。

    住在运行状态页里，排在状态卡**下方**（版式见 `Panel._place_sheet_cards`）。
    和状态卡同族：8px 窄边、高度按内容定死、**不随窗口拉伸**，毛玻璃走同一个特例
    （暗色铺 SHEET_CARD_FILL 实色，否则会比它所在的那页还暗）。
    """

    MIN_H = 60              # 高度下限（见 _place_sheet_cards）

    MODE_HINTS = {
        "color": "彩虹极光绕边",
        "mono": "当前主题强调色",
        "off": "完全不发光",
    }

    def _radius(self):
        """内层卡：住在状态页**里面**，取 R_INNER(14)，描边也走内层那一档。"""
        return R_INNER

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 15, 20, 15)
        lay.setSpacing(11)
        self._lay = lay

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_head = QLabel("控制台")
        self.lb_head.setObjectName("cardTitle")
        head.addWidget(self.lb_head, 1)
        self.lb_hint = QLabel("")
        self.lb_hint.setObjectName("rowDesc")
        head.addWidget(self.lb_hint, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        k = QLabel("荧光效果")
        k.setObjectName("statKey")
        self.seg = GlowSegmented()
        row.addWidget(k, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.seg, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row)

        # 尾部 stretch：同状态卡 —— 高度是定死的，正常用不上；万一被外部改大，
        # 没有它 QVBoxLayout 会把多余高度摊到每一行之间，两行被拉成上下分离。
        lay.addStretch(1)
        self.modeChanged = self.seg.changed     # 语义别名：面板接这个
        self.sync_hint(self.seg.currentMode())

    def sync_hint(self, mode):
        self.lb_hint.setText(self.MODE_HINTS.get(mode, ""))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = R_INNER
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        self._paint_glass(p, path, rect, radius, veil_base=VEIL_CARD,
                          solid=SHEET_CARD_FILL)
        self._paint_glow(p, path, rect, radius)
        self._paint_edge(p, rect, radius, inner=True)
        p.end()


class ForgetCard(GlassBase):
    """遗忘手牌卡：点按钮 → 卡内展开选卡区 → **滚轮**上下选 → 点「遗忘」才真删。

    数据来自 agent.js 的 `forget_list()` / `forget()`。已实机标定（见 memory 2026-09-24）：
    三堆（抽牌堆 / 弃牌堆 / 已消耗）逐张列出，重复牌各是独立对象，删哪张删哪张；
    删除走 List 直改，删掉的牌**真的会从牌库消失**。

    ---- 交互版式（用户逐条确认过）----
    - **面板内展开**：不弹新窗、面板不跳大小。同一张卡在两种形态间切换，高度自适应。
    - **滚轮选卡**：滚轮上下翻，高亮当前项，旁边实时显示「序号 / 共 N 张」。
      滚轮只改选中项，**不动面板**（`wheelEvent` 里 accept 掉，不让它冒泡给滚动区）。
    - **二次确认**：滚轮选完还要再点一次「遗忘」才真删 —— 防滚过头误删。
      选中项一变就把按钮从「遗忘」复位成待确认态。
    - **列表即时刷新 + 计数**：删掉的牌立刻从列表消失，角落显示「本次已遗忘 N 张」。
    - **按堆分组**：先抽牌堆、再弃牌堆、再已消耗；堆内保持原顺序。
    - 自动跟随场景：营地读探索牌库、战斗读战斗牌库（agent 侧 `pickDeck` 决定）。

    为什么不用 QListWidget：整块面板都是自绘毛玻璃，系统控件的滚动条和选中底色
    跟这套视觉完全不搭。自绘 N 行反而更省事，也才能做到「高亮靠字号+颜色分层」。
    """

    MAX_ROWS = 5          # 一屏最多列几行，超出的靠滚轮翻（再多卡片会高得离谱）
                          # 用户 2026-10-01 从 7 改成 5（卡片更矮）。`ui_check` 的
                          # "展开态显示 N 行"那条是**读这个常量**的，改了不用动断言。
    # ⚠️ 版面刻度**必须和 DeckCard 对齐**（用户要求「与上方牌库顺序大小一致」）。
    # 上方的牌库卡一行是 12px 文字 + 靠 layout spacing(7) 撑出的行距，行与行之间
    # 有明确呼吸；这里原来每行只有 12px、行距 0，七行糊成一坨黑块，两张卡紧挨着
    # 摆在一起就非常明显。现在每行给 ROW_H = 12(文字) + 8(行距) = 20px 的槽位，
    # 行内文字**垂直居中**，整列读起来与上方横排是同一套节奏。
    ROW_H = 20            # 单行槽位高度（12px 文字 + 8px 行距）
    # ⚠️ 高度下限**必须和 DeckCard.MIN_H 相等**，两张卡收起态才会一样高（用户要求）。
    # 收起态内容 = 边距 15/13 + 标题 16 + spacing 7 + 「说明 + 按钮」行 30 = 81。
    # 而 DeckCard 收起态是 15+16+7+16+13 = 67，**被它自己的 MIN_H(70) 抬到 70**
    # （即牌库卡收起态根本不吃满内容，底部多出 3px 空档）。
    # 要让两张卡收起态都是 70，遗忘卡必须**吞掉 11px**（81-70），不是 7 ——
    # 不能按"按钮比文字高的 7px"去想，得按"和 DeckCard 的实际高度对齐"去算。
    # ⚠️ 但按钮高度**不能为了凑这 11px 去缩**：80px 宽的按钮装 4 个 12px 汉字是 48px
    # 文字宽，只剩 32px 内边距（QSS 还写着 `padding: 0 16px` = 32px），缩到 19px 高
    # 就会挤成"文字顶到上下边"的丑样子。所以这 11px 由**卡片自己吞掉**：
    # 卡片把按钮那一层在纵向"溢出"它的高度，但**不动画出来的底**。
    MIN_H = 70            # 收起态高度 == DeckCard.MIN_H
    # 收起态要吞掉的高度：内容自然高 81 - 目标高 70。
    # 展开态**不吞**（见 `sync_height`）—— 那时高度由选卡区主导，再减会让 pick 贴到卡边。
    _FOLD_OVERHANG = 11
    # 选卡区高度：MAX_ROWS 行 + 分组标题行 + 底部按钮行
    PICK_H = ROW_H * MAX_ROWS + 26 + 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)   # 整卡不再可点（展开态有按钮）
        self.setFixedHeight(self.MIN_H)
        self._h_need = self.MIN_H      # 逻辑高度（不受让位动画的插值影响，见 sync_height）

        self._open = False        # 是否处于「选卡」形态
        self._cards = []          # [{idx, stack, stackLabel, index, name, cost, ptr}]，牌名已汉化
        self._sel = 0             # 当前高亮项在 _cards 里的下标
        self._top = 0             # 列表视窗第一行的下标（滚动用）
        self._armed = False       # True = 已点过一次「遗忘」，等第二次确认
        self._forgotten = 0       # 本次展开已遗忘张数
        self._kind = ''           # 「探索」/「战斗」
        self._counts = {}
        self._warn = ''
        self._busy = False        # 遗忘请求在飞行中：禁按钮，防连点
        self._connected = False
        self._awaiting = False    # 已发出列表请求，等数据回来
        on_open = None            # 展开回调：Panel 用它去拉一手列表

        self.on_open = None
        # 高度变化回调：Panel 挂在 `_reflow_soon` 上。见 sync_height 的说明 ——
        # 这张卡一变高就会压住下面的卡，必须有人补排布。
        self.on_height = None
        self.on_forget = None     # (ptr, stack) -> None，Panel 负责丢给 worker

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 15, 20, 13)
        lay.setSpacing(7)
        self._lay = lay

        # ---- 标题行 ----
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_head = QLabel("遗忘手牌")
        self.lb_head.setObjectName("cardTitle")
        head.addWidget(self.lb_head, 1)
        self.lb_count = QLabel("")
        self.lb_count.setObjectName("forgetCount")
        self.lb_count.setVisible(False)
        head.addWidget(self.lb_count, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # ---- 说明行 + 按钮行**合并成同一行** ----
        # ⚠️⚠️ 按钮**不许**独占一行。原来 `lb_info` 一行、`btns` 又一行（36px + 7px
        # spacing），收起态白白比上方牌库卡高出 32px —— 两张卡都是"标题 + 说明"的小卡，
        # 上下挨着摆，高度差一眼就看得出来（用户截图报的就是这个）。
        # 现在按钮挂在说明行**右侧**，与那行灰色小字平行：收起态 70px，和牌库卡**一模一样**。
        # 展开态多出来的高度全部由选卡区（`pick`）贡献 —— 那才是真的"内容变多了"。
        # 按钮对齐用 `AlignBottom` 而不是居中：说明行被按钮撑到 30px 高，
        # 12px 的小字居中会在上下各留 9px，看着像飘在按钮中间；贴底与标题列的
        # 文字基线更齐。
        info_row = QHBoxLayout()
        # ⚠️ 行容器**不设任何最小高度**：按钮是 30px，而这一行只分到 30-11=19px
        # （因为卡片把 `_FOLD_OVERHANG` 吞掉了）。若给行设 min 30，布局会反过来
        # 把卡片顶高，白吞。**下边距留 0**：按钮要往下溢 11px 落到"被吞掉的"
        # 那段底边距里（卡内下边距名义 13，吞掉 11 后实际还剩 2px 给按钮呼吸）。
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(8)
        self.lb_info = QLabel("")
        self.lb_info.setObjectName("rowDesc")
        # 说明文字很长（"点右侧按钮，用滚轮选牌后遗忘（立即生效，不可撤销）"），
        # 必须让它自己截断。不设 minimumWidth(0) 的话 QLabel 的 sizeHint 会把
        # 整行顶宽，按钮被挤出可视区。
        self.lb_info.setMinimumWidth(0)
        info_row.addWidget(self.lb_info, 1)

        self.btns = QWidget()
        self.btns.setVisible(True)
        self.btns.setFixedHeight(30)      # 恒 30，与三个按钮同高
        # ⚠️⚠️ 必须同时**禁掉"随父级缩高"**。Qt 里父控件变矮时会向下压子控件，
        # `setFixedHeight` 也挡不住（实测：行被压到 19 时按钮只剩 19，
        # QSS 的 `border-radius: 15px` 又把文字上下各切一半，"遗忘手牌"被削成两截）。
        # 关掉这个属性 + `setFixedHeight` 之后，父级再矮也不动它 —— 按钮按 30px
        # 老实画在行里，超出的部分由**卡片吞掉**（见 `sync_height` 的 `_FOLD_OVERHANG`）。
        self.btns.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Fixed)
        bl = QHBoxLayout(self.btns)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.lb_hint2 = QLabel("")
        self.lb_hint2.setObjectName("forgetArm")
        bl.addWidget(self.lb_hint2, 1)
        self.b_cancel = QPushButton("取消")
        self.b_cancel.setObjectName("fgCancel")
        self.b_cancel.setFixedHeight(30)
        self.b_cancel.setMinimumWidth(96)
        self.b_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bl.addWidget(self.b_cancel, 0)
        self.b_go = QPushButton("遗忘")
        self.b_go.setObjectName("fgGo")
        self.b_go.setFixedHeight(30)
        self.b_go.setMinimumWidth(96)
        self.b_go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_go.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bl.addWidget(self.b_go, 0)
        # 收起态的入口按钮（和上面两个互斥显示），靠右与计数标签同一列。
        # 三个按钮**统一 96px 起**：宽度只在展开 <-> 收起之间切换，不该跟着跳一下。
        self.b_open = QPushButton("遗忘手牌")
        self.b_open.setObjectName("fgOpen")
        self.b_open.setFixedHeight(30)
        self.b_open.setMinimumWidth(96)
        self.b_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bl.addWidget(self.b_open, 0)
        info_row.addWidget(self.btns, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(info_row)

        # ---- 选卡区（默认隐藏） ----
        self.pick = QWidget()
        self.pick.setVisible(False)
        pl = QVBoxLayout(self.pick)
        pl.setContentsMargins(0, 2, 0, 0)
        pl.setSpacing(0)
        self._pl = pl

        self.rows = []
        for _ in range(self.MAX_ROWS):
            row = self._make_row()
            row.setVisible(False)
            pl.addWidget(row)
            self.rows.append(row)

        self.lb_note = QLabel("")
        self.lb_note.setObjectName("forgetNote")
        self.lb_note.setVisible(False)
        pl.addWidget(self.lb_note)
        lay.addWidget(self.pick)

        self.lb_warn = QLabel("")
        self.lb_warn.setObjectName("forgetWarn")
        self.lb_warn.setVisible(False)
        lay.addWidget(self.lb_warn)

        lay.addStretch(1)

        self.b_open.clicked.connect(self.open_pick)
        self.b_cancel.clicked.connect(self.close_pick)
        self.b_go.clicked.connect(self._on_go)
        self._render()

    # ---------- 一行：序号 / 堆名 / 牌名 / [费用] ----------
    @classmethod
    def _make_row(cls):
        """一行选卡槽。

        ⚠️ 行高**锁死 `ROW_H`**，四个标签全部 `AlignVCenter`：这样每行占一个等高
        槽位、文字在槽内居中，整列的行距与上方牌库卡的 layout spacing 是同一种
        「一行一块」的节奏。以前是自适应高度（=12px 文字高）且行距 0，七行贴成
        一整块，两张卡上下挨着看差别很明显（用户报的正是这个）。
        """
        w = QWidget()
        w.setFixedHeight(cls.ROW_H)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(7)
        lb_i = QLabel("")
        lb_i.setObjectName("forgetIdle")
        lb_i.setFixedWidth(20)                 # 固定宽，牌名才能左对齐成一列
        lb_i.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lb_st = QLabel("")
        lb_st.setObjectName("forgetIdle")
        lb_st.setFixedWidth(48)                # 「抽牌堆 / 弃牌堆 / 已消耗」三字对齐
        lb_st.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lb_nm = QLabel("")
        lb_nm.setObjectName("forgetName")
        lb_nm.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        # 牌名给足宽度：这一行最该抢眼的是牌名，费用只是附注。
        # 不设最小宽的话，牌名会被挤到左边一小撮、右边空一大片（版式会显得很散）。
        lb_nm.setMinimumWidth(150)
        lb_ct = QLabel("")
        lb_ct.setObjectName("forgetMeta")
        lb_ct.setFixedWidth(30)
        lb_ct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(lb_i)
        h.addWidget(lb_st)
        h.addWidget(lb_nm, 1)
        h.addWidget(lb_ct, 0)
        w.lb_i, w.lb_st, w.lb_nm, w.lb_ct = lb_i, lb_st, lb_nm, lb_ct
        return w

    def sync_height(self, notify=True):
        """按当前形态算高度。收起 / 展开两种形态高度不同，都要按内容自适应。

        `notify` 与返回值的语义同 `DeckCard.sync_height`：高度真变了就回调
        `on_height`，让 Panel 补一次让位；`notify=False` 给 `Panel._relayout` 用。
        ⚠️ 「真变了」比的是**逻辑高度 `_h_need`**，不是 `self.height()` —— 让位动画
        途中 `self.height()` 是插值中间值，拿它比会漏掉「变回原高度」那次（见 DeckCard
        那份说明里的实测）。

        ⚠️⚠️ **收起态要吞掉 `_FOLD_OVERHANG` 那 11px**：内容自然需要 81，
        而 DeckCard 收起态是 70（被它自己的 MIN_H 抬的，内容其实只要 67）。
        减去 11px 后两张卡收起态**都是 70**，用户要的"大小一致"落地。
        被吞掉的是**下方边距**（13 -> 2），按钮下沿离卡底仍有 2px，不会被裁
        （按钮自己 `setFixedHeight(30)` + 禁掉随父缩高，见 `btns` 的构造）。
        而画出来的玻璃面**就是整个 widget 矩形**，高度即卡面高度，所以
        "卡变矮"和"按钮没被裁"这两件事同时成立。
        展开态不减：那时按钮下方压着整块选卡区，减 11 会让最下面那行贴到卡边。
        """
        need = max(self.MIN_H, self._lay.sizeHint().height())
        # 只在**收起态**吞：展开态的内容高度本来就由选卡区主导，再减会让 pick 底部
        # 被压掉 11px，最下面那行贴到卡边。
        if not self._open:
            need = max(self.MIN_H, need - self._FOLD_OVERHANG)
        if notify and need == self._h_need:
            return False
        self._h_need = need
        if notify and self.on_height is not None:
            # 同 `DeckCard.sync_height`：高度**不在这里落地**，交给让位动画统一落地，
            # 否则会出现一帧「卡已经变高、下面的卡还没让开」的真重叠帧。
            self.on_height()
            return True
        self.setFixedHeight(need)
        return True

    # ---------- 展开 / 收起 ----------
    def isOpen(self):
        return self._open

    def open_pick(self):
        if self._open:
            return
        self._open = True
        self._sel = 0
        self._top = 0
        self._armed = False
        self._forgotten = 0
        self._awaiting = True
        self._cards = []
        # 展开 = 一次「开」：整卡从左往右充满液体，和上方胶囊完全同源
        # （同一个 `_liq_p`、同一套 `_paint_liquid`，不是另画一套像的）。
        self._liq_to(True)
        self._render()
        if self.on_open:
            self.on_open()

    def close_pick(self):
        if not self._open:
            return
        self._open = False
        self._awaiting = False
        self._cards = []
        # 收起 = 一次「关」：从右往左抽空
        self._liq_to(False)
        self._render()

    def toggle_pick(self):
        self.close_pick() if self._open else self.open_pick()

    def _on_go(self):
        """第一次点：进入待确认；第二次点：真删。"""
        if not self._cards or self._sel >= len(self._cards):
            return
        if not self._armed:
            self._armed = True
            self._render()
            return
        card = self._cards[self._sel]
        if self._busy:
            return
        self._busy = True
        self._armed = False
        self._render()
        if self.on_forget:
            self.on_forget(card["ptr"], card["stack"])

    # ---------- 数据 ----------
    def update_list(self, info):
        """info = agent.js forget_list() 的快照。

        ⚠️ 断线时**不要清空已有列表**：一次失败的重拉把列表抹掉的话，用户会以为
        牌丢了。只在拿不到牌库（`ok=False` 且没有任何牌）时保留旧数据并给提示。
        """
        info = info or {}
        self._awaiting = False
        self._busy = False
        dt = str(info.get("deckTypeName") or "")
        self._kind = dt if dt and dt != "未知" else ""
        self._counts = info.get("counts") or {}
        self._warn = str(info.get("warning") or "")

        raw = info.get("cards") or []
        if not raw and not info.get("ok") and self._cards:
            # 重拉失败：保留旧列表，只提示原因
            self._render()
            return

        cards = []
        for c in (info.get("cards") or []):
            c = c or {}
            cost = c.get("cost")
            try:
                cost = int(cost)
            except (TypeError, ValueError):
                cost = -1
            cards.append({
                "idx": int(c.get("idx") or 0),
                "stack": str(c.get("stack") or ""),
                "stackLabel": str(c.get("stackLabel") or ""),
                "index": int(c.get("index") or 0),
                "name": card_zh(str(c.get("name") or "")),
                "cost": None if cost < 0 else cost,
                "ptr": str(c.get("ptr") or ""),
            })
        # 尽量把高亮留在同一张牌上（删完一张后光标不要乱跳）
        keep = None
        if self._cards and 0 <= self._sel < len(self._cards):
            keep = self._cards[self._sel]["ptr"]
        self._cards = cards
        if keep:
            for i, c in enumerate(cards):
                if c["ptr"] == keep:
                    self._sel = i
                    break
            else:
                self._sel = min(self._sel, max(0, len(cards) - 1))
        self._sel = max(0, min(self._sel, max(0, len(cards) - 1)))
        self._clamp_top()
        self._render()

    def note_forgotten(self, name, ok, err=''):
        """遗忘结果的即时反馈。

        成功时**本地先摘掉这一张** —— 用户要的是"列表即时刷新"，而重拉列表
        要等一个 worker 往返，中间会有一拍卡顿。本地先摘，随后重拉的结果会
        以权威数据覆盖（`update_list`），两边一致就无感，不一致以真机为准。
        """
        self._busy = False
        if ok:
            if 0 <= self._sel < len(self._cards):
                self._cards.pop(self._sel)
            self._forgotten += 1
            self._clamp_top()
            self._render()
        else:
            self._warn = (err or "遗忘失败")[:40]
            self._render()

    def set_connected(self, ok):
        ok = bool(ok)
        if ok == self._connected:
            return
        self._connected = ok
        if not ok:
            self._cards = []
            self._open = False
            self._awaiting = False
        self._render()

    # ---------- 滚轮：只翻选中项，不动面板 ----------
    def _clamp_top(self):
        n = len(self._cards)
        if n <= self.MAX_ROWS:
            self._top = 0
            return
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + self.MAX_ROWS:
            self._top = self._sel - self.MAX_ROWS + 1
        self._top = max(0, min(self._top, n - self.MAX_ROWS))

    def wheelEvent(self, e):
        """滚轮 = 上下选牌。

        没展开时**必须显式转发给父级**：`QWidget` 默认会吃掉滚轮（不管）也不会
        自动冒泡给 Panel，收起态在卡片上滚动就变成"滚不动"。展开时才 accept，
        把滚轮锁在自己身上，避免选牌的同时整页跟着跑。
        """
        if not self._open or not self._cards:
            e.ignore()
            # Qt 不会自动把滚轮递给父控件（Panel 是卡片父级的父级），显式转发一次，
            # 否则光标压在收起态的遗忘卡上时整页滚不动。
            p = self.parent()
            while p is not None:
                if isinstance(p, Panel):
                    p.wheelEvent(e)
                    break
                p = p.parent()
            return
        d = e.angleDelta().y()
        if d == 0:
            d = e.pixelDelta().y()
        if d == 0:
            e.accept()
            return
        step = -1 if d > 0 else 1
        self._sel = max(0, min(len(self._cards) - 1, self._sel + step))
        self._clamp_top()
        self._armed = False     # 选了别的牌 → 待确认状态复位
        self._render()
        e.accept()

    # ---------- 渲染 ----------
    def _render(self):
        kind = self._kind if (self._connected and self._kind) else ""
        self.lb_head.setText("遗忘手牌 · " + kind if kind else "遗忘手牌")
        self.pick.setVisible(self._open)
        # 两种形态各用各的按钮：收起态只有入口按钮，展开态只有「取消 / 遗忘」。
        # 三者同在一个恒可见的 `btns` 容器里，靠 visible 互斥切换（容器不能藏）。
        self.b_open.setVisible(not self._open)
        self.b_open.setEnabled(self._connected)
        self.b_cancel.setVisible(self._open)
        self.b_go.setVisible(self._open)

        # 计数：本次已遗忘 N 张（只在有数时出现）
        self.lb_count.setVisible(self._forgotten > 0)
        if self._forgotten > 0:
            self.lb_count.setText(f"本次已遗忘 {self._forgotten} 张")

        if not self._open:
            self.lb_info.setText("未连接游戏，无法遗忘" if not self._connected
                                 else "点右侧按钮，滚轮选牌后遗忘")
            for r in self.rows:
                r.setVisible(False)
            self.lb_note.setVisible(False)
            self.lb_warn.setVisible(False)
            self.lb_hint2.setText("")
            self.sync_height()
            return

        self.lb_info.setText("滚轮选牌，选中后点「遗忘」")

        if not self._connected:
            self.lb_info.setText("未连接游戏，无法遗忘")
            for r in self.rows:
                r.setVisible(False)
            self.lb_note.setVisible(False)
            self._set_btns(False)
            self.lb_warn.setVisible(False)
            self.sync_height()
            return

        if not self._cards:
            msg = "正在读取牌库…" if self._awaiting else "没有可遗忘的牌"
            self.lb_info.setText(msg)
            for r in self.rows:
                r.setVisible(False)
            self.lb_note.setVisible(bool(self._warn) and not self._awaiting)
            self.lb_note.setText(self._warn)
            self._set_btns(False)
            self.sync_height()
            return

        # 选卡区
        n = len(self._cards)
        shown = self._cards[self._top:self._top + self.MAX_ROWS]
        for i in range(self.MAX_ROWS):
            r = self.rows[i]
            if i < len(shown):
                self._fill_row(r, self._top + i, shown[i])
                r.setVisible(True)
            else:
                r.setVisible(False)

        # 「还有 N 张」只在真的放不下时出现。
        # 去掉了原来把 `_warn` 也塞进这里的分支 —— 底部 `lb_warn` 已经显示同一句话，
        # 两边同时亮就是同一行错误出现两遍（截图里能明显看出来）。
        rest = n - self.MAX_ROWS
        self.lb_note.setVisible(rest > 0)
        if rest > 0:
            self.lb_note.setText(f"牌组共 {n} 张")

        self._set_btns(True)
        # 失败提示要**能看见**：之前这里无条件隐藏，遗忘失败就完全静默了（踩过）
        self.lb_warn.setVisible(bool(self._warn))
        if self._warn:
            self.lb_warn.setText(self._warn)
        self.sync_height()

    def _set_btns(self, on):
        self.b_go.setEnabled(bool(on) and not self._busy)
        self.b_cancel.setEnabled(not self._busy)
        # armed 走动态属性 + QSS 选择器，避免直接 setStyleSheet 覆盖整套外观
        self.b_go.setProperty("armed", "true" if self._armed else "false")
        self.b_go.style().unpolish(self.b_go)
        self.b_go.style().polish(self.b_go)
        if not on:
            self.b_go.setText("遗忘")
            self.lb_hint2.setText("")
            return
        cur = self._sel + 1
        total = len(self._cards)
        if self._armed:
            self.b_go.setText("确认遗忘")
            self.lb_hint2.setText(f"再点一次真的删掉（{cur}/{total}）")
        else:
            self.b_go.setText("遗忘")
            self.lb_hint2.setText(f"选中 {cur} / {total}")

    def _fill_row(self, r, abs_idx, card):
        on = (abs_idx == self._sel)
        r.lb_i.setText(f"{abs_idx + 1}")
        r.lb_st.setText(card["stackLabel"])
        r.lb_nm.setText(card["name"])
        r.lb_ct.setText(f"[{card['cost']}]" if card["cost"] is not None else "")
        # 高亮 = 字号 + 颜色双重分层（选中行牌名放大半档并加深），不铺背景块。
        # 堆名也跟着加深半档：只亮牌名的话，一行里"半亮不亮"反而看着像渲染错位。
        r.lb_nm.setObjectName("forgetNameOn" if on else "forgetName")
        r.lb_i.setObjectName("forgetMeta" if on else "forgetIdle")
        r.lb_st.setObjectName("forgetMeta" if on else "forgetIdle")
        for w in (r.lb_i, r.lb_st, r.lb_nm):
            w.style().unpolish(w)
            w.style().polish(w)

    def _radius(self):
        """**内层卡**：住在状态页里面，取 R_INNER(14)，描边也走内层那一档。
        （原来它在滚动区、是外层卡；用户 2026-10-06 要求把「遗忘手牌」挪进状态页。）
        """
        return R_INNER

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = R_INNER
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # 展开态稍微提一点白度：选卡是"正在操作"，卡面该比只读卡更实
        # ⚠️ 住在状态页里 ⇒ 要铺 SHEET_CARD_FILL（暗色下是半透明 tint），
        #    否则它不会比它所在的那张液面页更亮、层级就看不出来（见 ConsoleCard）。
        self._paint_glass(p, path, rect, radius,
                          veil_base=(VEIL_CARD_OPEN if self._open else VEIL_CARD),
                          solid=SHEET_CARD_FILL)
        # 液面在玻璃之上、荧光之下（液面不透明，画在荧光后面会把光整片盖掉）
        if self.hasLiquid():
            self._paint_liquid(p, path, rect, radius)
        self._paint_glow(p, path, rect, radius)
        self._paint_edge(p, rect, radius, inner=True)
        p.end()


class DeckCard(GlassBase):
    """牌库顺序卡：按抽牌顺序列出抽牌堆里的牌（序号 + 牌名 + 费用）。**纯只读**，整卡点击开关。

    数据来自 agent.js 的 `deck()` RPC。已实机标定：DrawNextCard 从抽牌堆 List 的
    头部（index 0）按序取牌，所以这里的顺序就是"休息后会依次补进手里的顺序"。

    版式（第四版）：**横着排成一行**，最多 5 张，每张是「序号 牌名 [费用]」一格；
    放不下的张数用「还有 N 张」一行小灰字挂在正下方。

    为什么不用 QLabel 富文本一长串：横排要能**按内容自适应宽度**，而单个 QLabel
    里没法给每张牌单独算宽度 —— 只能每张牌一个 QWidget，名字长了它自己撑宽。
    这样卡片本身也不必锁死高度：横排只占一行，内容多寡由「还有 N 张」决定，
    所以高度用 `sync_height()` 按实际布局算出来，而不是拍一个常数。
    """

    MAX_SHOW = 5     # 最多横着摆 5 张
    # 高度下限，防「收起/空态」被压成一条。实测各内容量的自然 sizeHint：
    #   空(只有标题+说明) 59 / 横排一行 66 / 再多一行「还有 N 张」 85
    # 所以下限取 70 —— 略高于「一行牌」，让横排那行上下有 4px 呼吸；
    # 又不能高过 85，否则「还有 N 张」出现时高度就不动了，自适应当场失效（踩过）。
    MIN_H = 70

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(self.MIN_H)
        self._h_need = self.MIN_H      # 逻辑高度（不受让位动画的插值影响，见 sync_height）
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._on = False
        self._cards = []           # [{name, cost}]，牌名已汉化
        self._discard = 0
        self._exhaust = 0
        self._hand = 0
        self._handSize = 0
        self._warn = ''
        self._kind = ''            # 「探索」/「战斗」——当前读的是哪份牌库
        self._connected = False
        self.on_change = None      # 开关变化回调：Panel 用它决定是否让 worker 去读牌库
        # 高度变化回调：Panel 挂在 `_reflow_soon` 上。这张卡不是最后一张 ——
        # 游戏一跑起来它就会多出「还有 N 张」那一行，长高会把下面的遗忘卡压住。
        self.on_height = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 15, 20, 13)
        lay.setSpacing(7)
        self._lay = lay

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_head = QLabel("牌库顺序")
        self.lb_head.setObjectName("cardTitle")
        head.addWidget(self.lb_head, 1)
        self.lb_state = QLabel("已关闭")
        self.lb_state.setObjectName("rowDesc")
        head.addWidget(self.lb_state, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        self.lb_info = QLabel("")
        self.lb_info.setObjectName("rowDesc")
        lay.addWidget(self.lb_info)

        # ---- 横排：每张牌一个 QWidget ----
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)         # 牌与牌之间的间距（拉大一点，横排才不至于糊成一条）
        self.cells = []
        for _ in range(self.MAX_SHOW):
            chip = self._make_chip()
            chip.setVisible(False)
            row.addWidget(chip)
            self.cells.append(chip)
        row.addStretch(1)          # 不足 5 张时剩下的格子不收拢，左边对齐
        lay.addLayout(row)

        self.lb_more = QLabel("")   # 「还有 N 张」——挂在横排正下方
        self.lb_more.setObjectName("deckMore")
        self.lb_more.setVisible(False)
        lay.addWidget(self.lb_more)

        self.lb_warn = QLabel("")
        self.lb_warn.setObjectName("deckWarn")
        self.lb_warn.setVisible(False)
        lay.addWidget(self.lb_warn)

        lay.addStretch(1)          # 内容不足时，多出来的高度由它吸掉（不居中跳动）
        self._render()

    @staticmethod
    def _make_chip():
        """一格牌：「序号」「牌名」「[费用]」三个 QLabel 横排。

        拆成三个控件而不是一个富文本串，是为了让牌名自己撑宽度、费用紧跟其后；
        换行/截断交给 Qt 的 sizeHint，不做手工测量。
        """
        chip = QWidget()
        h = QHBoxLayout(chip)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        lb_idx = QLabel("")
        lb_idx.setObjectName("deckIdx")
        lb_idx.setAlignment(Qt.AlignmentFlag.AlignBottom)
        lb_name = QLabel("")
        lb_name.setObjectName("deckName")
        lb_name.setAlignment(Qt.AlignmentFlag.AlignBottom)
        lb_cost = QLabel("")
        lb_cost.setObjectName("deckCost")
        lb_cost.setAlignment(Qt.AlignmentFlag.AlignBottom)
        h.addWidget(lb_idx)
        h.addWidget(lb_name)
        h.addWidget(lb_cost)
        chip.lb_idx, chip.lb_name, chip.lb_cost = lb_idx, lb_name, lb_cost
        return chip

    def sync_height(self, notify=True):
        """按实际布局算高度。横排一行 + 可选「还有 N 张」，多寡不同高度不同。

        - 用 `lay.sizeHint()` 而不是 `lay.totalMinimumSize()`：后者会把 addStretch
          算成 0、把隐藏项也算进去，得出的高度忽高忽低；sizeHint 是内容真实需要的。
        - 值没变就 return：`Panel._relayout` 每次滚动/拉伸都会调过来，重复
          `setFixedHeight` 会触发无谓的几何重算，动画途中看得出来在抖。
        - **高度真变了就回调 `on_height`**：这张卡下面还压着别的卡，它一长高就把
          下面的盖住（用户报的「UI 重叠」）。所以"变高"必须让 Panel 知道，由它补一次
          让位。`notify=False` 留给 `Panel._relayout` —— 那边自己正在排布，
          再回调回去就是递归，而且它是**无条件落地**（它要的就是目标几何）。
        - ⚠️ "没变"比的是**逻辑高度 `_h_need`**，不是 `self.height()`。让位动画途中
          卡片的高度是插值出来的中间值，拿它比会把「变回原来的高度」误判成"没变化"：
          实测踩过 —— 110→89 的收缩动画刚起步（首帧正好是 110），内容又变回 110，
          这里早退 → 通知不发 → 那张卡最后**停在 89**。逻辑高度不受动画影响。
        """
        need = max(self.MIN_H, self._lay.sizeHint().height())
        if notify and need == self._h_need:
            return False                    # 逻辑高度没变 → 什么都不做
        self._h_need = need
        if notify and self.on_height is not None:
            # ⚠️⚠️ 这里**绝对不能**自己 `setFixedHeight(need)`。
            # 让位动画要拿"旧高度"当起点（见 `Panel._reflow`），先落地的话，
            # 从这一句到 `_reflow` 跑起来之间会存在一帧「上面那张已经变高、
            # 下面那张还没让开」—— 实测这一帧**会被真的画到屏幕上**：把检查挂在
            # 真实 Paint 事件上，一次变高数到 6 帧重叠（DeckCard 已 110 / ForgetCard
            # 还停在 321）。交给 `Panel._reflow` → `_layout_plan` 统一落地即可
            # （`notify=False` 那条是无条件落地的）。
            self.on_height()
            return True
        self.setFixedHeight(need)
        return True
        return True

    # ---------- 开关 ----------
    def isOn(self):
        return self._on

    def toggle(self):
        self.setOn(not self._on)

    def setOn(self, on):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        # 整卡从左往右充满液体（关闭从右往左抽空）—— 与上方胶囊、遗忘卡同源：
        # 用的是 GlassBase 的 `_liq_p` 与 `_paint_liquid`，不是另画一套有点像的。
        self._liq_to(on)
        self._render()
        if self.on_change:
            self.on_change(on)

    def mouseReleaseEvent(self, e):
        # 拖拽滚动时 release 会被 Panel.eventFilter 吞掉，所以这里只会收到真正的点击
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            return
        if self.rect().contains(e.position().toPoint()):
            self.toggle()
        super().mouseReleaseEvent(e)

    # ---------- 数据 ----------
    @staticmethod
    def _norm_card(raw):
        """把 agent.js 给的一条牌规整成 {name, cost}。兼容纯字符串（旧格式/容错）。

        效果文本（agent 里的 text 字段）**故意不取**：游戏的效果是运行时用模板+数值拼的，
        英文原文既不完整也不适合直接展示，中文又要复刻整套占位符语法，性价比太低。
        """
        if isinstance(raw, str):
            return {"name": card_zh(raw), "cost": None}
        raw = raw or {}
        name = card_zh(str(raw.get("name") or ""))
        cost = raw.get("cost")
        try:
            cost = int(cost)
        except (TypeError, ValueError):
            cost = -1
        if cost < 0:
            cost = None
        return {"name": name, "cost": cost}

    def update_data(self, info):
        """info = agent.js deck() 的快照。未开启时不重绘，省掉无谓排版。"""
        info = info or {}
        # 牌名汉化：agent.js 给的是英文，用抽出来的串表查中文；查不到就显英文
        cards = info.get("cards")
        if not cards:
            # 兼容没有 cards 字段的快照：只有牌名
            cards = info.get("draw") or []
        self._cards = [self._norm_card(c) for c in cards]
        self._discard = int(info.get("discard") or 0)
        self._exhaust = int(info.get("exhaust") or 0)
        self._hand = int(info.get("hand") or 0)
        self._handSize = int(info.get("handSize") or 0)
        self._warn = str(info.get("warning") or "")
        # 牌子库类型：营地(探索) / 战斗 —— 让用户一眼看出读的是哪份牌库
        dt = str(info.get("deckTypeName") or "")
        self._kind = dt if dt and dt != "未知" else ""
        if self._on:
            self._render()

    def set_connected(self, ok):
        if ok == self._connected:
            return
        self._connected = bool(ok)
        # 断线就把旧顺序丢掉：重连时宁可先空一拍，也不拿上一局的牌序骗人
        if not self._connected:
            self._cards = []
        if self._on:
            self._render()

    # ---------- 渲染 ----------
    @staticmethod
    def _fill_chip(chip, idx, card):
        """把一张牌填进一格：序号 / 牌名 / [费用]。"""
        chip.lb_idx.setText(str(idx + 1))
        chip.lb_name.setText(card["name"])
        cost = card["cost"]
        # 读不出费用就不显示假值，宁可这一格空着也别给个误导性的 [0]
        chip.lb_cost.setText(f"[{cost}]" if cost is not None else "")
        chip.lb_cost.setVisible(cost is not None)

    def _render(self):
        # 标题带上是哪份牌库（探索 = 营地，战斗 = 本场战斗），省得用户自己猜
        kind = self._kind if (self._on and self._connected and self._kind) else ""
        self.lb_head.setText("牌库顺序 · " + kind if kind else "牌库顺序")

        def _hide_all():
            for chip in self.cells:
                chip.setVisible(False)
            self.lb_more.setVisible(False)
            self.sync_height()

        if not self._on:
            self.lb_state.setText("已关闭")
            self.lb_info.setText("点击卡片开启，提前看到休息后补进手里的牌")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        self.lb_state.setText("已开启")

        if not self._connected:
            self.lb_info.setText("未连接游戏，连上后自动刷新")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        if not self._cards:
            self.lb_info.setText("未读取到牌库（可能不在战斗中）")
            _hide_all()
            self.lb_warn.setVisible(bool(self._warn))
            self.lb_warn.setText(self._warn)
            return

        info = f"抽牌堆 {len(self._cards)} 张 · 弃牌 {self._discard} · 消耗 {self._exhaust}"
        if self._handSize:
            info += f" · 手牌 {self._hand}/{self._handSize}"
        self.lb_info.setText(info)

        shown = min(len(self._cards), self.MAX_SHOW)
        for i in range(self.MAX_SHOW):
            if i < shown:
                self._fill_chip(self.cells[i], i, self._cards[i])
                self.cells[i].setVisible(True)
            else:
                self.cells[i].setVisible(False)
        # 放不下的挂一行小灰字在横排正下方（含被挤掉的那张，所以 +1）
        rest = len(self._cards) - self.MAX_SHOW
        self.lb_more.setVisible(rest > 0)
        if rest > 0:
            self.lb_more.setText(f"还有 {rest} 张")
        self.sync_height()

        self.lb_warn.setVisible(bool(self._warn))
        self.lb_warn.setText(self._warn)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = R_SURFACE
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        self._paint_glass(p, path, rect, radius, veil_base=VEIL_CARD)
        # 液面夹在玻璃与荧光之间（顺序铁律见 GlassBase）
        if self.hasLiquid():
            self._paint_liquid(p, path, rect, radius)
        self._paint_glow(p, path, rect, radius)
        self._paint_edge(p, rect, radius)
        p.end()


class PlanCard(GlassBase):
    """打法建议卡：把「这一手怎么打最划算」摆在**主页面**（滚动区，牌库顺序下方）。

    数据链路：`agent.js battle(withIntents=True)` 读战况 → `battle_planner
    .plan_from_snapshot()` 搜最优出牌序 → worker 回传 `{"_plan": {...}}` → 这里渲染。

    ⚠️ 它是**滚动区卡片**（外层玻璃面）：圆角走默认 R_SURFACE、整卡点击开关、
    高度按内容自适应并回调 `on_height` 让 Panel 补让位 —— 与牌库卡同一套。
    （2026-10-06 用户要求从状态页搬回主页面、放在「牌库顺序」下方。）
    ⚠️ 版式刻意沿用牌库顺序卡那套「序号 牌名 [费用]」小格（直接复用
    `DeckCard._make_chip`）—— 用户对"一张牌长什么样"的读法该只有一种。
    """

    MAX_SHOW = 5          # 建议里最多横着摆几步
    MIN_H = 70            # 收起态高度（与 DeckCard.MIN_H 对齐，两张卡同高才整齐）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(self.MIN_H)
        self._h_need = self.MIN_H
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._on = False
        self._data = {}
        self._connected = False
        self._kind = ""            # 「探索」/「战斗」
        self.on_change = None      # 开关变化回调：Panel 用它决定要不要让 worker 去推演
        self.on_height = None      # 高度变化回调（同 DeckCard，见其 sync_height 注释）

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 15, 20, 15)
        lay.setSpacing(9)
        self._lay = lay

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_head = QLabel("打法建议")
        self.lb_head.setObjectName("cardTitle")
        head.addWidget(self.lb_head, 1)
        self.lb_hint = QLabel("")
        self.lb_hint.setObjectName("rowDesc")
        head.addWidget(self.lb_hint, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        self.lb_info = QLabel("")
        self.lb_info.setObjectName("rowDesc")
        lay.addWidget(self.lb_info)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.cells = []
        for _ in range(self.MAX_SHOW):
            chip = self._make_step_chip()
            chip.setVisible(False)
            row.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
            self.cells.append(chip)
        row.addStretch(1)
        lay.addLayout(row)

        self.lb_note = QLabel("")      # 敌人状态一行（有状态才显示）
        self.lb_note.setObjectName("deckMore")
        self.lb_note.setVisible(False)
        lay.addWidget(self.lb_note)

        self.lb_warn = QLabel("")
        self.lb_warn.setObjectName("deckWarn")
        self.lb_warn.setVisible(False)
        lay.addWidget(self.lb_warn)

        lay.addStretch(1)
        self._render()

    @staticmethod
    def _make_step_chip():
        """一步的格子：**两行** —— 上面「序号 牌名 [费用]」，下面「→ 目标 6/10」。

        ⚠️ 上半行直接**复用** `DeckCard._make_chip()`（同一套 objectName / 字号 / 对齐），
           只在外面套一层竖排 + 加一行目标标签 —— "一张牌长什么样"仍然只有一种读法。
        ⚠️ 两行是为了让"打谁"和"牌名"对齐着看：竖排后每格约 90px 宽，
           5 步也放得下（横排单行再挂一条尾巴会挤爆）。
        """
        chip = QWidget()
        v = QVBoxLayout(chip)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        top = DeckCard._make_chip()
        chip.lb_idx, chip.lb_name, chip.lb_cost = top.lb_idx, top.lb_name, top.lb_cost
        v.addWidget(top)
        lb_tgt = QLabel("")
        # ⚠️ 亮色正文那一档（用户要求"白色更显眼"），不是小灰字
        lb_tgt.setObjectName("planTgt")
        lb_tgt.setVisible(False)
        chip.lb_tgt = lb_tgt
        v.addWidget(lb_tgt)
        return chip

    @staticmethod
    def _target_text(t):
        """目标 → 「→ ② 6/10」。全体 → 「→ 全体」；没有目标（自身增益）→ 空串。"""
        if not t:
            return ""
        if t.get("aoe"):
            return "→ 全体"
        lab = str(t.get("label") or "").strip()
        hp = t.get("hp")
        mx = t.get("maxHp")
        if hp is None:
            return ("→ " + lab).strip()
        try:
            hp_txt = "%.0f" % float(hp)
        except (TypeError, ValueError):
            return ("→ " + lab).strip()
        try:
            if mx:
                hp_txt += "/%.0f" % float(mx)
        except (TypeError, ValueError):
            pass
        return ("→ %s %s" % (lab, hp_txt)).strip()

    @staticmethod
    def _fill_step_chip(chip, idx, step):
        """填一格：序号/牌名/费用（复用牌库卡的填法）+ 目标血量。"""
        DeckCard._fill_chip(chip, idx, step)
        txt = PlanCard._target_text(step.get("target"))
        chip.lb_tgt.setText(txt)
        chip.lb_tgt.setVisible(bool(txt))

    def sync_height(self, notify=True):
        """按内容算高度。与 `DeckCard.sync_height` 同一套语义（含"比较逻辑高度"那条坑）。"""
        need = max(self.MIN_H, self._lay.sizeHint().height())
        if notify and need == self._h_need:
            return False
        self._h_need = need
        if notify and self.on_height is not None:
            # ⚠️ 这里**绝不能**自己 setFixedHeight：让位动画要拿旧高度当起点，
            #    先落地会露一帧"上面长高了、下面还没让开"（详见 DeckCard.sync_height）。
            self.on_height()
            return True
        self.setFixedHeight(need)
        return True

    # ---------- 开关 ----------
    def isOn(self):
        return self._on

    def toggle(self):
        self.setOn(not self._on)

    def setOn(self, on):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        # 整卡从左往右充满液体（关闭从右往左抽空）—— 与胶囊、牌库卡同源
        self._liq_to(on)
        self._render()
        if self.on_change:
            self.on_change(on)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            return
        if self.rect().contains(e.position().toPoint()):
            self.toggle()
        super().mouseReleaseEvent(e)

    # ---------- 数据 ----------
    def set_connected(self, ok):
        if ok == self._connected:
            return
        self._connected = bool(ok)
        if not self._connected:
            self._data = {}        # 断了就别拿上一局的结论骗人
        if self._on:
            self._render()

    def update_data(self, d):
        """worker 回传的推演结果 → 渲染。`d` 见 `build_plan()` 的返回。"""
        d = d or {}
        self._data = d
        self._kind = str(d.get("kind") or "")
        if self._on:
            self._render()

    # ---------- 渲染 ----------
    def _render(self):
        def _hide_all():
            for chip in self.cells:
                chip.setVisible(False)
            self.lb_note.setVisible(False)
            self.sync_height()

        kind = self._kind if (self._on and self._connected and self._kind) else ""
        self.lb_head.setText("打法建议 · " + kind if kind else "打法建议")

        if not self._on:
            self.lb_hint.setText("已关闭")
            self.lb_info.setText("点击卡片开启，按当前手牌算出最优出牌顺序")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        self.lb_hint.setText("已开启")

        if not self._connected:
            self.lb_info.setText("未连接游戏，连上后自动推演")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        d = self._data
        if not d:
            self.lb_info.setText("等待第一轮推演…")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        if not d.get("ok"):
            self.lb_info.setText(d.get("reason") or "这一手算不出建议")
            _hide_all()
            self.lb_warn.setVisible(False)
            return

        seq = d.get("sequence") or []
        if not seq:
            self.lb_info.setText("这一手没有值得打出的牌（当前精力下无正收益）")
            _hide_all()
            note = d.get("statusLine") or ""
            self.lb_note.setVisible(bool(note))
            self.lb_note.setText(note)
            self.lb_warn.setVisible(bool(d.get("warn")))
            self.lb_warn.setText(d.get("warn") or "")
            self.sync_height()
            return

        if d.get("lethal"):
            info = "预计削减 %.0f 血 · 可斩杀！ · 余力 %d" % (d.get("damage") or 0,
                                                              d.get("leftEnergy") or 0)
        else:
            info = ("预计削减 %.0f 血 · 余力 %d · 剩敌 %d 只 / 共 %.0f 血"
                    % (d.get("damage") or 0, d.get("leftEnergy") or 0,
                       d.get("enemiesLeft") or 0, d.get("enemyHpAfter") or 0))
        if d.get("method") == "greedy":
            info += " · 贪心近似"
        self.lb_info.setText(info)

        shown = min(len(seq), self.MAX_SHOW)
        for i in range(self.MAX_SHOW):
            if i < shown:
                self._fill_step_chip(self.cells[i], i, seq[i])
                self.cells[i].setVisible(True)
            else:
                self.cells[i].setVisible(False)

        note = d.get("statusLine") or ""
        self.lb_note.setVisible(bool(note))
        self.lb_note.setText(note)
        self.lb_warn.setVisible(bool(d.get("warn")))
        self.lb_warn.setText(d.get("warn") or "")
        self.sync_height()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = R_SURFACE
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        self._paint_glass(p, path, rect, radius, veil_base=VEIL_CARD)
        # 液面夹在玻璃与荧光之间（顺序铁律见 GlassBase）
        if self.hasLiquid():
            self._paint_liquid(p, path, rect, radius)
        self._paint_glow(p, path, rect, radius)
        self._paint_edge(p, rect, radius)
        p.end()


class EdgeVeil(QWidget):
    """贴在滚动视口上/下边缘的渐变帘。

    **必须做成独立的叠加控件**：Panel.paintEvent 里画的任何东西都会被 content→卡片
    在正常子控件绘制流程里盖掉（父先画、子后画）。只有做成兄弟控件并 raise_()，
    才能真正盖在卡片之上，把「内容被视口裁断」的那条硬边化掉。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._img = None
        self._key = None

    def set_image(self, img):
        self._img = img
        self.update()

    def paintEvent(self, e):
        if self._img is None:
            return
        p = QPainter(self)
        p.drawImage(0, 0, self._img)
        p.end()


# ============================ Worker ============================

def build_plan(trainer):
    """读战况 → 推演 → 给界面的紧凑结构。**在 worker 线程里跑**（会读内存 + 搜出牌序）。

    返回（喂给 `PlanCard.update_data`）：
      {ok, reason, kind, sequence:[{name,cost}], damage, leftEnergy, lethal,
       enemiesLeft, enemyHpAfter, method, statusLine, warn}

    ⚠️ `battle(True)` 要读每张手牌的意图，比 `deck()` 重一些 —— 所以只在
       **状态页展开时**才跑（见 `Panel.toggle_status_sheet` 里的 `plan_want`）。
    ⚠️ 这里**不碰任何 Qt 控件**：worker 是独立线程，跨线程改控件必崩。
    """
    snap = trainer.battle(True)
    if not snap or not snap.get("ok"):
        return {"ok": False, "reason": "读不到战况（可能不在游戏中）"}
    if not any(e.get("alive", True) and not e.get("retreated")
               for e in (snap.get("enemies") or [])):
        return {"ok": False, "reason": "不在战斗中 —— 推演只在战斗里有意义"}

    # 延迟导入：这两张表在 import 时读 JSON，放在 worker 线程里不挡界面启动
    from battle_planner import (plan_from_snapshot, status_short,
                                status_kind, STATUS_MODEL)

    res, br = plan_from_snapshot(snap)
    out = {
        "ok": bool(res.get("ok")),
        "reason": res.get("reason") or "",
        "kind": snap.get("deckTypeName") or "",
        "sequence": [{"name": s.get("name"), "cost": s.get("cost"),
                      "target": s.get("target")}
                     for s in (res.get("sequence") or [])],
        "damage": res.get("damageDealt") or 0,
        "leftEnergy": res.get("leftEnergy") or 0,
        "lethal": bool(res.get("lethal")),
        "enemiesLeft": res.get("enemiesLeft") or 0,
        "enemyHpAfter": res.get("enemyHpAfter") or 0,
        "method": res.get("method") or "",
        "statusLine": "",
        "warn": "",
    }

    # 敌人状态一行（只列非 flavor 的，免得"掉落/风味"把一行塞满）
    rows, punish = [], 0
    for e in br.get("enemies") or []:
        st = getattr(e, "statuses", None) or {}
        if not st:
            continue
        parts = []
        for nm, n in sorted(st.items()):
            if status_kind(nm) == "flavor":
                continue
            parts.append(status_short(nm, n))
            if status_kind(nm) == "player_punish":
                punish += int(n)
        if parts:
            rows.append("%s %s" % (e.name, " · ".join(parts)))
    out["statusLine"] = "敌人状态：" + "；".join(rows) if rows else ""

    # 警告：只挑"会让结论失真"的两条说（其余 assumptions 太技术，界面不展示）
    warns = []
    for a in res.get("assumptions") or []:
        if "没带效果" in a:
            warns.append("有手牌没带效果，本次推演会低估它们")
        elif "状态没读出来" in a:
            warns.append("有敌人状态没读出来，本次推演可能偏乐观")
    if punish > 0:
        warns.append("敌人在场时每打一张牌自伤 %d 点（咒语虚弱）" % punish)
    out["warn"] = "⚠️ " + "；".join(warns) if warns else ""
    return out


class Worker(QThread):
    status = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.cmds = queue.Queue()
        self.trainer = GameTrainer()
        self.attached = False
        self.alive = True
        # 牌库预览只在用户开启时才去读，避免无谓的内存遍历（GUI 线程写、worker 线程读）
        self.deck_want = False
        # 打法推演只在**状态页展开时**才算：它要读每张手牌的意图，比 deck() 重
        self.plan_want = False

    def post(self, fn):
        self.cmds.put(fn)

    def shutdown(self):
        self.alive = False
        try:
            if self.attached:
                self.trainer.detach()
        except Exception:
            pass

    def run(self):
        while self.alive:
            try:
                while True:
                    fn = self.cmds.get_nowait()
                    try:
                        fn(self)
                    except Exception as ex:
                        self.status.emit({"_error": str(ex)})
            except queue.Empty:
                pass
            pid = None
            try:
                pid = self.trainer.find_pid()
            except Exception:
                pid = None
            st = {}
            if self.attached:
                try:
                    st = self.trainer.status() or {}
                except Exception as ex:
                    self.attached = False
                    try:
                        self.trainer.detach()
                    except Exception:
                        pass
                    st = {"_error": str(ex)}
            st["_pid"] = pid
            st["_attached"] = self.attached
            if self.attached and self.deck_want:
                try:
                    st["_deck"] = self.trainer.deck()
                except Exception as ex:
                    st["_deck"] = {"ok": False, "warning": f"读取失败: {str(ex)[:50]}"}
            if self.attached and self.plan_want:
                try:
                    st["_plan"] = build_plan(self.trainer)
                except Exception:
                    st["_plan"] = {"ok": False, "reason": "推演失败（读取战况出错）"}
            self.status.emit(st)
            # 打法建议开着时刷快一点（用户实测报「出牌后血量没法实时刷新」）；
            # 只开牌库/什么都不开时沿用 450ms，省点无谓的内存遍历。
            self.msleep(250 if self.plan_want else 450)


# ============================ 主面板 ============================

class Panel(QWidget):
    # 遗忘卡的数据回传：worker 线程算完 → 信号 → GUI 线程改控件。
    # 直接用信号而不是 `st["_forget"]` 走 status 循环，是因为遗忘是**一次性交互**，
    # 挂在 450ms 的常驻轮询上会让"点完立刻刷新"慢半拍。
    _sig_forget_list = pyqtSignal(dict)
    _sig_forget_res = pyqtSignal(dict)
    # 打法推演的另一条来回：展开状态页时**立刻**要一版，不等 450ms 轮询
    _sig_plan = pyqtSignal(dict)

    # ---- 面板尺寸与滚动区参数 ----
    PANEL_W = 576            # 宽度锁死
    PANEL_H = 350            # 启动时的默认高度
    PANEL_MIN_H = 350        # 纵向最小高度（可自由往上拉）
    CARD_GAP = 10            # 滚动区内卡片间距
    CONTENT_PAD_B = 16       # 内容底部留白：给最后一张卡的阴影留位置
    SBAR_GUTTER = 12         # 右侧给滚动条留的沟槽，避免压在胶囊上
    SCROLL_STEP = 110        # 一格滚轮的位移（原 46 太迟钝）
    DRAG_SLOP = 6            # 超过这个位移就判定为"拖拽滚动"而非点击
    POP_ENTER_MIN = 24       # 至少露出这么多像素才算进入视口 → 触发从左弹出
    POP_EXIT_SLOP = 2        # 完全离开视口才复位，避免边界抖动反复弹
    POP_STAGGER = 55         # 同一批弹出的卡片之间的错开间隔
    VEIL_H = 18              # 视口上下边缘"融进背景"的渐变帘高度
    FROST_W, FROST_H = 576, 1400   # 背景按固定大画布预渲染：拉伸窗口不必重算

    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Shroom & Gloom by-XIZI")
        # 宽锁死、高可自由拉伸（最低 350）：拉高时视口变大，能一屏看完所有卡片
        self.setFixedWidth(self.PANEL_W)
        self.setMinimumHeight(self.PANEL_MIN_H)
        self.resize(self.PANEL_W, self.PANEL_H)
        self.game_dir = find_game_dir()
        self.attached = False
        self._topmost = False        # 面板是否已置顶（本地界面行为，与游戏无关）
        # 「点面板不抢游戏焦点」当前状态：True=挂着 WS_EX_NOACTIVATE（默认常开）。
        # None = 还没同步过 / 上次同步失败 → 下次一定要真调一次系统 API。
        self._noact = None
        # 「标题栏深浅档」当前状态：True=深色档。None = 还没同步过 / 上次没设上
        # → 下次一定要真调一次 DWM（缓存只为省掉重复下发）。
        self._cap_dark = None
        self.frost = None
        self._shadows = []
        self._shadow_key = None
        self._veils_drawn = []
        # ---- 主题 ----
        # `THEME_NAME` 由 main() 按上次的选择先推好（这里只是跟着走），所以自检里
        # 直接 new 一个 Panel 永远是浅色 —— 不会被机器上存过的偏好污染。
        self._theme = THEME_NAME
        self._frosts = {}            # 主题名 → Frost（一份 ~250ms，两套各留一份）
        self._shadow_cache = {}      # 主题名 → 投影贴图列表
        self._shadow_sig = None      # 投影贴图对应的卡片尺寸（尺寸变了整个作废）
        self._theme_anim = None      # 圆形扩散动画（非 None 表示正在切）
        self._theme_target = self._theme
        # 布局重入合并标记：拖拽窗口时 resizeEvent 会连着来，落在 `_laying` 期间的那些
        # 请求**不能丢**（丢了窗口已经变大、状态页却停在旧几何，卡片下方会露出背景，
        # 看起来像"分层的残影"）。置位后在 `_do_layout` 的循环里补跑一轮。
        self._layout_again = False

        # 滚动状态
        self._scroll = 0.0          # 当前滚动位移（px，向下为正）
        self._press_y = None        # 拖拽起点（全局 y）
        self._dragging = False
        self._laying = False
        self._pop_armed = False     # 入场动画开始前，滚动不触发弹入
        self._sbar_a = 0.0          # 滚动条不透明度
        self._sanim = QVariantAnimation(self)
        self._sanim.setDuration(170)      # 短一点，滚轮才跟手
        self._sanim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sanim.valueChanged.connect(self._on_scroll_anim)
        self._sbar_timer = QTimer(self)
        self._sbar_timer.setSingleShot(True)
        self._sbar_timer.setInterval(620)
        self._sbar_timer.timeout.connect(self._fade_sbar)
        self._sbar_anim = QVariantAnimation(self)
        self._sbar_anim.setDuration(260)
        self._sbar_anim.valueChanged.connect(self._on_sbar_anim)

        # 让位补间（见 `_reflow`）：卡片的**内容**变高/变矮时，它自己是不会去通知排布的
        # （`sync_height` 只 setFixedHeight），下面的卡就会被压住 —— 这是用户报的重叠。
        # 这里把「那一瞬」先冻在旧几何上，再用一条动画把高度和 y 一起推到新几何。
        self._reflow_anim = None        # 正在跑的让位补间（None = 没在跑）
        self._reflow_from = None        # 起点几何 [(card, y, h), ...]
        self._reflow_to = None          # 终点几何
        self._h_settled = {}            # 上一次**落定**的卡片高度（补间的起点，见 _reflow）
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(0)   # 合并同一轮事件循环里的多次请求
        self._reflow_timer.timeout.connect(self._reflow)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(11)

        # ---------- 头部 ----------
        # 只留一行标题：下面的灰色副标题已按要求去掉，头部矮一截、视口也就多出一点高度
        # 头部包成 QWidget（而不是直接 addLayout）：状态页的几何要以「标题栏下沿」为基准，
        # 拿 QWidget 就能直接读 geometry()，不必手算 layout 的 spacing 和字体高度。
        self.head_w = QWidget()
        head = QHBoxLayout(self.head_w)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        # 仪表盘按钮排在标题**左侧**（用户要求），点它开关运行状态页
        self.b_dash = DashButton()
        self.b_dash.clicked.connect(self.toggle_status_sheet)
        head.addWidget(self.b_dash, 0, Qt.AlignmentFlag.AlignVCenter)
        t = QLabel("Shroom & Gloom by-XIZI")
        t.setObjectName("title")
        # ⚠️ 标题**不再吃伸缩**：原来它拿 1 的伸缩量，把「圆点 + 连接状态」一路顶到
        #    最右端。用户要求连接状态显示挪到标题右侧，所以伸缩项挪到状态之后。
        head.addWidget(t, 0)
        self.dot = Dot()
        self.conn = QLabel("未连接")
        self.conn.setObjectName("conn")
        head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.conn, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        # 白天/黑夜开关独占右上角（原来是连接状态待的地方）
        self.b_theme = ThemeButton()
        self.b_theme.clicked.connect(self.toggle_theme)
        head.addWidget(self.b_theme, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.head_w)

        # ---------- 滚动视口（胶囊都放进来；状态卡已独立成一页，见 StatusSheet） ----------
        self.viewport = QWidget(self)
        self.viewport.setObjectName("viewport")
        self.content = QWidget(self.viewport)
        self.content.setObjectName("scroller")
        root.addWidget(self.viewport, 1)

        # 视口上下边缘的渐变帘：做成 viewport 之后的兄弟控件，才能盖在卡片之上
        self.veil_top = EdgeVeil(self)
        self.veil_bot = EdgeVeil(self)
        self.veil_top.hide()
        self.veil_bot.hide()

        self.cap_god = Capsule("无敌免疫伤害", "免疫一切伤害来源，血量不再下降")
        self.cap_free = Capsule("使用卡牌不消耗精力", "打出卡牌零消耗，费用显示为 0")
        # PinWin 那套「把窗口钉在最前」的能力，搬进面板自己身上：
        # 玩游戏时面板不会被游戏窗口或别的窗口盖住，随时能点。
        self.cap_top = Capsule("窗口置顶", "面板浮在游戏之上，不被窗口遮挡")
        self.deck_card = DeckCard()
        self.forget_card = ForgetCard()
        self.status_card = GlassPanel()
        self.val = self.status_card.val
        self.plan_card = PlanCard()
        self.console_card = ConsoleCard()

        # 滚动区手动布局：卡片位置由 _relayout 计算，便于做「从左弹出」动画
        # 「窗口置顶」排第一：它是最好用的开关，放最上面一屏就能点到
        # 牌库卡 / 打法卡挨着放 —— 都是"看牌"，用户扫一眼就知道谁是谁（2026-10-06 用户指定：
        # 打法建议放「牌库顺序」**下方**）。「遗忘手牌」搬进状态页了，不在这一列。
        # ⚠️ 状态卡**不在**这里：它已经挪进独立的状态页（见 StatusSheet）。
        #    因此滚动区不再有"吃剩余高度"的卡，`_relayout` 也不再有 last 特例。
        self.scroll_items = [self.cap_top, self.cap_god, self.cap_free,
                             self.deck_card, self.plan_card]
        for c in self.scroll_items:
            c.setParent(self.content)
            c._ly = 0            # 在 content 内的逻辑 y
            c._popped = False
            c._pop_anims = None
            c.installEventFilter(self)
            for gk in c.findChildren(QWidget):
                gk.installEventFilter(self)
        self.viewport.installEventFilter(self)
        # 「按内容变高」的卡要能通知 Panel 补一次让位（见 _reflow）。同一个回调挂给所有
        # 声明了 on_height 的卡，而且**只在 sync_height 里被调用** —— 所以"卡片长高了
        # 却没人补排布"这件事结构上不可能发生：高度既然是 sync_height 算的，
        # 它就一定会走到这里。（回归断言见 ui_check 的「让位」一段。）
        for c in self.scroll_items:
            if hasattr(c, "on_height"):
                c.on_height = self._reflow_soon

        # ---------- 运行状态页（从右向左滑出的一整页） ----------
        self.sheet = StatusSheet(self)
        self.sheet.hide()
        # 页内三张卡（从上到下）：运行状态 / **遗忘手牌** / 控制台。都手动 move，不进 layout。
        # ⚠️ 遗忘卡是**可展开**的（选卡区），高度会变 ⇒ 它的 on_height 要接到
        #    `_sheet_cards_changed`（重排页内卡 + 窗口按需长高），不是滚动区那套让位。
        self.sheet_cards = [self.status_card, self.forget_card, self.console_card]
        self._sheet_card_y = {}                  # 卡 → 落定的 y（展开动画要按它钉住）
        for c in self.sheet_cards:
            c.setParent(self.sheet)
            c.setMinimumHeight(0)                # 不再靠"最小 200"吃窗口高度
        # ⚠️ 遗忘卡是页内唯一**会自己变高**的卡（展开选卡区）。它的高度回调不能挂
        #    `_reflow_soon`（那是滚动区的让位），必须挂页内专用的重排 + 窗口长高。
        self.forget_card.on_height = self._sheet_cards_changed
        # ⚠️ 不用 layout 摆它们，改手动 move（见 _place_sheet_cards）：
        #    widget 一旦进 layout，自己的 move() 会被 layout 覆盖掉，白设。
        self._sheet_open = False
        self._sheet_anim = None
        # 打开状态页时的窗口高度账本（装不下就自动长高，关上还原；见 _fit_sheet_window）
        self._h_restore = None
        self._h_grown = None
        # 控制台当前那一项就是荧光模式；面板负责把选择广播给所有玻璃控件
        self.console_card.seg.changed.connect(self.set_glow_mode)
        # 注意：**不给 sheet 装 eventFilter**。装上就会走 Panel 的"拖拽滚动"分支，
        # 而 sheet 不是滚动内容 —— 在状态页上拖动会把底下的视口滚走。

        # ---------- 主题切换的圆形揭示遮罩 ----------
        # 平时藏起来；点右上角开关时铺上"旧主题整帧"，再用一个从图标中心长大的圆
        # 把底下的新主题抠出来（见 RevealOverlay）。它是 Panel 的子控件，所以盖得住
        # 状态页和边缘帘子，只要在用到时 raise_() 到最上层。
        self.reveal = RevealOverlay(self)

        # ---------- 消息 ----------
        self.msg = QLabel("提示：先点「启动游戏」进入游戏，再点「连接游戏」。")
        self.msg.setObjectName("msg")
        root.addWidget(self.msg)

        # ---------- 按钮 ----------
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.b_launch = QPushButton("启动游戏")
        self.b_attach = QPushButton("连接游戏")
        self.b_detach = QPushButton("断开")
        for b, name in ((self.b_launch, "act"), (self.b_attach, "primary"), (self.b_detach, "act")):
            b.setObjectName(name)
            # 固定高 42：QSS 的 border-radius 是绝对值，高度一定才能保证 21px = 胶囊
            b.setFixedHeight(42)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if name == "act":
                eff = QGraphicsDropShadowEffect(b)
                eff.setBlurRadius(18)
                eff.setOffset(0, 5)
                eff.setColor(QColor(44, 56, 70, 48))
                b.setGraphicsEffect(eff)
            else:
                # 主按钮也要有阴影，否则三个胶囊里就它看着是"贴平的"
                eff = QGraphicsDropShadowEffect(b)
                eff.setBlurRadius(22)
                eff.setOffset(0, 6)
                eff.setColor(QColor(63, 108, 136, 78))
                b.setGraphicsEffect(eff)
            btns.addWidget(b)
        self.b_launch.clicked.connect(self.do_launch)
        self.b_attach.clicked.connect(self.do_attach)
        self.b_detach.clicked.connect(self.do_detach)
        root.addLayout(btns)

        # ---------- 逻辑 ----------
        self.worker = Worker()
        self.worker.status.connect(self.on_status)
        self.worker.start()
        self._sig_forget_list.connect(self.forget_card.update_list)
        self._sig_forget_res.connect(self.on_forget_result)
        self._sig_plan.connect(self.on_plan)

        self.cap_god.toggled.connect(lambda v: self.on_toggle("god", v))
        self.cap_free.toggled.connect(lambda v: self.on_toggle("freeCards", v))
        self.cap_top.toggled.connect(self.on_top_toggle)
        self.deck_card.on_change = self._on_deck_toggle
        self.plan_card.on_change = self._on_plan_toggle
        self.forget_card.on_open = self._on_forget_open
        self.forget_card.on_forget = self._on_forget_do

        def hot(cell):
            return lambda: cell.toggle()

        QShortcut(QKeySequence("F1"), self, activated=hot(self.cap_god))
        QShortcut(QKeySequence("F2"), self, activated=hot(self.cap_free))
        # F3 空着：原来绑「无限精力」，该功能已整体移除（不做重排，免得肌肉记忆错乱）
        QShortcut(QKeySequence("F4"), self, activated=hot(self.cap_top))
        QShortcut(QKeySequence("PageDown"), self, activated=lambda: self._scroll_by(self._view_h() * 0.7))
        QShortcut(QKeySequence("PageUp"), self, activated=lambda: self._scroll_by(-self._view_h() * 0.7))

        # 状态卡悬停微提示
        QTimer.singleShot(0, self._wire_hint)

        if os.environ.get("SHROOM_AUTO_ATTACH"):
            QTimer.singleShot(900, self.do_attach)
        # 真机冒烟用：自动打开置顶，好让外部进程读窗口 exstyle 验证「真的生效了」
        if os.environ.get("SHROOM_AUTO_TOP"):
            QTimer.singleShot(600, self.cap_top.toggle)
        # 另一套皮肤的素材（背景 ~250ms + 阴影贴图）推迟到窗口显示之后再算：
        # 放在 __init__ 里会把启动拖慢 250ms，放在这里用户已经在看面板了。
        QTimer.singleShot(500, self._prebuild_other_theme)

    # ------------- 滚动区：手动布局 + 滚动 + 从左弹出 -------------
    REFLOW_MS = 170          # 「让位」补间（<= 400ms 硬约束）

    def _layout_plan(self):
        """算出滚动的**目标几何**，不动控件：`[(卡, y, 高)] + 内容宽 + 内容高`。

        ⚠️ 顺序有讲究：**先把宽度落地，再问 `sizeHint`**。宽度会影响换行 / 横排重排，
        进而影响内容需要多高；先量后改宽的话，量到的是旧宽度下的高度。
        单行文字为主的卡片上两者恰好相同，但别依赖这个巧合。

        ✅ 修正过一处顺序：以前是 `sync_height()` 在前、`resize(cw, …)` 在后。
        """
        vw = max(120, self.viewport.width())
        vh = self._view_h()
        cw = max(120, vw - self.SBAR_GUTTER)

        plan = []
        h = 0
        for c in self.scroll_items:
            if c.width() != cw:
                c.resize(cw, c.height())
            # 「按内容钳死高度」的卡（min==max）：刷新它自己的高度。
            # 不这么做的话，卡片内容是变了（比如「还有 N 张」出现），但之前设过的
            # setFixedHeight 还锁着老高度，新内容就会被压掉一行看不见。
            # `notify=False` —— 这里正在排布，让它回调回来就是递归（会由 _reflow 统一收口）。
            if c.minimumHeight() == c.maximumHeight() and hasattr(c, "sync_height"):
                c.sync_height(notify=False)
            plan.append((c, h, c.height()))
            h += c.height() + self.CARD_GAP
        h -= self.CARD_GAP                          # 最后一张后面不留间隙
        return plan, cw, max(vh, h + self.CONTENT_PAD_B)

    def _relayout(self):
        """把滚动的目标几何**立刻**落地（不补间）。窗口尺寸变化走这条。

        - 卡片宽度给右侧留出 SBAR_GUTTER，滚动条就不会压在胶囊上；
        - 底部留 CONTENT_PAD_B，最后一张卡的阴影有地方落，不会被视口切出直边；
        - **每张卡都按自身高度排**，窗口拉高时底部自然留白。
          以前这里有个 last 特例：把多出来的高度全塞给状态卡（"一屏看完、无空档"）。
          状态卡挪进独立页面之后就没有可拉伸的卡了，特例连同 MIN_STATUS_H 一起删掉。
        """
        plan, cw, ch = self._layout_plan()
        for c, y, _h in plan:
            c._ly = y
            c.move(c.x(), y)                        # 保留 x：动画中途排布不会把卡拽回来
        self.content.setFixedSize(cw, ch)
        # 记下"已落定"的高度。让位补间要拿**旧**高度当起点，而 `c.height()` 在回调
        # 那一刻已经是新值了（`sync_height` 是先 `setFixedHeight` 再回调的），
        # 所以不能拿它当起点 —— 详见 `_reflow`。
        self._h_settled = {c: c.height() for c in self.scroll_items}

    # ---- 让位：某张卡按内容变高/变矮时，下面的卡跟着挪 ----
    def _reflow_soon(self):
        """卡片报告「我的高度变了」→ 排一次让位。

        合并：同一轮事件循环里连着来几次（比如一张卡变高顺带触发别的重绘）只排一次。

        ⚠️ 正在补间时**必须把手里那条掐掉**（`_abort_reflow`），不能"等它收尾再补一次"。
        原因：那条动画的高度目标是**上一轮**算出来的，它的每一帧都无条件
        `setFixedHeight(插值)` —— 会把卡片刚刚设好的新高度**顶回去**；等它 170ms 收尾
        才补第二轮，中间这段时间用户看到的就是"点了没反应"。实测踩过：遗忘卡点展开后
        50ms 高度还是 109（应该 118），自检直接判红。
        掐掉时**不落地**：落地是往旧目标收，同样会把新高度抹掉。
        """
        if self._reflow_anim is not None:
            self._abort_reflow()
        if not self._reflow_timer.isActive():
            self._reflow_timer.start()

    def _reflow(self):
        """算出新几何，然后**把旧几何钉住、用一条动画推到新几何**。

        为什么不能只调 `_relayout()`：那样是「瞬间跳」。用户要的是下方卡片**让位**，
        而且过程中不能重叠 —— 变高的卡和被推走的卡如果各走各的节奏，长高的那张会把
        下面前一帧还没挪走的卡盖住（就是报障截图里那个样子）。

        锁步为什么天然不重叠：卡片纵坐标是累加出来的
        `y_i = Σ_{j<i} (h_j + GAP)`，所以只要**每张卡的高度和 y 用同一条动画、
        同一个进度**去插值，中间每一帧的间距都还是 GAP。全程零重叠是算出来的，不是调出来的。

        ⚠️ 缓动必须**单调减速、绝不能回弹**：回弹会让下面前一张卡往上冲，间距被吃穿。

        ⚠️⚠️ 起点高度**不能取 `c.height()`**。`sync_height()` 是「先 `setFixedHeight(need)`
        再回调」的，走到这里时那张卡已经是**新**高度了 —— 拿它当起点的话，补间就变成
        「高度瞬间到位、只有下面的卡在慢慢挪」，开头那一两帧上面那张的下沿会**压进**
        下面那张（实测 `gap = -11px`）。所以起点取 `_h_settled` 里"上一次落定"的高度，
        先把它退回去，补间才有得插。
        """
        if self._reflow_anim is not None:
            self._abort_reflow()          # 兜底：起点必须和当前帧一致
        # 起点 y 用 `c.y()`（**实际几何**）而不是 `c._ly`（账本）：账本一旦和实际
        # 不一致，这里就会把起点算错，补间会从错误的位置滑过去 —— 读实际值最稳。
        old = [(c, c.y(), self._h_settled.get(c, c.height())) for c in self.scroll_items]
        # 再按目标排一遍：既拿到目标几何，也让 content / 阴影 / 滚动范围落到新状态
        # （`_relayout` 顺带把 `_h_settled` 刷成目标）
        self._relayout()
        new = [(c, c._ly, c.height()) for c in self.scroll_items]
        if old == new:
            self._sync_shadows()
            return
        # ⚠️ 退回起点必须放在 `_relayout()` **之后**：`_relayout` 会把几何推到终点，
        #    而起点要用 `old`。更要紧的是 `QVariantAnimation.start()` 不保证同步发一次
        #    `valueChanged(0.0)` —— 万一没发，这一帧就会停在终点几何上，表现为
        #    「先瞬间跳到位、再往回缩着滑」（实测采样到 y=342,342,327…）。
        # ⚠️⚠️ `_ly` 要**跟着一起退**。只 `move()` 不回写 `_ly` 的话，账上记的是终点 y、
        #    实际控件在起点 y；此后只要那条补间被打断（`_abort_reflow`），下一轮
        #    `_reflow` 会算出 `old == new` 直接早退 —— 而控件永远停在起点没动。
        #    实测现象：牌库卡 89、遗忘卡 y 停在 302（该 321），间距 -9 真重叠，
        #    而且**时好时坏**（要靠打断恰好落在这一瞬才会触发）。
        for c, y, h in old:
            if c.height() != h:
                c.setFixedHeight(h)
            c._ly = y
            c.move(c.x(), y)
        # 内容高先取两端的**较大值**：补间途中任何一帧的卡片底边都不会超出它，
        # 长的那张不会被父级裁掉。
        self.content.setFixedSize(self.content.width(),
                                  max(self.content.height(), self._plan_bottom(new)))

        self._reflow_from, self._reflow_to = old, new
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(self.REFLOW_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._on_reflow_step)
        anim.finished.connect(self._on_reflow_done)
        self._reflow_anim = anim
        anim.start()
        self._on_reflow_step(0.0)      # 起点帧由自己钉死，不依赖 start() 会不会发 0.0

    @staticmethod
    def _plan_bottom(plan):
        return max((y + h for _c, y, h in plan), default=0)

    def _on_reflow_step(self, v):
        old, new = self._reflow_from, self._reflow_to
        if old is None or new is None:
            return
        e = float(v)
        for (c, y0, h0), (_c2, y1, h1) in zip(old, new):
            h = int(round(h0 + (h1 - h0) * e))
            if c.height() != h:
                c.setFixedHeight(h)
            c._ly = int(round(y0 + (y1 - y0) * e))
            c.move(c.x(), c._ly)
        self.update()

    def _on_reflow_done(self):
        anim, self._reflow_anim = self._reflow_anim, None
        if anim is not None:
            anim.deleteLater()
        new = self._reflow_to
        self._reflow_from = self._reflow_to = None
        if new is not None:
            # 精确落到终点（补间是浮点插值，收尾必须归位，别留半像素）
            for c, y, h in new:
                if c.height() != h:
                    c.setFixedHeight(h)
                c._ly = y
                c.move(c.x(), y)
        # 落定 → 这一帧就是下一次让位的起点
        self._h_settled = {c: c.height() for c in self.scroll_items}
        # 动画途中不重建阴影贴图：贴图按 (宽, 高) 缓存，每帧都变尺寸等于每帧重算全部
        # 五张卡的模糊，会把 170ms 的动画拖成幻灯片。收尾补一次就够 ——
        # 阴影画在卡片**下面**，途中略有滞后看不出来。
        self._sync_shadows()
        self._apply_scroll()

    def _stop_reflow(self):
        """正在补间时被窗口尺寸变化打断：立刻落到终点，别让它继续按旧目标插值。"""
        if self._reflow_anim is None:
            return
        anim = self._reflow_anim
        anim.stop()                     # stop() 不发 finished，收尾要自己叫
        anim.deleteLater()
        self._reflow_anim = None
        self._on_reflow_done()

    def _abort_reflow(self):
        """掐掉正在跑的让位补间，**但保持当前这一帧的几何**（不落地）。

        与 `_stop_reflow` 的区别：那是"尺寸变了，旧目标已作废，先就地落地再由
        `_relayout` 重排"；这里是"高度又变了，旧目标也作废，但落地会往旧值收、
        把刚设好的新高度抹掉"，所以只清状态、几何保持原样，交给紧接着的
        `_reflow` 拿**当前**几何当新起点重算。
        """
        anim, self._reflow_anim = self._reflow_anim, None
        self._reflow_from = self._reflow_to = None
        # 当前这一帧成为新的"落定"状态 —— 否则下一轮 `_reflow` 会拿旧基线当起点，
        # 一上来就把卡片弹回去（回跳一帧）。
        # `_ly` 直接从控件读回来（而不是指望上一处一定写过）：这条不变量
        # 「账 == 实际几何」一旦破掉，下一轮 `_reflow` 会算出 old == new 而早退，
        # 控件就永远停在半路（实测踩过：遗忘卡卡在 302、和上面那张重叠 9px）。
        self._h_settled = {c: c.height() for c in self.scroll_items}
        for c in self.scroll_items:
            c._ly = c.y()
        if anim is None:
            return
        anim.stop()
        anim.deleteLater()

    # ------------- 运行状态页：从左往右滑入 -------------
    SHEET_MS = 260           # 单次动效 <= 400ms 的硬约束
    SHEET_CARD_GAP = 10      # 页内两张卡（运行状态 / 控制台）之间的间距

    def _sheet_rect(self):
        """状态页的几何：**铺满**标题栏以下的全部区域（左右到底、下方到底）。

        标题栏留着 —— 关闭方式就是再点一次那个仪表盘按钮，盖住就没法关了。
        不再留左右 20 / 下方 16 的版面边距：用户要的是"占满整个页面"，
        液体色的这张大页应当直接顶到窗口边缘。
        """
        top = self.head_w.geometry().bottom() + 1 + self.layout().spacing()
        return QRect(0, top, self.width(), max(80, self.height() - top))

    # ------------- 主题：白天 / 黑夜 -------------
    THEME_MS = 360           # 圆形扩散时长（<= 400ms 硬约束）；图标翻转 340ms 落在它之内

    def _reveal_radius(self, c):
        """圆心到四个角的最远距离 —— 圆要长到这么大才算盖住整页。"""
        dx = max(c.x(), float(self.width()) - c.x())
        dy = max(c.y(), float(self.height()) - c.y())
        return math.hypot(dx, dy)

    def apply_theme(self, name):
        """把整套皮肤切到 `name`。**不动画、不落盘**，纯状态切换。

        顺序有讲究：
          ① 先推调色板 —— 绘制代码读的就是那些模块全局；
          ② 重建并下发 QSS（QSS 是 import 时算死的字符串，不下发则所有控件文字停在老配色；
             `setStyleSheet` 本身会触发全控件重新 polish，所以按钮/标签不用手动刷）；
          ③ 换背景 —— 两套皮肤的 Frost 都缓存着，这里只是换引用（0 成本）；
          ④ 投影贴图同理（缓存 key 里带主题）；
          ⑤ 最后补"写完就固定"的行内样式：`setStyleSheet` 直接写在控件上的那两处，
             QSS 换掉也压不住它们，只能主动补一遍。
        """
        use_palette(name)
        self._theme = name
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(QSS)
        if name not in self._frosts:
            self._frosts[name] = self._make_frost()
        self.frost = self._frosts[name]
        self._attach_frost()
        self._shadow_key = None
        self._sync_shadows()
        for c in (self.cap_god, self.cap_free, self.cap_top):
            c._sync_label()
        if self.val["hook"].styleSheet():
            self.val["hook"].setStyleSheet(f"color: {C_WARN};")
        # 控制台的分段控件是**纯自绘**的：轨道底 / 指示器 / 单色荧光的色相都取模块全局，
        # 换肤后必须显式重绘（不能指望 setStyleSheet 的 repolish 一定覆盖到自绘路径）。
        self.console_card.update()
        self.console_card.seg.update()
        # ⚠️ 必须走 `_apply_scroll()` 而不是 `update()`：视口上下那两条渐变帘是
        #    **一张按背景取色缓存起来的贴图**（`EdgeVeil._key` 里只有位置和尺寸，
        #    不带主题）。换了主题只 update() 的话帘子会一直用旧主题的那张 ——
        #    实测暗色下会在视口下沿横贯一条**发亮的浅色带**。
        #    `_attach_frost()` 已经把 `_key` 清空，这里再跑一次就会重新取色。
        self._apply_scroll()
        # 标题栏的深浅档跟着主题走：换肤后必须重下发，否则浅色皮肤配一条深色标题栏。
        # 放在最后 —— 让整帧都换完了再动非客户区。
        self._sync_caption()

    def set_glow_mode(self, mode):
        """控制台：切荧光模式（彩色 / 单色 / 关闭）。**改全局 + 广播 + 落盘**。

        荧光是**所有玻璃控件各自画**的（`GlassBase._paint_glow`），绘制时读的是模块级
        `GLOW_MODE`，所以这里只要换掉那个全局、再让每张卡重新认一次状态
        （`refresh_glow()` 负责开/收 30fps 表 —— `off` 模式下绝不许有表在跑）。
        返回是否真的变了。
        """
        global GLOW_MODE
        if mode not in GLOW_MODES:
            return False
        changed = mode != GLOW_MODE
        GLOW_MODE = mode
        # 反方向同步一次控制台的指示器：这条路径也可能是**程序化**调的
        # （开机还原偏好、自检直接调），那时用户并没有点过任何一段。
        # `emit=False` 是关键 —— 用户点的那条路是「控件 → 信号 → 这里」，若这里再
        # 发一次信号就会绕回去重入；逻辑下标在 `setMode` 里是先落地的，所以
        # 从信号绕回来也不会自激，但少一次重入总是更干净。
        self.console_card.seg.setMode(mode, emit=False)
        self.console_card.sync_hint(mode)
        # `findChildren` 是递归的：滚动区的 5 张、状态页、页内两张卡都在里面。
        # 单色模式的色相由主题决定（GLOW_MONO），所以换肤后也要重绘一次 —— 见 apply_theme。
        for w in self.findChildren(GlassBase):
            w.refresh_glow()
        self.update()
        if changed:
            save_glow_pref(mode)
        return changed

    def _prebuild_other_theme(self):
        """提前把另一套皮肤的素材算好（窗口显示之后 500ms 触发，见 __init__）。"""
        self._prebuild_theme("dark" if self._theme == "light" else "light")

    def toggle_theme(self, animate=True):
        """点右上角那个开关：图标翻面 + 从图标中心向整页扩散地换主题。"""
        if self._theme_anim is not None:
            return                # 转场中不接第二次，免得遮罩内容和新旧主题对不上
        target = "dark" if self._theme == "light" else "light"
        if not animate:
            self.apply_theme(target)
            self.b_theme.setNight(target == "dark", animate=False)
            save_theme_pref(target)
            return

        old = self._theme
        # ① 先抓一张**旧主题的整帧**（此刻面板还是旧主题）。
        #    抓之前把开关自己藏起来 —— 遮罩铺上之后这个按钮会被 raise_() 到最上层继续
        #    播翻转动画，快照里若也留着一枚图标，两层图标会叠在一起（像描了两遍）。
        self.b_theme.hide()
        pm_old = self.grab()
        # ② 新主题**当场落地**（活的面板立刻就是新主题）。遮罩盖的是旧帧、中间留一个洞，
        #    洞里露出来的就是活的新主题 —— 扫到哪儿哪儿是真的新主题，不是拿图糊上去的。
        #    收尾时圆已覆盖全屏，撤掉遮罩和新主题严丝合缝，**这一侧不会有"晚一拍"**。
        self.apply_theme(target)
        self.b_theme.show()
        self.b_theme.setNight(target == "dark")

        # ③ 圆心 = 开关中心（`x()` 是 head_w 局部的，要换算成面板坐标）
        c = QPointF(self.b_theme.mapTo(self, QPoint(self.b_theme.width() // 2,
                                                   self.b_theme.height() // 2)))
        rmax = self._reveal_radius(c)
        self.reveal.setup(pm_old, c, rmax)
        self.reveal.setGeometry(0, 0, self.width(), self.height())
        self.reveal.show()
        self.reveal.raise_()
        self.b_theme.raise_()          # 图标浮在遮罩之上：翻转动画全程看得见

        # ④ 圆从图标中心长到盖住最远的那个角
        a = QVariantAnimation(self)
        a.setDuration(self.THEME_MS)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.valueChanged.connect(lambda v: self.reveal.set_radius(rmax * float(v)))
        a.finished.connect(self._finish_reveal)
        self._theme_anim = a
        self._theme_target = target
        a.start()
        save_theme_pref(target)

    def _finish_reveal(self):
        """扩散收尾 —— 也可能被 resize 提前打断（那张整帧就对不上新尺寸了）。

        新主题在 `toggle_theme` 里就已经落地了，所以这里其实只是"把遮罩撤掉"。
        `apply_theme` 留一道保险：万一哪天改成延后落地，这个函数也不会把主题落下。

        ⚠️ `stop()` **不发 `finished`**：打断那条路必须自己调进这里，否则遮罩会永远
        留在屏幕上（和状态页滑入动画收尾是同一个坑）。
        """
        a = self._theme_anim
        if a is None and not self.reveal.isVisible():
            return
        self._theme_anim = None
        if a is not None:
            a.stop()
        if self._theme != self._theme_target:
            self.apply_theme(self._theme_target)
        self.reveal.hide()
        self.b_theme.raise_()
        self.update()

    def isSheetOpen(self):
        return self._sheet_open

    def toggle_status_sheet(self):
        """点仪表盘按钮：开 / 关状态页。"""
        # 主题转场中先把转场落地：状态页会被 raise_ 到遮罩之上，露出来的会是**旧主题**
        # 的它，和正在扩散的新主题同时对不上。转场只有 360ms，落地一下就好。
        if self._theme_anim is not None or self.reveal.isVisible():
            self._finish_reveal()
        opening = not self._sheet_open
        # ⚠️ **窗口高低的调整必须排在 `_slide_sheet` 之前**。两个原因：
        #    ① 页面几何是按窗口高算的，先调窗口才不会用旧高度摆一遍；
        #    ② 更关键：`resize()` 会让 Qt 的统一动画时钟重启，**同一 tick 里刚 start()
        #       的指针旋转会被这次重启带走** —— 实测 `state=Running` 但 30ms 后
        #       `currentTime` 仍是 0（指针该转不转）。放到前面就没这回事。
        if opening:
            self._h_restore = self.height()
            self._h_grown = None
            self._fit_sheet_window()
        else:
            # ⚠️ **关页时缩窗口要等滑出动画跑完**，不能和它同 tick：`resize()` 会让 Qt 的
            #    统一动画时钟重启，同一轮里刚 `start()` 的指针旋转会被这次重启带走
            #    —— 实测 `state=Running` 但 `currentTime` 一直停在 0（指针该转不转）。
            #    顺带一个好处：页面滑走了窗口再收，观感比"边滑边缩"稳。
            QTimer.singleShot(self.SHEET_MS + 20, self._restore_sheet_window)
        self._sheet_open = opening
        self.b_dash.setOn(self._sheet_open)
        self._slide_sheet(self._sheet_open)
        # 打法推演**不再跟状态页绑定**：它已经搬回主页面（滚动区），由卡片自己的
        # 开关驱动（见 `_on_plan_toggle`）。

    # ------------- 状态页：窗口自动长高 -------------
    # 为什么不用"页内滚动"：滚轮在状态页上是**穿透给底层滚动区**的（ui_check 钉着这条
    # 既有行为），改成页内滚动会动到它。把窗口撑到装得下，对其余行为零影响。
    SHEET_PAD = 8            # 页内卡片的窄边（与 _place_sheet_cards 的 pad 同值）

    def _sheet_card_h(self, c):
        """一张页内卡该有多高。⚠️ 读 sizeHint 前先 activate（同 `_place_sheet_cards`）。"""
        try:
            c._lay.activate()
        except Exception:
            pass
        return max(getattr(c, "MIN_H", 60), c._lay.sizeHint().height())

    def _sheet_need_h(self):
        """页内三张卡全部装下所需的面板高度。"""
        need = self.SHEET_PAD * 2 + sum(self._sheet_card_h(c) for c in self.sheet_cards)
        need += self.SHEET_CARD_GAP * (len(self.sheet_cards) - 1)
        return self._sheet_rect().top() + need

    def _fit_sheet_window(self, force=False):
        """把窗口调到「三张卡都装得下」。**只在我们管得着的高度上动手**：
        用户自己拖过窗口（当前高 != 我们记的那两个值）就一律不抢。"""
        if self._h_restore is None:
            return
        cur = self.height()
        grown = self._h_grown
        if not force and ((grown is not None and cur != grown)
                          or (grown is None and cur != self._h_restore)):
            return                       # 用户已经自己拖过了 → 别抢
        base = max(self.PANEL_MIN_H, self._h_restore or self.PANEL_MIN_H)
        # ⚠️ 目标高度是 `max(原始高度, 实际需要)`，**不是**"装得下就退回原始高度"：
        #    内容缩小但仍在原始高度里装不下时，退回去就又裁了（踩过：
        #    切到"不在战斗中"后窗口退回 350，卡底 333 > sheet 293）。
        target = max(base, self._sheet_need_h())
        try:
            avail = self.screen().availableGeometry().height() - 60   # 给边框/任务栏留余量
            target = min(target, max(self.PANEL_MIN_H, avail))
        except Exception:
            pass
        if target != cur:
            self.resize(self.width(), target)
        self._h_grown = target if target > base else None

    def _restore_sheet_window(self):
        """关页 → 还原到打开前的高度（只有当窗口还停在我们撑开的高度时才还原）。

        ⚠️ 它是被 `QTimer.singleShot` 延后调的（见 `toggle_status_sheet`），所以这里
        必须自己确认"此刻页面真的还是关着的"——用户可能在这几十毫秒里又点开了。
        """
        if self._sheet_open:
            return                       # 又开回来了，别缩
        if self._h_grown is not None and self.height() == self._h_grown:
            self.resize(self.width(), max(self.PANEL_MIN_H, self._h_restore or self.PANEL_MIN_H))
        self._h_grown = None
        self._h_restore = None

    def _slide_sheet(self, show):
        """`show`：从左侧外面往右推进到位；否则反向收回左边。

        `_sheet_rect().left()` 是 0，所以"外面"就是负 x（-width）。
        """
        r = self._sheet_rect()
        off = r.width()                       # 完全滑出**左**边界之外
        self.sheet.setFixedSize(r.width(), r.height())
        if self._sheet_anim is not None:
            self._sheet_anim.stop()
        if show:
            self.sheet.move(r.left() - off, r.top())
            self.sheet.show()
            self.sheet.raise_()
            self._place_sheet_cards()
            self._unfold_card(0.0)            # 从 0 宽起展开（见 _unfold_card）
        a = QPropertyAnimation(self.sheet, b"pos", self)
        a.setDuration(self.SHEET_MS)
        a.setEasingCurve(QEasingCurve.Type.OutCubic if show
                         else QEasingCurve.Type.InCubic)
        a.setStartValue(QPoint(int(self.sheet.x()), r.top()))
        a.setEndValue(QPoint(r.left() - (off if not show else 0), r.top()))
        if show:
            # 卡片的展开**锁在页面动画的每一帧上**，不各跑各的 ——
            # 两条独立动画只要错一帧，卡片就会先被页面的边界切掉、或者反过来盖住旧内容。
            a.valueChanged.connect(self._on_sheet_anim_step)
        a.finished.connect(self._on_sheet_anim_done)
        self._sheet_anim = a
        # 指针转一圈，**和页面滑动同起跑线、同时长** → 页面铺到位那一刻刚好转满一圈。
        # 时长直接读页面这条动画的 duration，不另写一个常量（见 DashButton.spin_once）。
        # 收回时倒着转，和"转出去 / 转回来"对应。
        # 指针是纯自绘量、不参与布局，所以它可以独立跑 —— 不像卡片展开必须锁在页面
        # 那条动画的 valueChanged 上（那两条错一帧就会被页面边界切掉）。
        self.b_dash.spin_once(ms=a.duration(), reverse=not show)
        a.start()

    def _unfold_card(self, e):
        """按页面推进比例 `e` 把页内两张卡**展开**成 `W*e - 2*pad` 宽，并钉住。

        ⚠️ 卡片是状态页的子控件，页面从左外侧滑入时它们的面板坐标本来跟着一起走 ——
        于是**左缘**要等页面左缘到位（动画最后一刻）才进画面，中途只看得见
        "一块没有左边的白块"。用户报的「白卡片晚一拍才出现」就是这个观感：
        蓝底擦进来的时候，卡片还不是"一张卡片"。

        两处一起改：
        · 局部 x 取 `pad - sheet.x()`，抵消页面的位移 → 卡片的**面板 x 恒为 pad**，
          左缘从第一帧起就钉在那里，页面的左边缘还没进画面时它就是完整的左缘；
        · 宽度取 `W*e - 2*pad` → 卡片右缘永远离页面前沿 `pad`，**卡片全程都在页面内部**
          （不会被页面的边界裁掉，右角的圆角也就不会缺），左边也始终留着 `pad` 的页底色。
        于是整段动画里它们都是**有左有右、圆角齐全的卡片在展开**，页面在它们外面铺开。
        """
        pad = 8
        w = max(0, int(round(self.sheet.width() * e)) - pad * 2)
        dx = pad - self.sheet.x()
        for c in self.sheet_cards:
            c.setFixedWidth(w)
            c.move(dx, self._sheet_card_y.get(c, pad))

    def _on_sheet_anim_step(self, _pos):
        s = self.sheet
        w = float(max(1, s.width()))
        self._unfold_card(min(1.0, max(0.0, 1.0 + s.x() / w)))

    def _on_sheet_anim_done(self):
        """滑入/滑出动画收尾：清掉引用，**并把几何归位/补算一次**。

        ⚠️ 动画进行中 `_sync_sheet_geometry()` 是主动跳过的（怕把动画拽断），
        所以这段时间里窗口若被拉伸 / 收缩，状态页就会**停在旧几何** ——
        动画结束后如果不补这一次，页面就比面板矮一截、下方露出一条背景
        （用户报过"蓝色背景没包住下方、留了空隙"）。
        滑出时则收尾隐藏：留着会盖住标题栏下方的鼠标事件。
        `_place_sheet_cards()` 负责把展开用的临时宽度/位置还原成正式几何
        —— 动画被中途 `stop()` 掉时 `finished` 不发，所以下一次开页也会由
        `_slide_sheet` 里的 `_unfold_card(0.0)` 重新起步，两条路都不会留脏状态。
        """
        self._sheet_anim = None
        self._place_sheet_cards()
        if self._sheet_open and self.sheet.isVisible():
            self._sync_sheet_geometry()
        else:
            self.sheet.hide()

    def _place_sheet_cards(self):
        """把页内三张卡（运行状态 / **遗忘手牌** / 控制台）从左上角依次码下来，各留 8px 窄边。

        三张都是**尺寸按内容定死**：宽度 = 页宽 - 16（面板宽锁死 576，实际是恒定值），
        高度 = 各自的 sizeHint，**永远不跟着窗口纵向拉伸**。
        高度走过两版：最早按内容 → 后来"撑满整页"（用户要"背景占满"）→ 现在回到按内容，
        因为用户拖窗口时嫌卡片跟着长高，明确要求「卡片大小不随拖拽窗口变大」。
        下方那片大面积留给液体色的页面底色。

        只留 8px 窄边（而不是 20/16 的版面边距）：这一页是"占满整个窗口"的整页覆盖层，
        卡片在它内部只需要一点点呼吸缝。
        """
        pad = 8
        y = pad
        for c in self.sheet_cards:
            c.setFixedWidth(max(80, self.sheet.width() - pad * 2))
            # ⚠️ **必须先 activate() 再读 sizeHint**：`setFixedWidth` 刚把布局标脏，
            #    这时 `sizeHint()` 返回的是**上一个宽度下的缓存值**。推演卡的内容会
            #    随数据变（chips 显隐），不 activate 的话量到的是旧高度 ——
            #    实测表现是"卡片按 65 摆、内容 128 高"，底下那张直接被裁掉一截。
            try:
                c._lay.activate()
            except Exception:
                pass
            c.setFixedHeight(max(getattr(c, "MIN_H", 60), c._lay.sizeHint().height()))
            c.move(pad, y)
            self._sheet_card_y[c] = y
            y += c.height() + self.SHEET_CARD_GAP

    def _sync_shadows(self):
        """卡片尺寸变了（窗口拉伸、牌库卡长高）才重建阴影贴图。

        只跟滚动区这几张卡。状态卡在独立页面里、底下还有一层实心玻璃页，
        再给它投影只会显脏，所以不参与。

        key 里带上主题：两套皮肤的投影颜色/强度不同（C_SHADOW_RGB / SHADOW_MUL），
        不带主题的话切过去会沿用上一套的贴图。
        """
        key = (self._theme, tuple((w.width(), w.height()) for w in self.scroll_items))
        if key != self._shadow_key:
            self._shadow_key = key
            self._build_shadows()

    def _view_h(self):
        return max(1, self.viewport.height())

    def _max_scroll(self):
        return max(0, self.content.height() - self._view_h())

    def _apply_scroll(self):
        self._scroll = max(0.0, min(self._scroll, float(self._max_scroll())))
        self.content.move(0, -int(round(self._scroll)))
        self._check_pop()
        self._update_veils()
        self.update()

    def _on_scroll_anim(self, v):
        try:
            self._scroll = float(v)
        except (TypeError, ValueError):
            return
        self._apply_scroll()

    def _scroll_to(self, target, smooth=True):
        target = max(0.0, min(float(target), float(self._max_scroll())))
        if abs(target - self._scroll) < 0.5:
            return
        self._sanim.stop()
        if smooth:
            self._sanim.setStartValue(self._scroll)
            self._sanim.setEndValue(target)
            self._sanim.start()
        else:
            self._scroll = target
            self._apply_scroll()
        # 先掐掉可能还在跑的淡出动画，否则它会把刚点亮的滚动条又拖回 0
        self._sbar_anim.stop()
        self._sbar_a = 1.0
        self._sbar_timer.start()

    def _scroll_by(self, d):
        self._scroll_to(self._scroll + d)
        return  # 供 QShortcut 调用

    def _fade_sbar(self):
        self._sbar_anim.stop()
        self._sbar_anim.setStartValue(self._sbar_a)
        self._sbar_anim.setEndValue(0.0)
        self._sbar_anim.start()

    def _on_sbar_anim(self, v):
        try:
            self._sbar_a = float(v)
        except (TypeError, ValueError):
            return
        self.update()

    # 「滑动出现」时从左向右弹出：只在卡片被滚动带进视口时播一次
    def _check_pop(self):
        if not self._pop_armed:
            return              # 入场落位之前不抢跑
        top, bot = self._scroll, self._scroll + self._view_h()
        seq = 0                 # 同一批里第几个弹 → 决定错开延迟
        for i, c in enumerate(self.scroll_items):
            ctop, cbot = c._ly, c._ly + c.height()
            # ① 完全滚出视口 → 复位，下次滑回来重新弹一次
            if cbot <= top + self.POP_EXIT_SLOP or ctop >= bot - self.POP_EXIT_SLOP:
                if c._popped:
                    self._stop_pop(c)
                    c._popped = False
                    c.move(-26, c._ly)
                continue
            if c._popped:
                continue
            # ② 只露出一小角先不弹，免得停在边界上下抖时反复弹
            if cbot <= top + self.POP_ENTER_MIN or ctop >= bot - self.POP_ENTER_MIN:
                continue
            c._popped = True
            self._pop_in(c, seq)
            seq += 1

    def _stop_pop(self, c):
        """掐掉某张卡上可能还在跑的弹出动画与透明效果。

        必须「先停动画、再摘效果」：QWidget.setGraphicsEffect() 会立刻 delete 掉
        旧 effect，若旧的不透明度动画还指着它，下一次 tick 就是悬空指针（实测会
        直接把进程打崩），所以摘之前一定要先 stop。
        """
        for a in (getattr(c, "_pop_anims", None) or ()):
            try:
                a.stop()
            except Exception:
                pass
        c._pop_anims = None
        try:
            c.setGraphicsEffect(None)
        except Exception:
            pass

    def _pop_in(self, c, idx):
        """从左向右弹出：位移带轻微过冲 + 淡入。动画结束就摘掉透明效果，
        避免长期挂着 QGraphicsOpacityEffect 让每次重绘都走离屏合成。

        ⚠️⚠️ **只动 x，y 每帧现读 `c._ly`** —— 不能用 `QPropertyAnimation(c, b"pos")`
        把整条 pos 都交给它。弹出动画的终点 y 是**启动那一刻**记下来的常量，而让位
        补间（`_reflow`）会改 y；两条动画抢同一个 `pos` 时后写的赢，于是：
        让位把卡片推到新位置 → 弹出动画的 `pa` 继续按旧 y 插值、`cleanup` 更是直接
        `move(0, 旧y)` 把它拽回去 → 卡片和上面那张重叠 9px。
        而且只在「卡片刚弹出来 + 同时变高」时才复现（实测真机上时好时坏，最难查的那类）。
        """
        self._stop_pop(c)
        c.move(-26, c._ly)
        eff = QGraphicsOpacityEffect(c)
        eff.setOpacity(0.0)
        c.setGraphicsEffect(eff)

        def start():
            if c.graphicsEffect() is not eff:
                return                  # 已被新一轮弹出取代，这一轮作废
            pa = QVariantAnimation(c)
            pa.setStartValue(-26.0)
            pa.setEndValue(0.0)
            pa.setEasingCurve(QEasingCurve.Type.OutBack)   # 轻微过冲 = “弹出”感
            pa.setDuration(300)
            pa.valueChanged.connect(
                lambda v, cc=c: cc.move(int(round(v)), cc._ly))
            fa = QPropertyAnimation(eff, b"opacity", self)
            fa.setStartValue(0.0)
            fa.setEndValue(1.0)
            fa.setEasingCurve(QEasingCurve.Type.OutCubic)
            fa.setDuration(220)
            c._pop_anims = (pa, fa)      # 持引用，防被 GC

            def cleanup():
                c._pop_anims = None
                if c.graphicsEffect() is not eff:
                    return              # 效果已经换过了，别去动别人的
                c.move(0, c._ly)        # y 现读：这 360ms 里让位可能已经把它挪走了
                try:
                    c.setGraphicsEffect(None)
                except Exception:
                    pass

            QTimer.singleShot(360, cleanup)
            pa.start()
            fa.start()

        QTimer.singleShot(int(idx * self.POP_STAGGER), start)

    def _arm_pop(self):
        """入场落位：启动时**不播**任何动画，卡片直接就在位置上。

        用户明确要的是「滚动时卡片出现才弹」，不是「开应用时弹一遍」。
        所以这里只把已经在视口里的卡片标记成"已弹过"，视口外的留 `_popped=False`
        —— 等滚动把它们带进来时，`_check_pop` 才会触发从左弹出。
        """
        self._do_layout()                    # 先保证布局生效，_view_h() 才是真的
        self._pop_armed = True
        top, bot = self._scroll, self._scroll + self._view_h()
        for c in self.scroll_items:
            self._stop_pop(c)
            c.move(0, c._ly)
            ctop, cbot = c._ly, c._ly + c.height()
            already = not (cbot <= top + self.POP_ENTER_MIN
                           or ctop >= bot - self.POP_ENTER_MIN)
            c._popped = already

    def wheelEvent(self, e):
        pd = e.pixelDelta()
        if not pd.isNull():
            # 触控板 / 高分辨率滚轮给的是像素增量，直接跟手，别再做补间
            self._sanim.stop()
            self._scroll_to(self._scroll - pd.y(), smooth=False)
        else:
            self._scroll_to(self._scroll - e.angleDelta().y() / 120.0 * self.SCROLL_STEP)
        e.accept()

    # 拖拽滚动：与点击共存（位移超过阈值就吞掉 release，胶囊不会被误触）
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == ev.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            # 点到面板上任何地方 → 确保「不抢焦点」挂着（窗口重建后可能掉了）
            self._panel_noactivate(True)
            self._press_y = ev.globalPosition().y()
            self._dragging = False
        elif t == ev.Type.MouseMove and self._press_y is not None:
            if not (ev.buttons() & Qt.MouseButton.LeftButton):
                # 左键已经不按了却还留着起点：说明拖拽中途把 release 弄丢了
                # （拖出窗口、alt-tab、被拖的控件销毁…）。状态卡死的话，之后光标
                # 随便划一下都会把整页拖走 —— 这里自愈。
                self._press_y = None
                self._dragging = False
                return super().eventFilter(obj, ev)
            dy = ev.globalPosition().y() - self._press_y
            if self._dragging or abs(dy) > self.DRAG_SLOP:
                self._dragging = True
                self._sanim.stop()
                self._scroll_to(self._scroll - dy, smooth=False)
                self._press_y = ev.globalPosition().y()
                return True
        elif t == ev.Type.MouseButtonRelease:
            if self._dragging:
                self._dragging = False
                self._press_y = None
                return True        # 吞掉，避免拖拽结束被当成点击
            # 普通点击：起点必须清掉。留着的话，之后**不按任何键**只是把光标划过去，
            # MouseMove 也会被当成「还在拖」，整页跟着光标跑。
            self._press_y = None
        return super().eventFilter(obj, ev)

    def _wire_hint(self):
        self.status_card.lb_hint.setText("内存只读")

    # ------------- 毛玻璃资源 -------------
    def showEvent(self, e):
        super().showEvent(e)
        # 入场落位（内含一次 _do_layout）。启动**不播**动画，只有滚动才弹。
        QTimer.singleShot(0, self._arm_pop)
        QTimer.singleShot(120, self._sync_shadows)   # 兜底：等文字度量稳定后校一次阴影
        # 原生窗口可能在 show / DPI 变化时被重建（HWND 变了，topmost 位就丢了）→ 补一次
        if self._topmost:
            QTimer.singleShot(0, lambda: set_window_topmost(self.winId(), True))
        # 同理，WS_EX_NOACTIVATE 也会随窗口重建丢掉 → 每次 show 都无条件重挂一次
        self._noact = None
        QTimer.singleShot(0, self._sync_noactivate)
        # 同理：标题栏深浅档也是记在 HWND 上的，窗口一重建就没了 → 补一次。
        self._cap_dark = None
        QTimer.singleShot(0, self._sync_caption)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 背景是固定大画布，拉窗口不需要重建 → 拖动拉伸不会卡
        QTimer.singleShot(0, self._do_layout)

    def mousePressEvent(self, e):
        # 点到面板自身的空白处（四周留白、标题行）→ 同样把「不抢焦点」挂回去。
        # 上面那些卡片/视口走的是 eventFilter，这条补的是过滤器覆盖不到的自家区域。
        self._panel_noactivate(True)
        super().mousePressEvent(e)

    # ------------- 点面板不抢游戏焦点 -------------
    def _sync_noactivate(self):
        """把 WS_EX_NOACTIVATE 挂上去。

        以前这里按「是不是在给能量输入框打字」决定挂/摘 —— 无限精力移除后，
        面板上**再也没有需要键盘输入的控件**，所以恒为挂上（不再有摘掉的分支）。
        只在 show 之后调（那时才有真的原生窗口）；窗口重建、DPI 切换之后也靠它自愈。

        ⚠️ 副作用：面板再也不会成为活动窗口，所以 F1/F2/F4 这类窗口级 QShortcut
        全部失效。要恢复得改成系统级全局热键（RegisterHotKey）—— 用户知悉、暂不处理。
        """
        self._panel_noactivate(True)

    def _panel_noactivate(self, on):
        """切换「点面板不抢游戏焦点」。值没变就直接 return —— 白跑一次 SetWindowPos 会抖。"""
        want = bool(on)
        if self._noact == want:
            return
        # 失败就记 None，下次再试（别把「失败了」记成「已经是这个状态」而永久跳过）
        self._noact = want if set_window_noactivate(self.winId(), want) else None

    # ------------- 标题栏跟随我们的主题（只下发深浅档，不做玻璃） -------------
    def _sync_caption(self):
        """把标题栏的深浅档设成跟当前皮肤一致。

        DWM 属性在**原生窗口存在之后**才有，所以只能在 show 之后调；窗口重建 / DPI 变化
        之后也靠它自愈（`showEvent` 里已经复位了缓存）。面板永远是常开的，没有开关。
        """
        self._panel_caption(self._theme == "dark")

    def _panel_caption(self, dark):
        """值没变就直接 return —— 每次下发都要过一趟 DWM，能省则省。"""
        want = bool(dark)
        if self._cap_dark == want:
            return
        # 失败（老系统 / 属性被拒）就记 None，下次再试；别把"失败了"记成"已经是这个状态"。
        self._cap_dark = want if set_window_caption_dark(self.winId(), want) else None

    def nativeEvent(self, eventType, message):
        """拦 WM_MOUSEACTIVATE，决定这次点击要不要把面板激活（=要不要抢游戏焦点）。

        三个 PyQt6 的坑（都踩过，写清楚免得下次再撞）：

        1) **绝不能 `return super().nativeEvent(...)`**。`QWidget.nativeEvent()` 的
           默认实现返回的是 `(False, **None**)` —— 第二项是 None 不是 0。PyQt 把它
           当 LRESULT 交给 Qt 之后**直接 access violation 整个进程崩掉**；而且是崩在
           `show()` 里，报错栈只指到 `show()` 那一行，看上去像"窗口一显示就崩"，
           极难往 nativeEvent 上想。哪怕什么都不处理，也必须 `return False, 0`。

        2) `eventType` 传进来是 **QByteArray**（`QByteArray(b'windows_generic_MSG')`）。
           本版 PyQt6 里它和 str 比较**也是 True**，所以 `== "windows_generic_MSG"`
           能跑；但用 `bytes()` 归一更明确，也不会被版本差异坑到。

        3) 光靠这里**不够**（真机实测过，见文件头的长注释）—— 必须叠 WS_EX_NOACTIVATE。
           这里的返回值只是"顺手再拦一道"，真正的保险是窗口样式。
        """
        try:
            et = bytes(eventType)      # QByteArray / bytes 都能转；其它类型当不匹配
        except Exception:
            et = b""
        if sys.platform == "win32" and et == b"windows_generic_MSG":
            try:
                msg = ctypes.cast(int(message), ctypes.POINTER(_WinMsg)).contents
            except Exception:
                return False, 0
            if msg.message == WM_MOUSEACTIVATE:
                # 一律不夺焦点。以前这里有个例外（点能量输入框要打字才放行），
                # 无限精力移除后面板再无输入控件，例外一并去掉。
                self._panel_noactivate(True)
                return True, MA_NOACTIVATE
        return False, 0

    def _do_layout(self):
        """重排整窗。**重入的请求要补跑，不能丢。**

        ⚠️ 原来写的是 `if self._laying: return` —— 拖拽窗口时 resizeEvent 是连着的，
        落在一次布局运行期间的那些请求会被无声丢掉；而**最后一次**往往正好被丢
        （拖拽停下时事件最密），于是窗口已经是新尺寸、状态页还停在旧几何，
        状态卡下方露出一截背景（用户看到的就是"卡片中间多了一道分界线、下面颜色不对"）。
        离屏自检复现不出：那里的 resize 是一次一次配 qWait 发的，不会重入。
        """
        # 尺寸一变，主题遮罩里那张"整帧"就和新窗口对不上了（会被拉花/留边）→ 立刻落地。
        # 放在重入判定**之前**：不然落在 `_laying` 期间的那次 resize 会把这条跳过。
        if self._theme_anim is not None or self.reveal.isVisible():
            self._finish_reveal()
        # 同理：正在跑的让位补间是按**旧**目标插值的，尺寸一变它的每一帧都是错的
        # → 先让它落地（`_stop_reflow` 自己会叫 `_on_reflow_done`），再按新尺寸重排。
        # 也在重入判定之前：落进 `_laying` 里的那次 resize 同样不能跳过这一条。
        self._stop_reflow()
        if self._laying:
            self._layout_again = True
            return
        self._laying = True
        try:
            # 最多补 3 轮（重排过程中自己又触发重排时收敛用；防病态死循环）
            for _ in range(3):
                self._layout_again = False
                # 关键：resizeEvent 早于布局生效，此时 viewport 还是默认几何。
                # 不强制跑一次布局，后面所有基于 viewport 尺寸的计算都会是错的。
                lay = self.layout()
                if lay is not None:
                    lay.activate()
                if (self.frost is None
                        or self.frost.w < self.width()
                        or self.frost.h < self.height()):
                    self._build_materials()
                self._relayout()
                self._sync_shadows()
                self._sync_sheet_geometry()
                self._apply_scroll()
                if not self._layout_again:
                    break
        finally:
            self._laying = False

    def _sync_sheet_geometry(self):
        """窗口尺寸变了 → 状态页跟着变。

        正在滑入/滑出时**不抢**：动画自己会走到终点位置，这里插手会把动画拽断。
        """
        if self._sheet_anim is not None or not self.sheet.isVisible():
            return
        r = self._sheet_rect()
        if self.sheet.geometry() == r:
            return
        # ⚠️ 必须**先 setFixedSize 再 move**：sheet 的尺寸是 setFixedSize 锁住的，
        #    只调 setGeometry 的话新尺寸会被旧的 min/max 钳回去（本仓库踩过这个坑）。
        self.sheet.setFixedSize(r.width(), r.height())
        self.sheet.move(r.left(), r.top())
        self._place_sheet_cards()

    def _make_frost(self):
        """按**当前**调色板造一份背景素材。

        单份要 ~250ms（576×1400 三通道三次盒式模糊），所以两套皮肤各留一份缓存
        （`self._frosts`），切换时只是换个引用 —— 详见 `_prebuild_theme()`。
        Frost 的渐变与光斑都是构造时读模块全局的，所以**必须在对应调色板下造**。
        """
        return Frost(max(self.FROST_W, self.width()), max(self.FROST_H, self.height()))

    def _under_palette(self, name, fn):
        """临时把模块调色板切到 `name` 跑 fn，跑完恢复原样。

        预建另一套皮肤的素材要用：Frost / 阴影贴图都是"按当时调色板"造出来的，
        必须在它那套颜色下造，但又不能把当前界面改花。
        """
        cur = THEME_NAME
        use_palette(name)
        try:
            return fn()
        finally:
            use_palette(cur)

    def _prebuild_theme(self, name):
        """把**另一套**皮肤的素材提前算好（背景 + 阴影贴图换成引用即可）。

        不预建的话，第一次切主题会在"点下去的那一帧"卡 250ms —— 正好卡在动画起步前，
        最刺眼。这里放在窗口显示之后 500ms 再算（见 `__init__` 里的 singleShot），
        那会儿用户刚看到面板、什么都没在动，停顿没人会注意到。
        """
        if name in self._frosts:
            return False

        def build():
            self._frosts[name] = self._make_frost()
            self._shadow_cache[name] = self._make_shadow_rows()

        self._under_palette(name, build)
        return True

    def _build_materials(self):
        # 背景按固定大画布预渲染，好处有二：
        # ① 拉伸窗口时直接沿用，不必重算模糊（拖动才跟手）；
        # ② 渐变与光斑不随窗口高度重新排布，视觉稳定。
        # 缓存里的贴图一旦比窗口小就作废（换掉后另一套皮肤下次切过去时再按需重建）。
        w = max(self.FROST_W, self.width())
        h = max(self.FROST_H, self.height())
        for k in [k for k, f in self._frosts.items() if f.w < w or f.h < h]:
            self._frosts.pop(k, None)
            self._shadow_cache.pop(k, None)
        if self._theme not in self._frosts:
            self._frosts[self._theme] = self._make_frost()
        self.frost = self._frosts[self._theme]
        self._attach_frost()

    def _attach_frost(self):
        for v in (self.veil_top, self.veil_bot):
            v._key = None              # 换了背景 → 帘子要重新取色
        # 状态页（含页内那几张卡）也要拿到 frost，否则 _paint_glass 直接 return、整页透明。
        # ⚠️ 这里必须**遍历 `sheet_cards`**，不能写死 `self.status_card` —— 漏一张就会有
        #    一张卡完全没有底（实测：控制台卡只画出描边，卡面是"破的"，一眼能看出来）。
        for w in [*self.scroll_items, self.sheet, *self.sheet_cards]:
            w.frost = self.frost
            w.frost_root = self
        self.update()

    def _make_shadow_rows(self):
        """按当前调色板造 rolling 区各卡的投影贴图（返回列表，不挂到自己身上）。"""
        out = []
        for w in self.scroll_items:
            r = w.height() / 2.0 if isinstance(w, Capsule) else R_SURFACE
            base = make_shadow(w.width(), w.height(), r, color=C_SHADOW_RGB,
                               sigma=SHADOW_BASE["sigma"], dy=SHADOW_BASE["dy"],
                               peak=SHADOW_BASE["peak"] * SHADOW_MUL)
            lift = make_shadow(w.width(), w.height(), r, color=C_SHADOW_RGB,
                               sigma=SHADOW_HOVER["sigma"], dy=SHADOW_HOVER["dy"],
                               peak=SHADOW_HOVER["peak"] * SHADOW_MUL)
            out.append((w, base, lift))
        return out

    def _build_shadows(self):
        if self.frost is None:
            self._build_materials()
        # 缓存以「主题」为键，但**卡片尺寸一变全部作废**（贴图是按 w/h 造的）
        sig = tuple((w.width(), w.height()) for w in self.scroll_items)
        if sig != self._shadow_sig:
            self._shadow_sig = sig
            # ⚠️ 这里**不能只重建当前主题**（原来的写法是 `clear()` 后只补当前那套）。
            #    那会把另一套皮肤那份预建结果一起丢掉 —— 下次切主题又得现算，
            #    `_prebuild_theme` 苦心经营的"切换 0 成本"就废了。
            #    而"卡片尺寸会变"这件事现在很常见（让位：牌库卡吃数据、遗忘卡展开），
            #    所以必须按"缓存里已经有哪些主题"逐个重造，尺寸才永远和当前几何一致。
            #    代价：每套 ~35ms，只在尺寸**真变**时付一次。纵向拖窗口不会触发
            #    （面板宽锁死、卡片高度跟内容走，sig 不动），所以拖拽手感不受影响。
            stale = sorted(self._shadow_cache)
            self._shadow_cache.clear()
            for name in stale:
                if name == self._theme:
                    continue                    # 当前主题下面统一算，省一次调色板来回切
                self._shadow_cache[name] = self._under_palette(
                    name, self._make_shadow_rows)
        out = self._shadow_cache.get(self._theme)
        if out is None:
            out = self._make_shadow_rows()
            self._shadow_cache[self._theme] = out
        self._shadows = out
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        if self.frost is None:
            p.fillRect(self.rect(), QColor(C_PAGE_FALLBACK))
            p.end()
            return
        p.drawPixmap(0, 0, self.frost.bg)

        # 阴影由父级（本控件）统一绘制。**只做纵向裁剪**（裁到滚动视口），
        # 否则滚出视口的卡片其阴影会糊到标题栏和按钮上；横向不裁，让阴影自然
        # 漫进左右留白 —— 横向也裁的话，卡片右缘会出现一条竖直的硬边。
        vp = self.viewport.geometry()
        p.save()
        p.setClipRect(QRect(0, vp.top(), self.width(), vp.height()))
        for w, base, lift in self._shadows:
            if not w.isVisible():
                continue
            pos = w.mapTo(self, QPoint(0, 0))
            t = getattr(w, "_hover", 0.0)
            pm, pad = base
            p.setOpacity(1.0)
            p.drawPixmap(pos.x() - pad, pos.y() - pad, pm)
            # 悬停：叠一层更大更低柔的阴影 → 读作“卡片浮起来”，且不改几何、不重排
            if t > 0.004:
                pm2, pad2 = lift
                p.setOpacity(t)
                p.drawPixmap(pos.x() - pad2, pos.y() - pad2, pm2)
        p.setOpacity(1.0)
        p.restore()

        # 纵向那两条裁剪硬边交给 veil_top / veil_bot 两个叠加控件去盖
        # （画在这里没用：卡片会把它盖掉）

        # 细滚动条：放在右侧沟槽里（卡片已窄了 SBAR_GUTTER），不再压住胶囊
        mx = self._max_scroll()
        if mx > 1 and self._sbar_a > 0.01:
            track_h = float(vp.height())
            thumb_h = max(26.0, track_h * self._view_h() / float(self.content.height()))
            ty = vp.top() + (track_h - thumb_h) * (self._scroll / mx)
            bw = 3.0
            bx = vp.right() - self.SBAR_GUTTER / 2.0 - bw / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            c = QColor(*C_SBAR_RGB)
            c.setAlpha(int(120 * self._sbar_a))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(bx, ty, bw, thumb_h), bw / 2.0, bw / 2.0)
        p.end()

    # ---- 视口边缘的渐变帘（叠加控件，见 EdgeVeil 的说明）----
    def _update_veils(self):
        if not hasattr(self, "veil_top"):
            return
        vp = self.viewport.geometry()
        mx = self._max_scroll()
        hgt = min(self.VEIL_H, max(0, int(vp.height() / 3)))
        drawn = []
        for veil, top, want in ((self.veil_top, True, self._scroll > 0.5),
                                (self.veil_bot, False, self._scroll < mx - 0.5)):
            if not want or hgt <= 2 or vp.height() <= 8:
                veil.hide()
                continue
            y = vp.top() if top else vp.bottom() - hgt + 1
            # 盖住整幅面板宽度：裁剪线是横贯的，只盖视口宽度会在左右留白里留下断口
            veil.setGeometry(0, y, self.width(), hgt)
            key = (y, self.width(), hgt, top)
            if veil._key != key:
                veil._key = key
                veil.set_image(self._make_veil(self.width(), hgt, y, top))
            veil.show()
            veil.raise_()
            drawn.append("top" if top else "bottom")
        self._veils_drawn = drawn          # 仅供自检断言，无副作用
        # ⚠️ 帘子靠 raise_() 才盖得住滚动内容，但这样一来它也会盖住**状态页** ——
        # 表现是状态页下半截被一条横贯的浅色带压住、中间出现一道水平分界线
        # （滚动 / 拉伸窗口都会走到这里，所以那两种操作下必现）。
        # 帘子服务于滚动内容，不该出现在整页覆盖层之上，所以这里把状态页抬回来。
        if hasattr(self, "sheet") and self.sheet.isVisible():
            self.sheet.raise_()

    def _make_veil(self, w, h, sy, top):
        """取背景同一块，再用 alpha 渐变把它擦成半透明 —— 盖上就是自然过渡。"""
        if self.frost is None or w <= 0 or h <= 0:
            return None
        bg = self.frost.bg
        y0 = max(0, min(max(0, bg.height() - 1), int(round(sy))))
        hh = max(1, min(bg.height() - y0, int(round(h))))
        strip = bg.copy(QRect(0, y0, max(1, bg.width()), hh))
        if strip.width() != w or strip.height() != h:
            strip = strip.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        img = QImage(strip.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        pp = QPainter(img)
        pp.drawPixmap(0, 0, strip)
        pp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        g = QLinearGradient(0, 0, 0, h)
        if top:                     # 上边缘：越靠上越实
            g.setColorAt(0.0, QColor(0, 0, 0, 255))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
        else:                       # 下边缘：越靠下越实
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 255))
        pp.fillRect(img.rect(), g)
        pp.end()
        return img

    # ------------- 操作 -------------
    @staticmethod
    def _name(key):
        return {"god": "无敌免疫伤害", "freeCards": "卡牌不消耗精力"}[key]

    def _cap(self, key):
        return {"god": self.cap_god, "freeCards": self.cap_free}[key]

    def on_toggle(self, key, on):
        """未连接游戏时也允许开关 —— 动画照常播放，便于先看效果/试动效。
        此时只改界面状态，不往游戏里下发；连接后由 do_attach 一次性补齐。"""
        if not self.attached:
            self.flash(f"预览：{self._name(key)} 已{'开启' if on else '关闭'}"
                       f"（连上游戏后自动生效）")
            return
        self.flash(f"{'开启' if on else '关闭'} · {self._name(key)}")
        self.worker.post(lambda w, k=key, o=on: w.trainer.set(k, o))

    def on_top_toggle(self, on):
        """窗口置顶开关。

        跟上面两个功能不同：这是**纯本地界面行为**，不下发给游戏，所以不走 worker、
        也不受「未连接/预览模式」影响 —— 断线、重连、重开游戏都不该把置顶弄丢。
        """
        ok = set_window_topmost(self.winId(), on)
        if on and not ok:
            # API 被系统拒了：把开关退回去，别让界面显示一个没生效的状态
            self.cap_top.blockSignals(True)
            self.cap_top.setChecked(False, animate=True, emit=False)
            self.cap_top.blockSignals(False)
            self.flash("置顶失败，系统拒绝了该操作")
            return
        self._topmost = bool(on) and (ok or not on)
        self.flash("面板已置顶，不会被其他窗口遮挡" if on else "已取消置顶")

    def _on_deck_toggle(self, on):
        """牌库预览是纯只读的，不需要下发游戏；只告诉 worker 要不要去读。"""
        self.worker.deck_want = bool(on)
        if on:
            self.deck_card.set_connected(self.attached)
            self.flash("已开启牌库预览，顺序即休息后补牌的顺序" if self.attached
                       else "已开启牌库预览，连上游戏后自动刷新")
        else:
            self.flash("已关闭牌库预览")

    def _on_plan_toggle(self, on):
        """打法推演同样是只读的（`battle(True)` 读手牌意图），只告诉 worker 要不要去算。"""
        self.worker.plan_want = bool(on)
        if on:
            self.plan_card.set_connected(self.attached)
            if self.attached:
                self._fetch_plan_now()      # 立刻给一版，不等 450ms 轮询
                self.flash("已开启打法建议")
            else:
                self.flash("已开启打法建议，连上游戏后自动推演")
        else:
            self.flash("已关闭打法建议")

    def _sheet_cards_changed(self):
        """页内卡片（遗忘卡）高度变了 → 重排页内卡并**按需长高窗口**。

        ⚠️ 不能复用滚动区那套 `_reflow_soon`：页内卡不进滚动内容，走的是
        `_place_sheet_cards` + `_fit_sheet_window` 这条完全不同的排布路径。
        """
        if not (self._sheet_open and self.sheet.isVisible()):
            return
        self._place_sheet_cards()
        self._fit_sheet_window()

    # ---------- 遗忘手牌 ----------
    # ---------- 打法建议 ----------
    def _fetch_plan_now(self):
        """展开状态页时插一次队，立刻算一版（不进 worker 的 450ms 常驻节奏）。"""
        def job(w):
            # ⚠️ 判 **worker 的** attached，不是 Panel 的 —— 只有 worker 真把
            #    frida session 挂上了，`trainer.battle()` 才调得动。拿 Panel 的状态
            #    去试会在未挂载时抛 `'NoneType' object has no attribute 'exports_sync'`，
            #    而那句话会原样显示到卡片上（自检里踩过）。
            if not w.attached:
                self._sig_plan.emit({"ok": False, "reason": "未连接游戏"})
                return
            try:
                data = build_plan(w.trainer)
            except Exception:
                data = {"ok": False, "reason": "推演失败（读取战况出错）"}
            self._sig_plan.emit(data)
        self.worker.post(job)

    def on_plan(self, d):
        """推演结果 → 卡片（卡片在滚动区，高度自适应由它自己 + 让位处理）。"""
        self.plan_card.update_data(d or {})

    def _on_forget_open(self):
        """展开选卡区 → 立刻拉一手列表（不进 worker 的常驻循环，用完就完）。"""
        if not self.attached:
            self.forget_card.note_forgotten('', False, '先连接游戏')
            return
        self._fetch_forget_list()

    def _fetch_forget_list(self):
        def job(w):
            try:
                data = w.trainer.forget_list()
            except Exception as ex:
                data = {"ok": False, "cards": [], "warning": f"读取失败: {str(ex)[:60]}"}
            # 回到 GUI 线程再碰控件（worker 是独立线程）
            self._sig_forget_list.emit(data)
        self.worker.post(job)

    def _on_forget_do(self, ptr_hex, stack_key):
        """点「确认遗忘」：真删。删完立刻重拉列表 —— 用户要的是"列表即时刷新"。"""
        if not self.attached:
            self.forget_card.note_forgotten('', False, '未连接游戏')
            return

        def job(w):
            res = None
            try:
                res = w.trainer.forget(ptr_hex, stack_key)
            except Exception as ex:
                res = {"ok": False, "err": str(ex)[:60]}
            self._sig_forget_res.emit(res or {})
        self.worker.post(job)

    def on_forget_result(self, res):
        res = res or {}
        ok = bool(res.get("ok"))
        if ok and not res.get("orderOk", True):
            # 数量对了但顺序没对上：不阻塞，只提示（次序对这张卡的使用没影响）
            self.flash(f"已遗忘 {res.get('name') or ''}（牌序有偏移）")
        elif ok:
            self.flash(f"已遗忘 {res.get('name') or ''}"
                       f"（{res.get('before')} → {res.get('after')}）")
        self.forget_card.note_forgotten(res.get("name") or "", ok,
                                        res.get("err") or "")
        if ok:
            self._fetch_forget_list()      # 列表即时刷新

    def _push_all(self, w, states):
        """把界面上当前的开关状态一次性同步给游戏（values 已在 GUI 线程取好）。"""
        w.trainer.set("god", states["god"])
        w.trainer.set("freeCards", states["freeCards"])

    def _revert(self, key):
        c = self._cap(key)
        c.blockSignals(True)
        c.setChecked(False, animate=True, emit=False)
        c.blockSignals(False)

    def do_launch(self):
        if not self.game_dir:
            self.flash("未找到游戏目录")
            return
        try:
            os.startfile(f"steam://rungameid/{APPID}")
            self.flash("已通过 Steam 启动游戏，进入存档后点「连接游戏」")
        except Exception:
            try:
                os.startfile(os.path.join(self.game_dir, GAME_EXE))
                self.flash("已启动游戏，进入存档后点「连接游戏」")
            except Exception as ex:
                self.flash(f"启动失败: {ex}")

    def do_attach(self):
        if self.attached:
            self.flash("已经连接")
            return

        # 开关状态必须先在 GUI 线程取好，worker 线程里不能碰控件
        states = {k: self._cap(k).isChecked() for k in ("god", "freeCards")}

        def job(w):
            w.trainer.attach()
            w.attached = True
            # 连接前可能已经在"预览模式"里开过开关，这里补齐，避免界面与游戏不一致
            self._push_all(w, states)

        self.flash("正在连接…")
        self.worker.post(job)

    def do_detach(self):
        if not self.attached:
            self.flash("当前未连接游戏")
            return

        def job(w):
            for k in ("god", "freeCards"):
                if self._cap(k).isChecked():
                    w.trainer.set(k, False)
            w.trainer.detach()
            w.attached = False

        self.worker.post(job)
        for k in ("god", "freeCards"):
            self._revert(k)
        self.flash("已断开")

    def flash(self, msg):
        self.msg.setText(msg)

    # ------------- 状态渲染 -------------
    def on_status(self, st):
        if st.get("_error"):
            self.attached = False
            self.dot.setColor(C_WARN)
            self.conn.setText("连接中断")
            self.deck_card.set_connected(False)
            self.forget_card.set_connected(False)
            self.plan_card.set_connected(False)
            self.flash(f"连接异常：{st['_error'][:60]}")
            return
        self.attached = bool(st.get("_attached"))
        pid = st.get("_pid")
        if self.attached:
            self.dot.setColor(C_OK)
            self.conn.setText("已连接")
            self.b_attach.setEnabled(False)
            self.set_val("pid", f"PID {pid}")
            if st.get("player"):
                self.set_val("hp", f"{st.get('hp')} / {st.get('maxHp')}")
                self.set_val("en", f"{st.get('energy')} / 上限 {st.get('softCap')}")
            else:
                self.set_val("hp", "等待进入游戏")
                self.set_val("en", "等待进入游戏")
            s = st.get("stats") or {}
            self.set_val("blk", f"格挡 {s.get('blocked', 0)} / 受击 {s.get('hits', 0)}")
            # 挂钩健康度：地址校验失败说明游戏已更新、RVA 漂移，功能不会生效
            hooks = st.get("hooks") or {}
            errs = s.get("hookErrors") or []
            # ⚠️ 必须**把失败的那一条点名**：只报"失败 1 项"没法排查
            #    （用户实测就是这个：状态页常年显示"失败 1 项"，看不出是谁）。
            if errs:
                self.set_val("hook", f"异常 {len(errs)} 项：{errs[0][:24]}")
                self.val["hook"].setStyleSheet(f"color: {C_WARN};")
                self.flash("游戏已更新，部分功能地址失效，请更新辅助：%s" % errs[0][:48])
            elif hooks:
                bad = [k for k, v in hooks.items() if str(v).startswith("FAILED")]
                if bad:
                    self.set_val("hook", f"失败 {len(bad)} 项：{'/'.join(bad)}")
                    self.val["hook"].setStyleSheet(f"color: {C_WARN};")
                    self.flash("挂钩失败：%s（游戏已更新？地址校验没过）" % "/".join(bad))
                else:
                    self.set_val("hook", f"正常 {len(hooks)} / {len(hooks)}")
                    self.val["hook"].setStyleSheet("")
            else:
                self.set_val("hook", "—")
                self.val["hook"].setStyleSheet("")
        else:
            self.dot.setColor(C_DOT_OFF)
            self.conn.setText("未连接")
            self.b_attach.setEnabled(True)
            self.set_val("pid", f"PID {pid}" if pid else "未运行")
            for k in ("hp", "en", "blk", "hook"):
                self.set_val(k, "—")

        # 牌库预览（纯只读）：数据由 worker 顺带取回，这里只负责渲染
        self.deck_card.set_connected(self.attached)
        self.forget_card.set_connected(self.attached)
        self.plan_card.set_connected(self.attached)
        # 打法建议：worker 只在卡片开着时才带回 `_plan`。卡片在滚动区，
        # 高度变化由它自己的 `sync_height` → `on_height` → 让位 处理，这里不用管排布。
        if "_plan" in st:
            self.plan_card.update_data(st.get("_plan") or {})
        if "_deck" in st:
            d = st.get("_deck") or {}
            self.deck_card.update_data(d)
            if DECK_DIAG:
                try:
                    with open(DECK_DIAG_PATH, "w", encoding="utf-8") as f:
                        json.dump({"attached": self.attached,
                                   "deckType": d.get("deckType"),
                                   "deckTypeName": d.get("deckTypeName"),
                                   "active": d.get("active"),
                                   "candidates": d.get("candidates"),
                                   "drawCount": d.get("drawCount"),
                                   "hand": d.get("hand"), "handSize": d.get("handSize"),
                                   "first": (d.get("cards") or d.get("draw") or [])[:6]},
                                  f, ensure_ascii=False, indent=1)
                except Exception:
                    pass

    def set_val(self, key, text):
        self.val[key].setText(text)

    def closeEvent(self, e):
        try:
            self.worker.shutdown()
            self.worker.wait(1500)
        except Exception:
            pass
        e.accept()


_SINGLETON = None


def already_running():
    """单实例守卫。

    两个面板 = 两套 frida agent 同时挂在同一批游戏函数上（agent.js 里是
    Interceptor 改写），其中一个卸载时会把钩子还原成"另一个的蹦床"，
    游戏轻则卡死重则崩 —— 实测吃过一次亏。所以第二个实例直接不让起。
    """
    global _SINGLETON
    if sys.platform != "win32":
        return False
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        _SINGLETON = k.CreateMutexW(None, False, "ShroomTrainer_Singleton")
        return ctypes.get_last_error() == 183      # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def load_theme_pref():
    """读上次选的皮肤。读不到 / 读坏了都退回浅色。"""
    try:
        from PyQt6.QtCore import QSettings
        v = QSettings(THEME_ORG, THEME_APP).value("theme", "light")
        return "dark" if str(v) == "dark" else "light"
    except Exception:
        return "light"


def save_theme_pref(name):
    """记住这次的选择。

    用 QSettings 存注册表（Windows 上就是 HKCU\\Software\\XIZI\\ShroomTrainer），
    **不往 exe 旁边写配置文件** —— 单文件打包运行时解压到临时目录，exe 所在路径
    本身不可靠，写文件反而会散落在用户没预期的位置。
    """
    try:
        from PyQt6.QtCore import QSettings
        s = QSettings(THEME_ORG, THEME_APP)
        s.setValue("theme", name)
        s.sync()
    except Exception:
        pass


def load_glow_pref():
    """读上次选的荧光模式。读不到 / 读坏了都退回彩色。"""
    try:
        from PyQt6.QtCore import QSettings
        v = str(QSettings(THEME_ORG, THEME_APP).value("glowMode", "color"))
        return v if v in GLOW_MODES else "color"
    except Exception:
        return "color"


def save_glow_pref(mode):
    """记住荧光模式。和皮肤偏好同一处（QSettings 注册表），同样不往 exe 旁边写文件。"""
    try:
        from PyQt6.QtCore import QSettings
        s = QSettings(THEME_ORG, THEME_APP)
        s.setValue("glowMode", mode)
        s.sync()
    except Exception:
        pass


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    if already_running():
        ctypes.windll.user32.MessageBoxW(
            None,
            "Shroom & Gloom 辅助面板已经在运行了。\n\n"
            "同时开两个会让两边各往游戏里挂一套钩子，可能把游戏卡死，所以这里拦住了。\n"
            "请直接用已经开着的那个窗口。",
            "Shroom & Gloom by-XIZI", 0x40)
        sys.exit(0)
    app = QApplication(sys.argv)
    # 皮肤必须在建面板**之前**定好：Panel 的第一份背景素材就是按当前调色板造出来的。
    # `SHROOM_THEME` 优先（真机冒烟用它直接起指定皮肤做像素比对，不受机器上存过的偏好影响）。
    forced = os.environ.get("SHROOM_THEME")
    use_palette(forced if forced in THEMES else load_theme_pref())
    # 荧光模式同理：必须在建面板**之前**定好 —— `GlowSegmented` 是拿当前
    # `GLOW_MODE` 当初始态的（见它的 __init__），建完再改就得额外同步一次指示灯。
    # `SHROOM_GLOW` 优先，供真机冒烟钉死模式做像素比对。
    global GLOW_MODE
    gforced = os.environ.get("SHROOM_GLOW")
    GLOW_MODE = gforced if gforced in GLOW_MODES else load_glow_pref()
    app.setStyleSheet(QSS)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    w = Panel()
    w.show()
    w.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
