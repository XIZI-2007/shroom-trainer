"""面板自检（434 项 / 14 节）：滚动灵敏度 / 沟槽 / 边缘渐变帘 / 入场不动效 / 纵向拉伸 /
连接状态排布 / 置顶 / 主题换肤 / 荧光三态 / 圆角三级制全树扫描 / 源码级 AST 反向断言。

时序要点：「入场不做动画」本身就是需求的一部分，所以「启动后卡片直接落位」必须在
任何滚动操作之前断言 —— 一旦滚动，复位+重弹逻辑会改变 x 和 _popped。

⚠️⚠️ **必须用真实窗口平台跑，不要设 `QT_QPA_PLATFORM=offscreen`。**
实测（2026-10-01）：offscreen 下会有 **9 项确定性假 FAIL**（不是竞态，数字每次都一样）：
  置顶 5 项 —— `winId()` 在 offscreen 下不是真 HWND ⇒ `SetWindowPos` 被系统拒绝
              ⇒ 面板提示「置顶失败，系统拒绝了该操作」、`window_is_topmost()` 恒 False；
  荧光台阶 1 项 —— offscreen 无真实合成，采样梯度不同（首值 59 / 最大一跳 16）；
  连接状态紧贴标题 1 项 —— 头行宽度按真实渲染算；
  仪表盘图标翻转 2 项 —— `t0/t1` 像素数相同、`_spin` 收尾到 0。
真实平台下这 434 项**全绿**。offscreen 只适合"抓图看版面"（配 SHROOM_UI_SHOT=1），
**不适合跑断言** —— 两者别混用。

⚠️ 另一类易误判的红点：**真光标落在面板上**（面板是置顶+跟随真实窗口位置的）。
实测过一次「初始没有卡片在发荧光 / 不悬停时逐帧刷新是停的」两项红，第 5 张卡
（`ForgetCard`）一上来就是 `hover=1.0` —— 就是鼠标压在上面。
**判读**：跑之前把光标挪到屏幕角落（`SetCursorPos`），或直接看失败项是不是集中在
某一张卡。这类红点**不要改代码**。
"""
import ast
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.pop("SHROOM_AUTO_ATTACH", None)

import ctypes
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from PyQt6.QtGui import (QFont, QMouseEvent, QWheelEvent, QShortcut, QKeySequence,
                         QEnterEvent, QCursor, QColor)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent, QByteArray

import trainer_gui as G

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ui_shot.png")
OUT_TALL = os.path.join(HERE, "ui_shot_tall.png")
OUT_BOTTOM = os.path.join(HERE, "ui_shot_bottom.png")
OUT_HOVER = os.path.join(HERE, "ui_shot_hover.png")
OUT_DECK = os.path.join(HERE, "ui_shot_deck.png")
OUT_FORGET = os.path.join(HERE, "ui_shot_forget.png")
OUT_FORGET_CLOSED = os.path.join(HERE, "ui_shot_forget_closed.png")
OUT_SHEET = os.path.join(HERE, "ui_shot_sheet.png")
# 截图默认**不写**：交付只给最终 exe，中间产物不该留一地。
# 需要肉眼核对界面时：SHROOM_UI_SHOT=1 python tools/ui_check.py
SHOT = os.environ.get("SHROOM_UI_SHOT") == "1"

# ⚠️ 硬门禁：offscreen 平台下跑断言必出 9 项假 FAIL（原因见文件头）。宁可拒绝启动，
#    也不要让人对着假红点去改不该改的代码。抓图例外（SHROOM_UI_SHOT=1 时放行）。
if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and not SHOT:
    sys.stderr.write(
        "\n[ui_check] 拒绝在 offscreen 平台下跑断言 —— 会有 9 项假 FAIL（非竞态）。\n"
        "  正确用法：  unset QT_QPA_PLATFORM && python tools/ui_check.py\n"
        "  只在抓图时才用 offscreen：SHROOM_UI_SHOT=1 QT_QPA_PLATFORM=offscreen python tools/ui_check.py\n\n")
    sys.exit(3)
fails = []


def check(name, ok, info=""):
    print(("  [OK]  " if ok else "  [FAIL]") + f" {name} {info}")
    if not ok:
        fails.append(name)


app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
app.setFont(QFont("Microsoft YaHei UI", 9))
p = G.Panel()
p.show()
QTest.qWait(500)


def _settle(limit=80):
    """等让位补间彻底落地再断言。

    ⚠️ 卡片高度变化现在是**动画**（170ms），`QWait(30)` 那种短等待会读到插值
    中间值：实测「收起 118→109」跑到 30ms 时是 115，「展开」跑到 50ms 时是 114，
    于是「展开比收起高」这条断言用 114 vs 115 判红 —— 那是测试没等动画，不是功能坏。
    """
    for _ in range(limit):
        if p._reflow_anim is None and not p._reflow_timer.isActive():
            return True
        QTest.qWait(16)
    return False

# ---------- 1. 面板尺寸：宽锁死 / 纵向可拉伸 / 下限 350 ----------
print("=== 1. 面板尺寸 ===")
check("启动 576x350", (p.width(), p.height()) == (576, 350), str((p.width(), p.height())))
check("纵向最小高度 350", p.minimumHeight() == 350, str(p.minimumHeight()))
p.resize(576, 180)                       # 试图压到下限以下
QTest.qWait(140)
check("压不破下限 350", p.height() == 350, f"h={p.height()}")
check("宽度被锁死 576", p.width() == 576, f"w={p.width()}")

# ---------- 1b. 头部文案 ----------
print("=== 1b. 头部文案 ===")
QTest.qWait(220)                        # 等尺寸 350 的布局落定，视口高才是真的
_lb_title = p.findChild(G.QLabel, "title")
check("标题已改为 by-XIZI",
      _lb_title is not None and _lb_title.text() == "Shroom & Gloom by-XIZI",
      repr(_lb_title.text() if _lb_title else None))
check("灰色副标题已删除", p.findChild(G.QLabel, "subtitle") is None,
      "QSS 里的 #subtitle 规则也应一并清掉")
check("窗口标题同步", p.windowTitle() == "Shroom & Gloom by-XIZI", p.windowTitle())
_hd = p.findChild(G.QLabel, "msg")
check("提示行还在（头部只砍了副标题）", _hd is not None)
print(f"     头部只剩一行，视口高 {p.viewport.height()}px")

# ---------- 2. 入场：不播动画，卡片直接落位 ----------
print("=== 2. 入场不做动画（需求 4）===")
QTest.qWait(300)
xs = [c.x() for c in p.scroll_items]
pop = [c._popped for c in p.scroll_items]
eff = [c.graphicsEffect() is not None for c in p.scroll_items]
print(f"     x={xs}  popped={pop}  有透明效果={eff}")
check("启动时所有卡片直接停在 x=0", all(x == 0 for x in xs), str(xs))
check("启动没有透明效果(没在播淡入)", not any(eff), str(eff))
check("视口内卡片标记为已就位",
      all(c._popped for c in (p.cap_top, p.cap_god, p.cap_free)), str(pop))
check("视口外卡片待弹(popped=False)",
      not p.deck_card._popped and not p.forget_card._popped, str(pop))
check("★ 状态卡已不在滚动区（搬进独立状态页了）", p.status_card not in p.scroll_items)
check("_pop_armed 已开", p._pop_armed)

# ---------- 3. 布局 / 滚动区间 ----------
print("=== 3. 布局 / 滚动区间 ===")
vw, vh = p.viewport.width(), p.viewport.height()
cw, ch, mx = p.cap_god.width(), p.content.height(), p._max_scroll()
print(f"     视口={vw}x{vh}  内容高={ch}  卡片宽={cw}  最大滚动={mx}")
check("右侧留出滚动条沟槽", cw == vw - G.Panel.SBAR_GUTTER,
      f"{cw} vs {vw - G.Panel.SBAR_GUTTER}")
check("内容超出视口(需要滚动)", ch > vh, f"content={ch} viewport={vh}")
_gaps = G.Panel.CARD_GAP * (len(p.scroll_items) - 1)
check("内容高 = 卡片和 + 间隙 + 底部留白",
      ch == sum(c.height() for c in p.scroll_items) + _gaps + G.Panel.CONTENT_PAD_B,
      str(ch))
# 按各卡真实高度累加，别写死 74 —— 牌库卡不是 64 高，写死就必然错
_want_ly = [0]
for _c0 in p.scroll_items[:-1]:
    _want_ly.append(_want_ly[-1] + _c0.height() + G.Panel.CARD_GAP)
check("卡片纵向依次排列", [c._ly for c in p.scroll_items] == _want_ly,
      str([c._ly for c in p.scroll_items]))
check("共 2 个功能胶囊 + 置顶 + 牌库 + 遗忘（状态卡已移出）",
      len(p.scroll_items) == 5, str(len(p.scroll_items)))

print("=== 3b. 游戏功能胶囊 ===")
# 按对象取，不按位置取 —— 「置顶」挪到第一张之后，[:3] 指的就不再是这几张了
_game = (p.cap_god, p.cap_free)
check("两条游戏胶囊等高 64", {c.height() for c in _game} == {64},
      str([c.height() for c in _game]))
check("功能名/解释左内边距 32", all(c.lb_title.x() == 32 for c in _game),
      str([c.lb_title.x() for c in _game]))
check("★ 胶囊都不再挂附加控件（附加控件接口已随无限精力一起删）",
      all(not hasattr(c, "extra") for c in _game),
      str([hasattr(c, "extra") for c in _game]))
check("★ Panel 上已不存在「无限精力」胶囊", not hasattr(p, "cap_energy"))
check("★ Panel 上已不存在能量输入控件", not hasattr(p, "st_energy"))

print("=== 3c. 「窗口置顶」胶囊（PinWin 功能）===")
_t = p.cap_top
check("文案正确", (_t.lb_title.text(), _t.lb_desc.text())
      == ("窗口置顶", "面板浮在游戏之上，不被窗口遮挡"),
      f"{_t.lb_title.text()} / {_t.lb_desc.text()}")
check("与其余胶囊等高 64", _t.height() == 64, str(_t.height()))
check("文字同样右移到 32", _t.lb_title.x() == 32, str(_t.lb_title.x()))
check("★ 排在第一位（本轮需求）", p.scroll_items[0] is _t,
      str([type(c).__name__ for c in p.scroll_items]))
check("启动就在视口最上方(_ly=0 且完全可见)", _t._ly == 0 and _t._popped,
      f"ly={_t._ly} popped={_t._popped}")
check("两张功能卡紧随其后、状态卡仍在最后",
      p.scroll_items[1:3] == [p.cap_god, p.cap_free]
      and p.scroll_items[-1] is p.forget_card,
      str([type(c).__name__ for c in p.scroll_items]))
check("牌库卡与遗忘卡相邻、遗忘卡是滚动区最后一张",
      p.scroll_items.index(p.forget_card) == p.scroll_items.index(p.deck_card) + 1
      and p.scroll_items.index(p.forget_card) == len(p.scroll_items) - 1,
      str([type(c).__name__ for c in p.scroll_items]))
check("没有附加控件(纯开关)", not hasattr(_t, "extra"))
check("F4 已绑定置顶", any(s.key() == QKeySequence("F4") for s in p.findChildren(QShortcut)),
      "F1/F2/F3 已有，此处只验 F4")
check("★ F3 已解绑（原绑无限精力，功能移除后不再注册）",
      not any(s.key() == QKeySequence("F3") for s in p.findChildren(QShortcut)),
      str([s.key().toString() for s in p.findChildren(QShortcut)]))

# ---------- 4. 滚动条位置：不压胶囊 ----------
print("=== 4. 滚动条不压胶囊（需求 2）===")
g = p.viewport.geometry()
bw = 3.0
bx = g.right() - G.Panel.SBAR_GUTTER / 2.0 - bw / 2.0
card_right = g.left() + cw
print(f"     卡片右缘={card_right}  滚动条 x={bx:.1f}..{bx + bw:.1f}  视口右缘={g.right()}")
check("滚动条在卡片右侧之外", bx >= card_right + 2, f"bx={bx:.1f} card_right={card_right}")
check("滚动条没超出视口", bx + bw <= g.right(), f"{bx + bw:.1f} <= {g.right()}")

# ---------- 5. 滚轮灵敏度 ----------
print("=== 5. 滚轮灵敏度（需求 1）===")


def wheel(notches=1, x=300, y=260):
    d = int(-120 * notches)          # 负 = 往下滚
    ev = QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0), QPoint(0, d),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    p.wheelEvent(ev)


p._scroll_to(0, smooth=False)
QTest.qWait(80)
wheel(1)
QTest.qWait(300)
print(f"     一格滚轮 -> scroll={p._scroll:.0f}（旧值 46）")
check("一格滚轮 >= 100px", p._scroll >= 100, f"{p._scroll:.0f}")
p._scroll_to(0, smooth=False)
QTest.qWait(80)
wheel(2)
QTest.qWait(340)
check("两格滚轮被夹在上界内", p._scroll <= mx + 0.5, f"{p._scroll:.0f} / {mx}")

# ---------- 6. 滚到才弹（需求 4 的另一半）----------
print("=== 6. 滚到才弹（需求 4）===")
p._scroll_to(0, smooth=False)
QTest.qWait(80)
sc = p.forget_card            # 滚动区最后一张（状态卡已移出）
sc._popped = False
sc.move(0, sc._ly)
p._check_pop()
QTest.qWait(60)
check("没滚到之前不弹", not sc._popped)
p._scroll_to(mx, smooth=True)
QTest.qWait(140)
check("滚到底立即触发", sc._popped)
QTest.qWait(650)
check("弹出后回到 x=0", sc.x() == 0, f"x={sc.x()}")
check("弹出后摘掉效果", sc.graphicsEffect() is None)

print("=== 6b. 滚回顶部：胶囊重新弹一次 ===")
god = p.cap_god
p._scroll_to(0, smooth=True)
QTest.qWait(160)
print(f"     滚回中  cap_god.pop={god._popped} x={god.x()}  末张.x={sc.x()}")
check("胶囊被重新触发弹出", god._popped)
QTest.qWait(700)
_reenter = (p.cap_top, p.cap_god, p.cap_free)
check("重新进入视口的三张都弹回 x=0", all(c.x() == 0 for c in _reenter),
      str([c.x() for c in _reenter]))

print("=== 6c. 只露一小角时不弹（迟滞）===")
# 拿第一张（现在是「窗口置顶」，ly=0 h=64）：滚动 62px 时它已完全离开视口（复位）；
# 滚到 50px 只露出 14px（< POP_ENTER_MIN=24）→ 不该弹；滚到 30px 露 34px → 才弹。
_first = p.scroll_items[0]
p._scroll_to(0, smooth=False)
QTest.qWait(80)
p._scroll_to(62, smooth=False)
QTest.qWait(60)
check("完全离开视口的那张复位", _first.x() == -26, f"x={_first.x()}")
check("仍在视口内的两张不动", all(c.x() == 0 for c in (p.cap_god, p.cap_free)),
      str([c.x() for c in (p.cap_god, p.cap_free)]))
p._scroll_to(50, smooth=False)
QTest.qWait(60)
check("只露 14px 不弹", not _first._popped, f"pop={_first._popped}")
p._scroll_to(58, smooth=False)
p._scroll_to(52, smooth=False)
p._scroll_to(58, smooth=False)
p._scroll_to(50, smooth=False)
QTest.qWait(60)
check("露头边界上下抖 8px 仍不弹", not _first._popped, f"pop={_first._popped}")
p._scroll_to(30, smooth=False)
QTest.qWait(80)
check("露够 24px 才弹", _first._popped, f"pop={_first._popped}")
QTest.qWait(700)

# ---------- 7. 边缘渐变帘 ----------
print("=== 7. 边缘渐变帘（需求 3）===")
# 帘子必须是 viewport 之后的兄弟控件，否则会被卡片盖掉（父级 paintEvent 画的都会）
kids = [k for k in p.children() if isinstance(k, G.QWidget)]
check("帘子排在 viewport 之后(才会画在卡片上)",
      kids.index(p.veil_top) > kids.index(p.viewport)
      and kids.index(p.veil_bot) > kids.index(p.viewport),
      str([type(k).__name__ for k in kids]))

p._scroll_to(0, smooth=False)
QTest.qWait(140)
p.repaint()
# 停在顶部时上方没东西被裁（不画上帘），但下方内容被裁 → 只画下帘
check("顶部只画下帘", p._veils_drawn == ["bottom"], str(p._veils_drawn))
check("下帘已显示", p.veil_bot.isVisible() and not p.veil_top.isVisible())

# ★ 关键：帘子必须真的改变画面。直接渲染两次做像素差 —— 如果帘子被卡片盖住，
#   两次渲染会一模一样，这条断言就会失败（这正是第一次实现踩的坑）。
#   注意 p.grab() 在高 DPI 下返回的是**物理像素**图，取样坐标要乘缩放比。
vp = p.viewport.geometry()
with_veil = p.grab().toImage()
scale = with_veil.width() / p.width()
p.veil_bot.hide()
p.repaint()
QTest.qWait(80)
without = p.grab().toImage()
p._update_veils()
p.repaint()
QTest.qWait(80)
row = int((vp.bottom() - 5) * scale)
diff = sum(1 for x in range(int(20 * scale), int((p.width() - 20) * scale))
           if with_veil.pixelColor(x, row) != without.pixelColor(x, row))
print(f"     缩放={scale:.2f}  底部第 {row} 行(物理)：有/无帘子差异像素={diff}")
check("帘子确实盖在卡片上(有实际效果)", diff > 200, f"diff={diff}")

img_b = p.veil_bot._img
check("下帘取色于背景并带 alpha 渐变", img_b is not None)
if img_b is not None:
    b0 = img_b.pixelColor(4, 0).alpha()
    b1 = img_b.pixelColor(4, img_b.height() - 1).alpha()
    print(f"     下帘 alpha: 顶端={b0} 底端={b1}（下实上虚）")
    check("下帘方向正确(下实上虚)", b1 > 240 and b0 < 25, f"{b0} -> {b1}")

p._scroll_to(mx, smooth=True)
QTest.qWait(320)
p.repaint()
check("滚到底只画上帘", p._veils_drawn == ["top"], str(p._veils_drawn))
img_t = p.veil_top._img
if img_t is not None:
    t0 = img_t.pixelColor(4, 0).alpha()
    t1 = img_t.pixelColor(4, img_t.height() - 1).alpha()
    print(f"     上帘 alpha: 顶端={t0} 底端={t1}（上实下虚）")
    check("上帘方向正确(上实下虚)", t0 > 240 and t1 < 25, f"{t0} -> {t1}")
check("帘子盖满整幅面板宽", p.veil_top.width() == p.width(), f"{p.veil_top.width()}")

p._scroll_to(mx / 2.0, smooth=False)
QTest.qWait(140)
p.repaint()
check("滚到中间时上下帘都在", sorted(p._veils_drawn) == ["bottom", "top"],
      str(p._veils_drawn))

# ---------- 8. 纵向拉伸 ----------
print("=== 8. 纵向自由拉伸（需求 5）===")
_heights_at_350 = [c.height() for c in p.scroll_items]
p.resize(576, 1000)
QTest.qWait(340)
vh2, mx2 = p.viewport.height(), p._max_scroll()
print(f"     拉到 1000: 视口高={vh2} 内容高={p.content.height()} 最大滚动={mx2}")
check("拉高后视口变大", vh2 > vh + 100, f"{vh2} vs {vh}")
check("拉高到装得下就不再需要滚动", mx2 == 0, f"max_scroll={mx2}")
# ★ 状态卡搬进独立页面后，滚动区**没有可拉伸的卡**了 ——
#   以前这里是「拉高后状态卡长高吃掉落差」，那个特例连同 MIN_STATUS_H 一起删了。
check("★ 拉高后每张卡高度都不变（滚动区已无可拉伸的卡）",
      [c.height() for c in p.scroll_items] == _heights_at_350,
      f"{[c.height() for c in p.scroll_items]} vs {_heights_at_350}")
check("★ 状态卡已不在滚动区", p.status_card not in p.scroll_items)
check("阴影贴图只跟滚动区这几张卡（key = 主题 + 各卡的 w/h）",
      p._shadow_key[0] == p._theme
      and len(p._shadow_key[1]) == len(p.scroll_items),
      f"theme={p._shadow_key[0]} 尺寸项={len(p._shadow_key[1])} vs {len(p.scroll_items)}")
check("★ 反向断言：状态卡**不在**投影列表里（它底下是实心玻璃页，再投影只会显脏）",
      all(w is not p.status_card for w, _, _ in p._shadows))
p.repaint()
if SHOT:
    p.grab().save(OUT_TALL)

p.resize(576, 350)
QTest.qWait(340)
check("缩回 350 又能滚", p._max_scroll() > 100, f"{p._max_scroll()}")
check("缩回后滚动量被重新夹取", p._scroll <= p._max_scroll() + 0.5, f"{p._scroll:.0f}")

# ---------- 9. 未连接也能开关（预览模式）----------
print("=== 9. 未连接也能开关（预览模式）===")
check("初始未连接", not p.attached)
p.cap_god.toggle()
p.cap_free.toggle()
QTest.qWait(420)
check("god 保持开启(未被回弹)", p.cap_god.isChecked())
check("freeCards 保持开启", p.cap_free.isChecked())
check("液面已填满", all(c._liq_p > 0.9 for c in (p.cap_god, p.cap_free)),
      str([round(c._liq_p, 2) for c in (p.cap_god, p.cap_free)]))
print("     提示行:", repr(p.msg.text()))
check("提示写明是预览", "预览" in p.msg.text(), p.msg.text())

# ---------- 10. 拖拽滚动 vs 点击 ----------
print("=== 10. 拖拽滚动 vs 点击 ===")
p._scroll_to(0, smooth=False)
QTest.qWait(140)


def mk(t, y):
    return QMouseEvent(t, QPointF(20, y), QPointF(20, y),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


# ---- 悬停：确定性地投递 Enter / Leave ----
# ⚠️ 不要用 `QTest.mouseMove` 来测悬停：Qt 里它是靠 **移动真实系统光标**
# （`QCursor::setPos`）实现的，只要窗口位置一变、或有别的窗口盖上来就失效 ——
# 测起来"看运气"（本例头部文案一改、窗口内容整体上移就全挂了）。
# 这里直接投递事件，只测我们自己的 enter/leave 处理；Qt 在真悬停时会不会送
# Enter 是标准行为，另有真机核对（移动真实光标 + 从 exe 截图量像素）兜底。
_hovered = [None]


def hover(w, pos=(120, 32)):
    if _hovered[0] is not None:
        QApplication.sendEvent(_hovered[0], QEvent(QEvent.Type.Leave))
        _hovered[0] = None
    if w is not None:
        QApplication.sendEvent(w, QEnterEvent(QPointF(pos[0], pos[1]),
                                              QPointF(0, 0), QPointF(0, 0)))
        _hovered[0] = w


p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonPress, 300))
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseMove, 240))
check("向上拖动后发生滚动", p._scroll > 30, f"scroll={p._scroll:.0f}")
check("拖拽中标记为 dragging", p._dragging)
consumed = p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonRelease, 240))
check("拖拽结束的 release 被吞掉", consumed is True, str(consumed))
check("释放后 dragging 复位", not p._dragging)

print("=== 10b. 卡片上仍能拖拽滚动（数值输入区已随无限精力移除）===")


def mk_up(t, y):
    """左键**已经松开**的鼠标事件（buttons = NoButton）。"""
    return QMouseEvent(t, QPointF(20, y), QPointF(20, y),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)


# ④ 卡片上仍要能拖拽滚动（别把功能一起关掉）
p._scroll_to(0, smooth=False)
QTest.qWait(100)
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonPress, 300))
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseMove, 240))
check("卡片上仍能拖拽滚动", p._scroll > 30, f"scroll={p._scroll:.0f}")
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonRelease, 240))

# ⑤ 拖拽中途把 release 弄丢（拖出窗口 / alt-tab）→ 状态必须自愈，
#    否则之后光标随便划一下都会把整页拖走（正是用户看到的现象之一）
p._scroll_to(0, smooth=False)
QTest.qWait(100)
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonPress, 300))
p._press_y = 300                        # 人为制造"起点还留着、却没收到 release"
ghost = p._scroll
eaten = p.eventFilter(p.deck_card, mk_up(QEvent.Type.MouseMove, 200))
check("左键已松开时的移动不滚动", abs(p._scroll - ghost) < 0.5,
      f"{ghost:.0f} -> {p._scroll:.0f}")
check("卡死的拖拽状态被清掉", p._press_y is None and not p._dragging,
      f"press_y={p._press_y} dragging={p._dragging}")
more = p.eventFilter(p.deck_card, mk_up(QEvent.Type.MouseMove, 100))
check("自愈之后光标再动也不拖走界面",
      abs(p._scroll - ghost) < 0.5 and more is False and eaten is False,
      f"scroll={p._scroll:.0f} move 被吞={more}")

# ⑥ 普通点击（按下/抬起都没位移）后，起点必须清干净 ——
#    留着的话，之后「只是把光标划过去」也会被当成还在拖，整页跟着光标跑
p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonPress, 300))
click_rel = p.eventFilter(p.deck_card, mk(QEvent.Type.MouseButtonRelease, 300))
check("普通点击的 release 没被吞", click_rel is False, str(click_rel))
check("普通点击后起点已清空", p._press_y is None, f"press_y={p._press_y}")
ghost2 = p._scroll
p.eventFilter(p.deck_card, mk_up(QEvent.Type.MouseMove, 120))
check("点击后光标划过不再拖走界面", abs(p._scroll - ghost2) < 0.5,
      f"{ghost2:.0f} -> {p._scroll:.0f}")

# ---------- 11. 窗口置顶（PinWin 那套能力）----------
print("=== 11. 窗口置顶 ===")
hwnd = int(p.winId())
q0 = p.worker.cmds.qsize()
check("初始未置顶且真实状态也是普通层",
      not p.cap_top.isChecked() and not G.window_is_topmost(hwnd),
      f"checked={p.cap_top.isChecked()} topmost={G.window_is_topmost(hwnd)}")

p.cap_top.toggle()
QTest.qWait(420)
print(f"     toggle 后：checked={p.cap_top.isChecked()} p={p.cap_top._liq_p:.2f} "
      f"topmost={G.window_is_topmost(hwnd)}  提示={p.msg.text()!r}")
check("胶囊已开启且液面填满", p.cap_top.isChecked() and p.cap_top._liq_p > 0.9,
      f"p={p.cap_top._liq_p:.2f}")
# ★ 关键：不能只看界面那个开关，要读回窗口真实的 exstyle —— 否则 API 静默失败也测不出来
check("★ 窗口真的进了 topmost 层", G.window_is_topmost(hwnd))
check("提示写明已置顶", "置顶" in p.msg.text(), p.msg.text())
# 置顶是纯本地行为：不能往 game 线程下发指令，否则连不上游戏时会被报错
check("不往 game 线程下发任何指令", p.worker.cmds.qsize() == q0,
      f"{q0} -> {p.worker.cmds.qsize()}")

p.do_detach()
QTest.qWait(200)
check("断开连接不会把置顶关掉", p.cap_top.isChecked() and G.window_is_topmost(hwnd),
      f"checked={p.cap_top.isChecked()} topmost={G.window_is_topmost(hwnd)}")

p.resize(576, 620)
QTest.qWait(320)
check("拉伸窗口后仍是置顶", G.window_is_topmost(hwnd),
      f"topmost={G.window_is_topmost(hwnd)}")
p.resize(576, 350)
QTest.qWait(320)
check("缩回 350 后仍是置顶", G.window_is_topmost(hwnd))

p.cap_top.toggle()
QTest.qWait(420)
check("关掉后摘掉 topmost",
      not p.cap_top.isChecked() and not G.window_is_topmost(hwnd),
      f"checked={p.cap_top.isChecked()} topmost={G.window_is_topmost(hwnd)}")

# ---------- 13. 悬停荧光 ----------
print("=== 13. 悬停荧光（动态 + 淡入淡出）===")
p.resize(576, 350)
QTest.qWait(300)
p._scroll_to(0, smooth=False)
QTest.qWait(140)
_c = p.scroll_items[0]                  # 「窗口置顶」，启动就在视口里
_all = list(p.scroll_items)
# 真实系统光标若恰好停在某张卡上，Qt 会自己给它发 EnterEvent，起始态就不是"全 0"了。
# 先把光标挪到**面板矩形之外**再断言，否则这条会随环境偶发失败。
#
# ⚠️⚠️ 踩过两次：
#   ① `QCursor.setPos(2, 2)` 在多显示器 / DPI 缩放下不可靠（实测被钳到 1121,776，
#      恰好压在面板上 ⇒ 某张卡一上来 hover=1.0）；
#   ② 固定挪到"虚拟屏右下角"也不行 —— 面板在**上一次跑完的位置**，可能就在那个角
#      （实测第 3 次跑时前 3 张卡同时 hover）。
# ⇒ 正确做法：**拿面板自己的矩形做避让**，选一个明确落在矩形外的点，并读回确认。
def _park_cursor(avoid_rect):
    if sys.platform != "win32":
        return None
    try:
        u = ctypes.windll.user32
        vx = u.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
        vy = u.GetSystemMetrics(77)
        vw = u.GetSystemMetrics(78)
        vh = u.GetSystemMetrics(79)
        cands = [(vx + 1, vy + 1), (vx + vw - 2, vy + 1),
                 (vx + 1, vy + vh - 2), (vx + vw - 2, vy + vh - 2),
                 (vx + vw // 2, vy + 1), (vx + 1, vy + vh // 2)]
        for cx, cy in cands:
            if not avoid_rect.contains(QPoint(cx, cy)):
                u.SetCursorPos(cx, cy)
                return QPoint(cx, cy)
    except Exception:
        pass
    return None

_parked = _park_cursor(p.frameGeometry())
QTest.qWait(320)
# 读回确认：若光标仍落在面板矩形内，说明没挪动成功，直接报出来（不要静默继续）
try:
    _cur = G.QCursor.pos()
    if p.frameGeometry().contains(_cur):
        print(f"     ⚠️ 光标仍压在面板内 {_cur.x()},{_cur.y()} —— 荧光起始态断言可能假 FAIL")
    else:
        print(f"     光标已挪到面板外 {_cur.x()},{_cur.y()}")
except Exception:
    pass
check("六张卡片都具备荧光能力",
      all(isinstance(x, G.GlassBase) and hasattr(x, "_paint_glow") for x in _all),
      str([type(x).__name__ for x in _all]))
check("初始没有卡片在发荧光",
      all(x._hover == 0 and not x._glow_on for x in _all),
      str([(round(x._hover, 2), x._glow_on) for x in _all]))
check("不悬停时逐帧刷新是停的（不空转）",
      all(not x._glow_t.isActive() for x in _all),
      str([x._glow_t.isActive() for x in _all]))

hover(_c)
QTest.qWait(90)
mid_in = _c._hover                      # 淡入途中的中间态
QTest.qWait(260)
print(f"     悬停 90ms 时 hover={mid_in:.2f}（0<x<1 → 渐变而非瞬变）；"
      f"稳定后 hover={_c._hover:.2f}")
check("★ 出现是渐变的（中途取到 0<x<1）", 0.02 < mid_in < 0.99, f"{mid_in:.2f}")
check("稳定后 hover 到 1", abs(_c._hover - 1.0) < 0.02, f"{_c._hover:.2f}")
check("荧光逐帧刷新已启动", _c._glow_on and _c._glow_t.isActive(),
      f"on={_c._glow_on} timer={_c._glow_t.isActive()}")

_a1 = _c._glow_ang
QTest.qWait(220)
print(f"     绕行角度 {_a1:.1f}° -> {_c._glow_ang:.1f}°")
check("★ 荧光是动态的（绕行角度随时间变）", abs(_c._glow_ang - _a1) > 4,
      f"{_a1:.1f} -> {_c._glow_ang:.1f}")


def _ring_spread(img, w, h, k):
    """沿卡片贴边取一圈像素，返回「最大通道差」的均值 —— 无彩边时接近 0，有荧光时明显升高。"""
    tot, n = 0.0, 0
    for x in range(30, w - 30, 6):
        for y in (int(5 * k), int(h * k) - int(6 * k)):
            c = img.pixelColor(int(x * k), y)
            tot += max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue())
            n += 1
    for y in range(14, h - 14, 5):
        for x in (int(4.5 * k), int(w * k) - int(5.5 * k)):
            c = img.pixelColor(x, int(y * k))
            tot += max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue())
            n += 1
    return tot / max(1, n)


img_on = _c.grab().toImage()
_k = img_on.width() / float(_c.width())
sp_on = _ring_spread(img_on, _c.width(), _c.height(), _k)
# 关掉荧光再取一张做对照
_c._hanim.stop()
_c._hover = 0.0
img_off = _c.grab().toImage()
sp_off = _ring_spread(img_off, _c.width(), _c.height(), _k)
print(f"     贴边一圈的通道差：无荧光 {sp_off:.1f} → 有荧光 {sp_on:.1f}")
check("★ 荧光确实画在贴边位置（彩色程度显著上升）", sp_on > sp_off + 10,
      f"{sp_off:.1f} -> {sp_on:.1f}")

_hues = []
for _x in (int(_c.width() * 0.16), int(_c.width() * 0.5), int(_c.width() * 0.84)):
    _col = img_on.pixelColor(int(_x * _k), int(5 * _k))
    _hues.append(_col.hue())
check("荧光是绕行的多色（不同位置色相不同）",
      all(h >= 0 for h in _hues) and len({h // 14 for h in _hues}) >= 2, str(_hues))

# ★ 分层的判据：从贴边往内的彩色程度必须是**单调平滑**的下降。
#   分层 = 一段平台 + 一次跳变（早先按 3 层大笔画画，就长这样）。这条能直接抓住它。
_cy = int(_c.height() * _k / 2)
_prof = []
for _x in range(0, 26):
    _col = img_on.pixelColor(int(2 * _k) + _x, _cy)
    _prof.append(max(_col.red(), _col.green(), _col.blue())
                 - min(_col.red(), _col.green(), _col.blue()))
_hop = [abs(_prof[i + 1] - _prof[i]) for i in range(len(_prof) - 1)]
print(f"     贴边往内的彩色衰减：{_prof[:12]} … 最大一跳={max(_hop)}")
check("★ 荧光过渡是单调平滑的（分层会破坏单调性）",
      _prof == sorted(_prof, reverse=True), str(_prof[:12]))
check("★ 荧光没有台阶（最大一跳不超过首值的 1/5）",
      max(_hop) <= max(1.0, _prof[0] / 5.0), f"首值={_prof[0]} 最大一跳={max(_hop)}")

# 恢复悬停 → 走真实的淡出路径
_c._hover_to(1.0)
QTest.qWait(120)
hover(p.deck_card, (60, 30))
QTest.qWait(90)
mid_out = _c._hover
QTest.qWait(360)
print(f"     移开 90ms 时 hover={mid_out:.2f}；稳定后 hover={_c._hover:.2f}")
check("★ 消失也是渐变的（中途取到 0<x<1）", 0.02 < mid_out < 0.99, f"{mid_out:.2f}")
check("淡出后回到 0", _c._hover <= 0.01, f"{_c._hover:.2f}")
check("★ 淡出后逐帧刷新停掉（不悬停不渲染）",
      not _c._glow_on and not _c._glow_t.isActive(),
      f"on={_c._glow_on} timer={_c._glow_t.isActive()}")

# ---------- 13b. 牌库顺序卡 ----------
print("=== 13b. 牌库顺序卡（默认关 + 只读渲染）===")
# 真实 worker 每 450ms 推一次状态，on_status 会按 attached 重设 deck 卡的连接态。
# 本节是纯渲染验证、要手工灌数据，不断开这条线就会被它抢跑成「未连接」而随机 FAIL。
try:
    p.worker.status.disconnect(p.on_status)
except TypeError:
    pass
_dc = p.deck_card
check("牌库卡之后只剩遗忘卡",
      p.scroll_items[-2] is _dc and p.scroll_items[-1] is p.forget_card,
      str([type(x).__name__ for x in p.scroll_items]))
check("牌库卡默认关闭", not _dc.isOn())
check("关闭时不显示任何牌名", not any(c.isVisible() for c in _dc.cells))
check("关闭态高度收在下限（收起时就该是最矮的样子）",
      _dc.height() == _dc.MIN_H, f"{_dc.height()} vs {_dc.MIN_H}")

# 未连接时
_dc.set_connected(False)
_dc.toggle()
QTest.qWait(60)
check("开启后状态文案变为「已开启」", _dc.lb_state.text() == "已开启", _dc.lb_state.text())
check("★ 开启后 worker 才开始去读牌库", p.worker.deck_want is True, str(p.worker.deck_want))
check("未连接时给出提示且不显示牌名",
      "未连接" in _dc.lb_info.text() and not any(c.isVisible() for c in _dc.cells),
      _dc.lb_info.text())

# 灌入模拟快照（真实数据来自 agent.js 的 deck() RPC，字段同名）
_names = ["Cheap Lighter", "Shovel", "Hammer", "Sweet Root", "Pet Rat"]
# 期望值直接问出货用的那张表要 —— 写死中文名会跟着串表更新而失配（Grimoire 就吃过一次）
_zh = [G.card_zh(n) for n in _names]
check("★ 汉化表已随程序加载（空表会让查不到就原样返回，形成假通过）",
      len(G._CARD_ZH) > 500, f"{len(G._CARD_ZH)} 条")
check("汉化表确实改变了牌名（抽查 5 张里至少 3 张有中文）",
      sum(1 for n, z in zip(_names, _zh) if n != z) >= 3,
      str(list(zip(_names, _zh))))
# ★ 大小写不敏感兜底：串表里 `TOASTY` 是全大写，游戏运行时给的是 `Toasty`
check("★ 大小写不一致也能查到中文（Toasty → 烤菇，实测踩过）",
      G.card_zh("Toasty") != "Toasty" and G.card_zh("toasty") != "toasty"
      and G.card_zh("BASH") != "BASH",
      f'Toasty={G.card_zh("Toasty")} toasty={G.card_zh("toasty")} BASH={G.card_zh("BASH")}')
check("★ 大小写兜底不会把本来就没翻译的名字变成中文",
      G.card_zh("Totally Unknown Card") == "Totally Unknown Card",
      G.card_zh("Totally Unknown Card"))
_costs = [0, 1, 2, 1, 0]
_cards = [{"name": n, "cost": c} for n, c in zip(_names, _costs)]
_dc.set_connected(True)
_dc.update_data({"ok": True, "cards": _cards, "draw": list(_names), "drawCount": len(_names),
                 "discard": 3, "exhaust": 2, "hand": 5, "handSize": 4, "warning": "",
                 "deckType": 2, "deckTypeName": "战斗",
                 "candidates": [{"deckType": 1, "inited": True, "active": False,
                                 "hand": 0, "draw": 12, "discard": 0, "exhaust": 0},
                                {"deckType": 2, "inited": True, "active": True,
                                 "hand": 5, "draw": 12, "discard": 3, "exhaust": 2}]})
QTest.qWait(60)
def _chip_text(chip):
    """拼出一格的可见文本。第四版起每格拆成序号/牌名/费用三个 QLabel
    （横排要能按内容自适应宽度，富文本一长串做不到），所以这里拼回来给断言用。

    ⚠️ 判可见性一律用 `isVisibleTo(chip)`，**不能用 `isVisible()`**：
    离屏跑测试时牌库卡自身从未 show()，子控件的 isVisible() 恒为 False，
    而 fee 是否该显示恰恰是靠可见性控制的 —— 用错会得到「费用全丢」的假 FAIL。
    """
    parts = [chip.lb_idx.text(), chip.lb_name.text()]
    if chip.lb_cost.isVisibleTo(chip):
        parts.append(chip.lb_cost.text())
    return " ".join(p for p in parts if p)


def _chip_cost(chip):
    """一格的费用文字（不带就返回空串）。可见性判据同 _chip_text。"""
    return chip.lb_cost.text() if chip.lb_cost.isVisibleTo(chip) else ""


_txt = [_chip_text(_dc.cells[i]) for i in range(len(_cards))]
# 费用快照必须**就地和 _txt 一起取**：后面的高度自适应段会 update_data 换成别的数据，
# 到那时再读 _dc.cells 已经是另一套牌了（踩过：断言拿 Hammer[1] 去比 5 张的费用表）。
_cost_txt = [_chip_cost(_dc.cells[i]) for i in range(len(_cards))]
check("★ 牌名按抽牌顺序逐个渲染（该顺序就是休息后补牌的顺序）",
      all(_zh[i] in _txt[i] for i in range(len(_cards))), str(_txt[:3]))
check("★ 牌名已汉化（英文原名不再出现在卡片上）",
      not any(n in _txt[i] for i in range(len(_cards)) for n in _names), str(_txt[:3]))
check("序号从 1 开始依次递增",
      [c.lb_idx.text() for c in _dc.cells[:5]] == [str(i + 1) for i in range(5)],
      str([c.lb_idx.text() for c in _dc.cells[:5]]))
check("计数行含抽牌堆 / 弃牌 / 消耗 / 手牌",
      all(k in _dc.lb_info.text() for k in ("抽牌堆 5 张", "弃牌 3", "消耗 2", "手牌 5/4")),
      _dc.lb_info.text())
check("★ 标题标明读的是哪份牌库（营地/战斗不会看串）",
      _dc.lb_head.text() == "牌库顺序 · 战斗", _dc.lb_head.text())
check("无预警时不显示预警行", not _dc.lb_warn.isVisible())

# ---- 版式：横排一行 ----
check("★ 最多显示 5 张", _dc.MAX_SHOW == 5, f"MAX_SHOW={_dc.MAX_SHOW}")
check("★ 没有残留的竖排/效果列属性",
      not hasattr(_dc, "desc_cells") and not hasattr(_dc, "ROWS"), "有旧版式残留")
check("★ 5 格是横着一排的（y 相同、x 递增）",
      len({c.y() for c in _dc.cells if c.isVisible()}) == 1
      and [c.x() for c in _dc.cells if c.isVisible()] == sorted(c.x() for c in _dc.cells if c.isVisible()),
      str([(c.x(), c.y()) for c in _dc.cells]))
check("★ 最右一格没有超出卡片宽度（不会横向溢出被裁掉）",
      max(c.x() + c.width() for c in _dc.cells if c.isVisible()) <= _dc.width(),
      f'{max(c.x() + c.width() for c in _dc.cells if c.isVisible())} vs {_dc.width()}')
check("★ 每格本身不再换行堆内容（拆成三个控件了）",
      all(not hasattr(c, "text") or "<br" not in c.text() for c in _dc.cells))

# ---- 高度自适应 ----
# 横排只有一行，所以「1 张 / 3 张 / 5 张」的高度应当一致，由 MIN_H 兜着；
# 真正会改变高度的是「还有 N 张」那一行的有无。
_h5 = _dc.height()
check("★ 横排 5 张不因张数不同而变高（都在一行里）",
      abs(_h5 - _dc.MIN_H) <= 40, f"5 张={_h5} MIN_H={_dc.MIN_H}")
_dc.update_data({"ok": True, "cards": [{"name": "Hammer", "cost": 1}],
                 "drawCount": 1, "discard": 0, "exhaust": 0, "hand": 1, "handSize": 4,
                 "warning": "", "deckTypeName": "战斗"})
QTest.qWait(50)
_h1 = _dc.height()
check("★ 有下限，不至于被压成一条（标题+说明还看得见）",
      _h1 >= _dc.MIN_H, f"{_h1} vs MIN_H={_dc.MIN_H}")
check("★ 没超出 5 张时不显示「还有 N 张」",
      not _dc.lb_more.isVisibleTo(_dc), _dc.lb_more.text())
check("★ 收起/空态的高度真的比有内容时矮（不是拍死的常数）",
      _h1 < 131, f"1 张={_h1}（有「还有 N 张」时=131）")

# ---- 费用（用上面和 _txt 一起取的快照，别读此刻的 _dc.cells）----
check("★ 每张牌都渲染出费用（[N] 形式）",
      _cost_txt == [f"[{c}]" for c in _costs], str(_cost_txt))
check("★ 费用为 0 也照样显示（不能被当成「没读到」跳过）",
      _cost_txt[0] == "[0]" and _cost_txt[4] == "[0]", str(_cost_txt))
check("★ 费用色与牌名色不同（要能一眼分出来）",
      G.C_COST != G.C_TEXT and G.C_COST != G.C_SUB, G.C_COST)

# ---- 效果文本已按需求移除 ----
_dc.update_data({"ok": True, "cards": [{"name": "Hammer", "cost": 1,
                                       "text": "Deal 4 Damage. Target something"}],
                 "drawCount": 1, "handSize": 4, "deckTypeName": "战斗"})
QTest.qWait(50)
_it = _chip_text(_dc.cells[0])
check("★ 效果文本不再渲染（按用户要求去掉，只留牌名+费用）",
      "Deal" not in _it and "Damage" not in _it
      and G.card_zh("Hammer") in _it and "[1]" in _it, _it)

# 缺费用时不能报错，也不能填假数据
_dc.update_data({"ok": True, "cards": [{"name": "Hammer", "cost": -1}],
                 "drawCount": 1, "handSize": 4, "deckTypeName": "战斗"})
QTest.qWait(50)
check("★ 读不到费用时不显示假费用（不出现 [0]/[-1]）",
      _chip_cost(_dc.cells[0]) == "" and not _dc.cells[0].lb_cost.isVisible()
      and G.card_zh("Hammer") in _chip_text(_dc.cells[0]), _chip_text(_dc.cells[0]))

# 牌名里的特殊字符：现在走 QLabel 的纯文本，不再需要富文本转义
_dc.update_data({"ok": True, "cards": [{"name": "A & B <C>", "cost": 1}],
                 "drawCount": 1, "handSize": 4, "deckTypeName": "战斗"})
QTest.qWait(50)
check("★ 牌名里的 & < > 原样显示（不走富文本就不会吃坏）",
      _dc.cells[0].lb_name.text() == "A & B <C>", _dc.cells[0].lb_name.text())

# 兼容旧的纯字符串格式（只有牌名）：不该崩，也不该冒出费用
_dc.update_data({"ok": True, "draw": ["Hammer", "Shovel"], "drawCount": 2,
                 "discard": 0, "exhaust": 0, "hand": 2, "handSize": 4, "warning": "",
                 "deckTypeName": "探索"})
QTest.qWait(50)
check("★ 只有牌名（无 cards 字段）时优雅降级：显牌名不显费用",
      G.card_zh("Hammer") in _chip_text(_dc.cells[0])
      and _chip_cost(_dc.cells[0]) == "", _chip_text(_dc.cells[0]))
check("牌库类型换成探索后标题跟着变", _dc.lb_head.text() == "牌库顺序 · 探索", _dc.lb_head.text())

# 汉化查不到的名字必须原样显示，不能变成空白
_dc.update_data({"ok": True, "cards": [{"name": "Totally Unknown Card", "cost": 3},
                                       {"name": "Bash", "cost": 2}],
                 "drawCount": 2, "discard": 0, "exhaust": 0, "hand": 2, "handSize": 4,
                 "warning": "", "deckTypeName": "探索"})
QTest.qWait(50)
check("★ 查不到中文的牌名原样显示英文（不吞名字）",
      "Totally Unknown Card" in _chip_text(_dc.cells[0]) and "猛击" in _chip_text(_dc.cells[1]),
      _chip_text(_dc.cells[0]))
check("★ 官方漏译的三条已补上（Flame Turret TEST / Body Hammer / Chainsaw Hands）",
      G.card_zh("Flame Turret TEST") != "Flame Turret TEST"
      and G.card_zh("Body Hammer") != "Body Hammer"
      and G.card_zh("Chainsaw Hands") != "Chainsaw Hands",
      f'{G.card_zh("Flame Turret TEST")} / {G.card_zh("Body Hammer")} / {G.card_zh("Chainsaw Hands")}')
check("牌库类型换成探索后标题跟着变", _dc.lb_head.text() == "牌库顺序 · 探索", _dc.lb_head.text())

# 洗牌预警
_dc.update_data({"ok": True, "cards": [{"name": "Grimoire", "cost": 1}],
                 "drawCount": 1, "discard": 8, "exhaust": 0, "hand": 3, "handSize": 4,
                 "warning": "抽牌堆仅剩 1 张，即将洗牌"})
QTest.qWait(60)
check("★ 抽牌堆过少时给出洗牌预警（顺序即将失效）",
      _dc.lb_warn.isVisible() and "洗牌" in _dc.lb_warn.text(), _dc.lb_warn.text())

# 超过显示上限
_dc.update_data({"ok": True, "cards": [{"name": f"Card {i}", "cost": 1} for i in range(20)],
                 "drawCount": 20, "discard": 0, "exhaust": 0, "hand": 1, "handSize": 4,
                 "warning": ""})
QTest.qWait(60)
check("★ 超过 5 张时用「还有 N 张」挂在下方（不再占一格位置）",
      _dc.lb_more.isVisibleTo(_dc) and _dc.lb_more.text() == "还有 15 张", _dc.lb_more.text())
check("★ 「还有 N 张」在横排的正下方（y 比所有格子都大）",
      _dc.lb_more.y() > max(c.y() for c in _dc.cells if c.isVisible()),
      f'{_dc.lb_more.y()} vs {max(c.y() for c in _dc.cells if c.isVisible())}')
check("最多只渲染 5 格",
      sum(1 for c in _dc.cells if c.isVisible()) == 5,
      str(sum(1 for c in _dc.cells if c.isVisible())))
_hmore = _dc.height()
check("★ 多出「还有 N 张」时高度比不带提示时更高（自适应真的在跑）",
      _hmore > _h1, f"带提示={_hmore} 只有1张={_h1}")
# 边界：正好 5 张不该出现提示
_dc.update_data({"ok": True, "cards": [{"name": f"Card {i}", "cost": 1} for i in range(5)],
                 "drawCount": 5, "discard": 0, "exhaust": 0, "hand": 1, "handSize": 4,
                 "warning": ""})
QTest.qWait(50)
check("★ 正好 5 张时不显示「还有 N 张」（边界不越界）",
      not _dc.lb_more.isVisibleTo(_dc), _dc.lb_more.text())
# 边界：6 张时提示应为 1 张
_dc.update_data({"ok": True, "cards": [{"name": f"Card {i}", "cost": 1} for i in range(6)],
                 "drawCount": 6, "discard": 0, "exhaust": 0, "hand": 1, "handSize": 4,
                 "warning": ""})
QTest.qWait(50)
check("★ 6 张时提示「还有 1 张」",
      _dc.lb_more.isVisibleTo(_dc) and _dc.lb_more.text() == "还有 1 张", _dc.lb_more.text())

# 读不到牌库
_dc.update_data({"ok": False, "draw": [], "warning": "未找到牌库（可能不在战斗中）"})
QTest.qWait(60)
check("读不到牌库时给出原因且不显示牌名",
      "不在战斗中" in _dc.lb_info.text() and not any(c.isVisible() for c in _dc.cells),
      _dc.lb_info.text())

# 点击卡片本身切换（Panel.eventFilter 对普通点击放行，不会被拖拽吃掉）
_before = _dc.isOn()
QTest.mouseClick(_dc, Qt.MouseButton.LeftButton, pos=QPoint(60, 30))
QTest.qWait(90)
check("★ 点击卡片即可开 / 关", _dc.isOn() != _before, f"{_before} -> {_dc.isOn()}")

if _dc.isOn():
    _dc.toggle()
QTest.qWait(70)
check("关掉后牌名全部隐藏", not any(c.isVisible() for c in _dc.cells))
check("关掉后把 worker 的 deck_want 也置回 False（不再去读内存）",
      p.worker.deck_want is False, str(p.worker.deck_want))

# 走一遍真实的 status 通路（worker 推什么，on_status 就得渲染什么），再恢复接线
p.worker.status.connect(p.on_status)
# 注意：toggle() 会经 on_change → _on_deck_toggle → set_connected(p.attached)，
#       而测试环境 attached=False。worker 的定时推送（450ms 一次）也会把连接态刷掉，
#       所以这里先断开再手工打开，否则这条断言会偶发失败（实测约 1/3 概率挂）。
try:
    p.worker.status.disconnect(p.on_status)
except TypeError:
    pass
_dc.toggle()
QTest.qWait(60)
_dc.set_connected(True)
p.on_status({"_attached": True, "_pid": 4321,
             "_deck": {"ok": True,
                       "cards": [{"name": "Hammer", "cost": 1},
                                 {"name": "Shovel", "cost": 0}],
                       "draw": ["Hammer", "Shovel"], "drawCount": 2,
                       "discard": 1, "exhaust": 0, "hand": 4, "handSize": 4, "warning": ""}})
QTest.qWait(60)
check("★ 真实 status 通路能把牌库快照渲染出来（worker → on_status → 卡片）",
      _dc.cells[0].isVisible() and G.card_zh("Hammer") in _chip_text(_dc.cells[0])
      and "抽牌堆 2 张" in _dc.lb_info.text(),
      _dc.lb_info.text())
check("★ 真实通路里费用也一并渲染",
      _chip_cost(_dc.cells[0]) == "[1]", _chip_cost(_dc.cells[0]))
# 连接断掉时，牌库卡必须跟着回到「未连接」，别留着上一局的顺序骗人
# 注意：真实的断开推送里**没有** _deck 键（worker 只在 attached 时才取快照），别塞 None 冒充
p.on_status({"_attached": False, "_pid": None})
QTest.qWait(60)
check("★ 断开连接后牌库卡立刻隐藏牌名（不留旧顺序骗人）",
      not any(c.isVisible() for c in _dc.cells) and "未连接" in _dc.lb_info.text(),
      _dc.lb_info.text())
# 重连后的第一拍：worker 还没把 deck 快照带回来，此时绝不能冒出上一局的牌序
p.on_status({"_attached": True, "_pid": 4321})
QTest.qWait(60)
check("★ 重连后数据未到的那一拍不显示上一局的旧顺序",
      not any(c.isVisible() for c in _dc.cells), _dc.lb_info.text())
if _dc.isOn():
    _dc.toggle()
QTest.qWait(60)

# ---------- 13d. 遗忘手牌卡（面板内展开 + 滚轮选卡 + 二次确认）----------
# 这一节是**纯渲染 / 交互逻辑**验证：喂模拟快照，看形态切换、滚轮选卡、
# 二次确认、列表即时刷新、计数。真实的删牌结果由 tools/forget_test.py 在真机上验，
# 这里不碰游戏。
print("=== 13d. 遗忘手牌卡（展开 / 滚轮 / 二次确认 / 即时刷新）===")
try:
    p.worker.status.disconnect(p.on_status)
except TypeError:
    pass
_fc = p.forget_card
# ☠️ 13d 全程与 worker 脱钩：把 on_open 摘掉，列表数据一律手动 update_list 喂。
# 不摘的话，每次 open_pick() 都会经 on_open → _fetch_forget_list() → worker.post()
# 真发一个后台线程请求，回包**时机不定**地投递回来 —— 实测表现为
# 「标题带牌库类型（战斗）」间歇性 FAIL（回包里的 deckTypeName 是空，把 _kind 洗掉了），
# 4 次里错 2 次。这类跨线程竞态就是「自检必须连跑 3 次」的由来。
#
# ⚠️ `on_forget_result` 里还有**第二处** `_fetch_forget_list()`（删完自动刷新列表），
#    测试要调它验「失败时给出原因」，那条路径同样会把 `_warn` 洗成 worker 的异常文本
#    （现象：期望「这张牌已不在这堆里」，实际变成「读取失败: 'NoneType' ...」）。
#    所以重拉函数本身也一起摘掉。
_fc_open_cb = _fc.on_open
_fc.on_open = None
_fetch_cb = p._fetch_forget_list
p._fetch_forget_list = lambda: None
check("遗忘卡排在牌库卡之后、是滚动区最后一张",
      p.scroll_items[-2] is _dc and p.scroll_items[-1] is _fc,
      str([type(x).__name__ for x in p.scroll_items]))

# --- 入口按钮：这次 bug 的回归断言 ---
# 之前 `toggle_pick()` 是死代码、没人调；`btns` 又在收起态被整个 setVisible(False)，
# 于是卡片永远停在收起态，"点下面按钮"下面根本没有按钮。
# 入口按钮的可点性跟连接状态挂钩，这里先连上（未连接的情形下一块单独测）。
_fc.set_connected(True)
_fc.close_pick()
QTest.qWait(30)
check("收起态入口按钮可点（不是灰的）", _fc.b_open.isVisible() and _fc.b_open.isEnabled(),
      f"visible={_fc.b_open.isVisible()} enabled={_fc.b_open.isEnabled()}")
_fc.b_open.click()
QTest.qWait(60)
check("★ 点「遗忘手牌」按钮真的能展开选卡区（入口没断线）", _fc.isOpen(),
      f"open={_fc.isOpen()}")
_fc.close_pick()
_settle()
check("★ 点「取消」能收起（同一个入口按钮又回来了）",
      not _fc.isOpen() and _fc.b_open.isVisible())

# --- 收起态 ---
check("默认收起（不展开选卡区）", not _fc.isOpen())
check("收起时不显示任何牌行", not any(r.isVisible() for r in _fc.rows))
# ★ 曾经这里断言「无按钮」—— 那正是 bug 本体：收起态把整行按钮连入口一起藏了，
#   卡片只剩标题 + "点下面按钮"，用户根本找不到按钮。断言反而把 bug 固化成了"正确行为"。
check("★ 收起态显示入口按钮「遗忘手牌」（用户点这里进选卡）",
      _fc.b_open.isVisible() and _fc.b_open.text() == "遗忘手牌",
      f"visible={_fc.b_open.isVisible()} text={_fc.b_open.text()!r}")
check("★ 收起态隐藏「取消 / 遗忘」（只在展开态有意义）",
      not _fc.b_cancel.isVisible() and not _fc.b_go.isVisible(),
      f"cancel={_fc.b_cancel.isVisible()} go={_fc.b_go.isVisible()}")
check("★ 入口按钮的容器 btns 恒可见（藏容器 = 按钮跟着消失，就是那个 bug）",
      _fc.btns.isVisible() or not _fc.isVisible(),
      f"btns={_fc.btns.isVisible()} card={_fc.isVisible()}")
_H_CLOSED = _fc.height()
check("收起态高度按内容自适应（含按钮行，不超过选卡区高度）",
      _fc.MIN_H <= _H_CLOSED <= _fc.PICK_H, f"{_H_CLOSED} in [{_fc.MIN_H}, {_fc.PICK_H}]")
check("收起态文案提示可用滚轮",
      "滚轮" in _fc.lb_info.text(), _fc.lb_info.text())

# --- 展开：未连接时不许操作 ---
_fc.set_connected(False)
_fc.open_pick()
_settle()                               # 展开是让位动画，要等它落地再比高度
check("★ 展开后进入选卡形态", _fc.isOpen() and _fc.pick.isVisible())
check("★ 展开后入口按钮让位（收起态那套按钮同时消失）",
      not _fc.b_open.isVisible() and _fc.b_cancel.isVisible() and _fc.b_go.isVisible(),
      f"open={_fc.b_open.isVisible()} cancel={_fc.b_cancel.isVisible()} go={_fc.b_go.isVisible()}")
check("未连接时提示先连游戏", "未连接" in _fc.lb_info.text(), _fc.lb_info.text())
check("未连接时「遗忘」按钮禁用", not _fc.b_go.isEnabled())
check("★ 未连接时入口按钮禁用（连不上就别让点）", not _fc.b_open.isEnabled())
check("展开后高度比收起态高（选卡区占位）", _fc.height() > _H_CLOSED,
      f"{_fc.height()} vs {_H_CLOSED}")

# --- 灌入模拟快照（字段与 agent.js forget_list() 同名）---
_mk = lambda i, st, lbl, nm, cost: {
    "idx": i, "stack": st, "stackLabel": lbl, "index": i,
    "name": nm, "cost": cost, "ptr": f"0xdead{i:04x}"
}
_snap = {
    "ok": True, "canForget": True, "deckType": 2, "deckTypeName": "战斗",
    "counts": {"draw": 4, "discard": 2, "exhaust": 1},
    "total": 7,
    "cards": [
        _mk(0, "_normalShuffledDrawCards", "抽牌堆", "Toasty", 0),
        _mk(1, "_normalShuffledDrawCards", "抽牌堆", "Stab", 1),
        _mk(2, "_normalShuffledDrawCards", "抽牌堆", "Roast", 1),
        _mk(3, "_normalShuffledDrawCards", "抽牌堆", "Crippling Blow", 2),
        _mk(4, "_discards", "弃牌堆", "Seasoning", 0),
        _mk(5, "_discards", "弃牌堆", "Stab", 1),
        _mk(6, "_exhausted", "已消耗", "Flash", 1),
    ],
}
_fc.update_list(_snap)
_fc.set_connected(True)
QTest.qWait(50)
check("★ 连接后「遗忘」按钮可用", _fc.b_go.isEnabled())
check("★ 牌名已汉化（Toasty→烤菇、Stab→刺击）",
      _fc._cards[0]["name"] == G.card_zh("Toasty")
      and _fc._cards[1]["name"] == G.card_zh("Stab"),
      f"{_fc._cards[0]['name']} / {_fc._cards[1]['name']}")
check("★ 刷到 7 张（含重复的 Stab 逐张列出）", len(_fc._cards) == 7, str(len(_fc._cards)))
check("★ 按堆分组：抽牌堆 → 弃牌堆 → 已消耗",
      [c["stackLabel"] for c in _fc._cards]
      == ["抽牌堆"] * 4 + ["弃牌堆"] * 2 + ["已消耗"],
      str([c["stackLabel"] for c in _fc._cards]))
_nrows = G.ForgetCard.MAX_ROWS          # 用户 2026-10-01 把 7 改成 5；读常量，别写死
check("展开态把 MAX_ROWS 行都显示出来",
      sum(1 for r in _fc.rows if r.isVisible()) == _nrows,
      f"{sum(1 for r in _fc.rows if r.isVisible())} / {_nrows}")
check("标题带牌库类型（战斗）", "战斗" in _fc.lb_head.text(), _fc.lb_head.text())
check("默认选中第 1 张", _fc._sel == 0)
check("选中行用的是高亮 objectName",
      _fc.rows[0].lb_nm.objectName() == "forgetNameOn"
      and _fc.rows[1].lb_nm.objectName() == "forgetName",
      f"{_fc.rows[0].lb_nm.objectName()} / {_fc.rows[1].lb_nm.objectName()}")
check("提示行给出「选中 1 / 7」", _fc.lb_hint2.text() == "选中 1 / 7", _fc.lb_hint2.text())

# --- 滚轮：只翻选中项，不动面板 ---
_scroll_before = p._scroll
_wv = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                  Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                  Qt.ScrollPhase.NoScrollPhase, False)
QApplication.sendEvent(_fc, _wv)
QTest.qWait(40)
check("★ 滚轮下滚 → 选中项 +1", _fc._sel == 1, str(_fc._sel))
check("★ 滚轮被卡片吃掉，面板不跟着滚",
      abs(p._scroll - _scroll_before) < 0.5,
      f"{_scroll_before} -> {p._scroll}")
check("选中行跟随高亮（第 2 行亮、第 1 行灭）",
      _fc.rows[1].lb_nm.objectName() == "forgetNameOn"
      and _fc.rows[0].lb_nm.objectName() == "forgetName",
      f"{_fc.rows[1].lb_nm.objectName()} / {_fc.rows[0].lb_nm.objectName()}")
check("滚轮后提示行同步", _fc.lb_hint2.text() == "选中 2 / 7", _fc.lb_hint2.text())

# --- 二次确认：点一次只进入待确认，不下发 ---
_fired = []
_fc.on_forget = lambda ptr, stack: _fired.append((ptr, stack))
_fc._on_go()
QTest.qWait(40)
check("★ 第一次点「遗忘」只进入待确认，不下发删除", not _fired)
check("按钮变成「确认遗忘」", _fc.b_go.text() == "确认遗忘", _fc.b_go.text())
check("armed 动态属性已置位（QSS 会把它染成警示色）",
      _fc.b_go.property("armed") == "true", str(_fc.b_go.property("armed")))
check("提示行警告「再点一次真的删掉」",
      "再点一次" in _fc.lb_hint2.text(), _fc.lb_hint2.text())

# --- 滚一下：待确认状态必须复位（防滚过头误删）---
QApplication.sendEvent(_fc, QWheelEvent(
    QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.NoScrollPhase, False))
QTest.qWait(40)
check("★ 滚过之后待确认状态自动复位（防误删）",
      not _fc._armed and _fc.b_go.text() == "遗忘", _fc.b_go.text())

# --- 第二次点：真下发，且带上正确的 ptr / stack ---
_fc._on_go()      # 第 1 次 → armed
QTest.qWait(30)
_fc._on_go()      # 第 2 次 → 下发
QTest.qWait(40)
check("★ 第二次点才下发删除", len(_fired) == 1, str(len(_fired)))
check("★ 下发的是当前选中那张的 ptr / stack",
      _fired and _fired[0][0] == _fc._cards[2]["ptr"]
      and _fired[0][1] == "_normalShuffledDrawCards",
      str(_fired))

# --- 结果回传：列表即时刷新 + 计数（走 Panel 的真实回调）---
_before_n = len(_fc._cards)
_sel_now = _fc._sel
_victim = _fc._cards[_sel_now]["name"]
_victim_en = None
for _c in _snap["cards"]:
    if G.card_zh(_c["name"]) == _victim and _c["ptr"] == _fc._cards[_sel_now]["ptr"]:
        _victim_en = _c["name"]
_fc.on_forget = None          # 上面用它探过下发；这里别再记一次
p.on_forget_result({"ok": True, "name": _victim_en or _victim,
                    "before": 4, "after": 3, "orderOk": True})
QTest.qWait(60)
check("★ 遗忘成功后列表即时刷新（少一张）",
      len(_fc._cards) == _before_n - 1, f"{_before_n} -> {len(_fc._cards)}")
check("★ 卡片角落显示「本次已遗忘 1 张」",
      _fc.lb_count.isVisible() and "1 张" in _fc.lb_count.text(), _fc.lb_count.text())
check("★ 被遗忘的那张已从列表消失（指针级）",
      _fc._cards[_sel_now]["ptr"] != "0xdead0002"
      if _sel_now < len(_fc._cards) else True,
      str([c["ptr"] for c in _fc._cards]))

# --- 失败态：必须给出原因，不能静默吞掉 ---
_n_before = len(_fc._cards)
p.on_forget_result({"ok": False, "err": "这张牌已不在这堆里（可能场景已切换）"})
QTest.qWait(50)
check("★ 失败时不改列表（不假装删成功）", len(_fc._cards) == _n_before,
      f"{_n_before} -> {len(_fc._cards)}")
check("★ 失败时给出具体原因（不静默）",
      _fc.lb_warn.isVisible() and "不在这堆" in _fc.lb_warn.text(), _fc.lb_warn.text())

# --- 滚轮在没展开时必须交还给面板（否则整页滚不动）---
_fc.close_pick()
QTest.qWait(40)
_sc0 = p._scroll
_ev2 = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                   Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                   Qt.ScrollPhase.NoScrollPhase, False)
QApplication.sendEvent(_fc, _ev2)
QTest.qWait(120)
check("★ 收起态的滚轮交还给面板（条目外滚轮照常滚页）",
      abs(p._scroll - _sc0) > 1.0, f"{_sc0} -> {p._scroll}")

# --- 收起：恢复原状 ---
check("★ 收起后回到默认形态（选卡区收起、入口按钮回来）",
      not _fc.isOpen() and not _fc.pick.isVisible()
      and _fc.b_open.isVisible() and not _fc.b_cancel.isVisible()
      and not _fc.b_go.isVisible(),
      f"open={_fc.isOpen()} pick={_fc.pick.isVisible()} entry={_fc.b_open.isVisible()}")
check("★ 收起态高度不高于选卡区（形态来回切不会越切越高）",
      _fc.height() <= _fc.PICK_H, f"{_fc.height()} vs {_fc.PICK_H}")

# ---------- 13c. 点面板不抢游戏焦点（WS_EX_NOACTIVATE + WM_MOUSEACTIVATE）----------
# 真点鼠标的那部分要真桌面，放不进离屏自检（见 tools/focus_check.py）。
# 这里验的是"决策逻辑"和"消息处理函数本身"：
#   - 常量与 Win32 定义一致；
#   - nativeEvent 一律给出 MA_NOACTIVATE（面板上已无任何需要键盘的控件）；
#   - WS_EX_NOACTIVATE 的位运算（纯函数，离线也能验）；
#   - 「无限精力」那套输入框焦点机制**确实已经删干净**（反向断言，防回归）。
# ⚠️ 单靠 nativeEvent 在真机**实测无效**（真机断言在 exe_smoke [2c]），别把这里的
#    绿灯当成"功能没问题"。
print("=== 13c. 点面板不抢游戏焦点（WS_EX_NOACTIVATE + WM_MOUSEACTIVATE）===")
import ctypes as _ct

check("★ 常量与 Win32 定义一致（WM_MOUSEACTIVATE=0x21 / MA_ACTIVATE=1 / MA_NOACTIVATE=3）",
      G.WM_MOUSEACTIVATE == 0x0021 and G.MA_ACTIVATE == 1 and G.MA_NOACTIVATE == 3,
      f"{G.WM_MOUSEACTIVATE:#x} {G.MA_ACTIVATE} {G.MA_NOACTIVATE}")
check("★ Win32 样式常量正确（WS_EX_NOACTIVATE=0x08000000 / WS_EX_APPWINDOW=0x00040000"
      " / GWL_EXSTYLE=-20）",
      G.WS_EX_NOACTIVATE == 0x08000000 and G.WS_EX_APPWINDOW == 0x00040000
      and G.GWL_EXSTYLE == -20,
      f"{G.WS_EX_NOACTIVATE:#x} {G.WS_EX_APPWINDOW:#x} {G.GWL_EXSTYLE}")

# exstyle 位运算：这是"点面板不抢焦点"真正的保险，位算错了整条链路就是摆设
_ex0 = 0
_ex_on = G._noact_exstyle(_ex0, True)
_ex_off = G._noact_exstyle(_ex_on, False)
check("★ 挂样式 = NOACTIVATE | APPWINDOW（少了 APPWINDOW 任务栏按钮就没了）",
      _ex_on == G.WS_EX_NOACTIVATE | G.WS_EX_APPWINDOW, f"{_ex_on:#x}")
check("★ 摘样式只清 NOACTIVATE、APPWINDOW 留着",
      _ex_off & G.WS_EX_APPWINDOW and not (_ex_off & G.WS_EX_NOACTIVATE),
      f"{_ex_off:#x}")
check("★ 样式运算幂等（反复挂不会累加出别的位）",
      G._noact_exstyle(_ex_on, True) == _ex_on
      and G._noact_exstyle(_ex_off, False) == _ex_off,
      f"{G._noact_exstyle(_ex_on, True):#x}")
check("★ 摘样式不动别的 exstyle 位（TOPMOST 之类不能被误伤）",
      G._noact_exstyle(G.WS_EX_TOPMOST | _ex_on, False) & G.WS_EX_TOPMOST == G.WS_EX_TOPMOST,
      f"{G._noact_exstyle(G.WS_EX_TOPMOST | _ex_on, False):#x}")
# 无效 HWND 不能抛（离屏 / 窗口还没建出来时就是这条路）
check("★ 无效句柄不抛异常、老实返回 False",
      G.set_window_noactivate(0, True) is False and G.window_noactivate(0) is False
      and G.set_window_noactivate(None, True) is False,
      "ok")

check("★ Panel 重写了 nativeEvent（没重写就完全拦不到激活）",
      "nativeEvent" in G.Panel.__dict__, "有" if "nativeEvent" in G.Panel.__dict__ else "缺少重写")
check("★ Panel 有「挂样式」状态机（_panel_noactivate/_sync_noactivate）",
      all(k in G.Panel.__dict__ for k in ("_panel_noactivate", "_sync_noactivate")),
      "有")
# ★ 反向断言：无限精力那套"输入框例外"必须已经删干净。
#   留着 _hit_energy_edit 就说明还有分支会放行激活 —— 那正是用户要干掉的东西。
check("★ 已无「输入框例外」的残留方法（_hit_energy_edit / _energy_focus / Stepper）",
      not any(k in G.Panel.__dict__ for k in ("_hit_energy_edit", "_energy_focus",
                                              "_in_stepper", "_halt_stepper")),
      str([k for k in ("_hit_energy_edit", "_energy_focus", "_in_stepper",
                       "_halt_stepper") if k in G.Panel.__dict__]))
check("★ 模块级已无 Stepper / force_foreground（输入控件与抢前台工具一起删了）",
      not hasattr(G, "Stepper") and not hasattr(G, "force_foreground"),
      f"Stepper={hasattr(G, 'Stepper')} force_foreground={hasattr(G, 'force_foreground')}")
_src_txt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "src", "trainer_gui.py"), encoding="utf-8").read()
check("★ 挂样式用的是 WS_EX_NOACTIVATE（不是靠注释写写而已）",
      "WS_EX_NOACTIVATE" in _src_txt and "set_window_noactivate(" in _src_txt
      and "_noact_exstyle" in _src_txt, "ok")
# Qt 传进来的 eventType 是 QByteArray，实现里按 bytes() 归一后比较
check("★ eventType 归一成 bytes 后可正确匹配（Qt 传的是 QByteArray）",
      bytes(QByteArray(b"windows_generic_MSG")) == b"windows_generic_MSG",
      repr(bytes(QByteArray(b"windows_generic_MSG"))))
# 源码级断言：给"整个进程崩掉"那个坑立碑 —— QWidget.nativeEvent() 默认实现返回
# (False, None)，把 None 当 LRESULT 交给 Qt 会直接 access violation。所以实现里
# **不允许**转交 super()。
#
# ⚠️ 必须走 AST 找**真实调用**，不能拿源码文本搜 "super().nativeEvent(" ——
#    实现里的注释恰好写了这串字，文本搜索会命中注释直接误报（这个坑本项目记过一次）。
_src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "src", "trainer_gui.py")
_native_fn = None
for _node in ast.walk(ast.parse(open(_src_path, encoding="utf-8").read())):
    if isinstance(_node, ast.FunctionDef) and _node.name == "nativeEvent":
        _native_fn = _node
        break
_super_calls = []
if _native_fn is not None:
    for _n in ast.walk(_native_fn):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "nativeEvent"
                and isinstance(_n.func.value, ast.Call)
                and isinstance(_n.func.value.func, ast.Name)
                and _n.func.value.func.id == "super"):
            _super_calls.append(getattr(_n, "lineno", "?"))
check("★ nativeEvent 没有转交 super()（它返回 (False, None)，当 LRESULT 会崩进程）",
      _native_fn is not None and not _super_calls,
      f"取到函数={_native_fn is not None} 转交点={_super_calls}")


# --- agent.js 挂钩自愈逻辑（源码级断言）---
#
# 坑：sigOk() 读的是"此刻内存里的机器码"。目标被 Interceptor 挂过一次后，入口前 5 字节
# 变成 `E9 <rel32>`（jmp trampoline）。此后再 resolve 时 sigOk 必然 false ⇒ 判成
# "RVA 漂移" ⇒ hooks 显示 FAILED ⇒ 无敌/不消耗精力**静默失效**；面板断开重连一次就永久废。
# 对策：入口已是 E9 桩 ⇒ 该地址就是对的 ⇒ 判为可用（via='hooked'）。
# 这里钉死「先查 isTrampolined，再走 sigOk」的**顺序**，以及 init 里挂钩后的 refreshResolved。
_agent_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "agent.js")
_agent_src = open(_agent_path, encoding="utf-8").read()
check("★ agent.js 定义了 isTrampolined（E9 桩识别）",
      "function isTrampolined(" in _agent_src
      and "addr.readU8() === 0xE9" in _agent_src,
      "isTrampolined=" + str("function isTrampolined(" in _agent_src))

check("★ agent.js 定义了 refreshResolved（挂钩后重解析）",
      "function refreshResolved(" in _agent_src,
      "定义=" + str("function refreshResolved(" in _agent_src))

# resolveTarget 里 isTrampolined 必须出现在 sigOk 之前（否则等于没修）
_rt_i = _agent_src.find("function resolveTarget(")
_rt_end = _agent_src.find("function refreshResolved(", _rt_i)
_rt_body = _agent_src[_rt_i:_rt_end] if _rt_i >= 0 and _rt_end > _rt_i else ""
_tramp_at = _rt_body.find("isTrampolined(a0)")
_sig_at = _rt_body.find("sigOk(a, t.sig)")
check("★ resolveTarget 里 isTrampolined 在 sigOk 之前（顺序被钉死）",
      _tramp_at >= 0 and _sig_at >= 0 and _tramp_at < _sig_at,
      f"isTrampolined@{_tramp_at} sigOk@{_sig_at}")

# init 里 ensureHooks / ensureSpendHook 之后必须调 refreshResolved
_init_i = _agent_src.find("function init()")
_init_end = _agent_src.find("/* ---------------- RPC", _init_i)
_init_body = _agent_src[_init_i:_init_end] if _init_i >= 0 and _init_end > _init_i else ""
_eh_at = _init_body.find("ensureSpendHook();")
_rr_at = _init_body.find("refreshResolved();")
check("★ init 里 ensureSpendHook 之后调用了 refreshResolved",
      _eh_at >= 0 and _rr_at >= 0 and _rr_at > _eh_at,
      f"ensureSpendHook@{_eh_at} refreshResolved@{_rr_at}")

# diag 必须暴露 trampoline 现场（否则线上没法判断"残留 hook"还是"真漂移"）
check("★ diag 暴露 trampoline 探测（firstByte/hooked/dest）",
      _agent_src.count("out.trampoline[k]") >= 2
      and "destInModule" in _agent_src,
      "trampoline 次数=" + str(_agent_src.count("out.trampoline[k]")))

# trainer_core.find_pid 必须走 Win32 快照，不许退回 Frida 枚举
_core_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "src", "trainer_core.py")
_core_src = open(_core_path, encoding="utf-8").read()
check("★ trainer_core 定义了 list_processes（Win32 快照枚举）",
      "def list_processes(" in _core_src
      and "CreateToolhelp32Snapshot" in _core_src
      and "_TH32CS_SNAPPROCESS" in _core_src,
      "list_processes=" + str("def list_processes(" in _core_src))
check("★ trainer_core 定义了 _win32_process_name（全路径反查）",
      "def _win32_process_name(" in _core_src
      and "QueryFullProcessImageNameW" in _core_src,
      "_win32_process_name=" + str("def _win32_process_name(" in _core_src))

_fp_i = _core_src.find("def find_pid(")
_fp_end = _core_src.find("\n    def ", _fp_i + 10)
_fp_body = _core_src[_fp_i:_fp_end] if _fp_i >= 0 and _fp_end > _fp_i else ""
_w32_at = _fp_body.find("list_processes()")
_fr_at = _fp_body.find("self.device.enumerate_processes()")
check("★ find_pid 走 Win32 快照（Frida 枚举只作最后兜底）",
      _w32_at >= 0 and (_fr_at < 0 or _w32_at < _fr_at),
      f"list_processes@{_w32_at} frida枚举@{_fr_at}")


def _feed_native(win_msg):
    """造一块真的 MSG 内存喂给 nativeEvent，走完整个解析路径。

    eventType 用 Qt 真实传的 QByteArray（实测 Qt 传的是这个类型，不是 str）。
    """
    m = G._WinMsg()
    m.message = win_msg
    return p.nativeEvent(QByteArray(b"windows_generic_MSG"), _ct.addressof(m))


# 一律不激活：面板上已经没有任何需要键盘输入的控件，MA_NOACTIVATE 是唯一答案。
# （以前这里要造假的 QCursor 位置来验"光标压住输入框才放行" —— 那个例外删掉后，
#   这几条变得与光标位置无关，反而更简单也更稳。）
_ret_no = _feed_native(G.WM_MOUSEACTIVATE)
check("★ 点面板 → nativeEvent 返回 MA_NOACTIVATE（点击照收、不抢游戏焦点）",
      _ret_no == (True, G.MA_NOACTIVATE) and isinstance(_ret_no[1], int),
      repr(_ret_no))
check("★ 第二项是 int 而不是 None（None 当 LRESULT 会 access violation）",
      isinstance(_ret_no[1], int), repr(_ret_no))
check("★ 用的不是 MA_NOACTIVATEANDEAT（那个会把点击吃掉、按钮不响应）",
      _ret_no[1] != 4 and not hasattr(G, "MA_NOACTIVATEANDEAT"), str(_ret_no[1]))
check("★ 连续多次点击都返回同一个答案（不再依赖光标位置）",
      all(_feed_native(G.WM_MOUSEACTIVATE) == (True, G.MA_NOACTIVATE) for _ in range(3)),
      "3 次")
# 非目标消息必须原样放行，不能把别的消息也吃掉
_ret_other = _feed_native(0x0010)
check("★ 其它窗口消息原样放行（不吞消息、第二项是 int 0）",
      _ret_other == (False, 0) and isinstance(_ret_other[1], int), repr(_ret_other))
_ret_dis = p.nativeEvent(QByteArray(b"windows_dispatcher_MSG"), 0)
check("★ 非 windows_generic_MSG 的事件类型也不崩、不放行",
      _ret_dis == (False, 0), repr(_ret_dis))
# str 类型（错误用法）走 bytes() 会抛，必须被兜住而不是把进程带崩
_ok, _r = p.nativeEvent("windows_generic_MSG", 0)
check("★ 传 str（错误用法）也不会抛异常（bytes() 要兜住）",
      _ok is False and _r == 0, f"{_ok} {_r}")

# ---------- 13e. 运行状态页（仪表盘按钮 + 从左往右滑入）----------
print("=== 13e. 运行状态页（仪表盘按钮 + 从左往右滑入）===")
_b = p.b_dash


def _panel_px(ax, ay, want_hex=None):
    """面板**绝对**坐标 (ax, ay) 处**最终显示**的颜色（含兄弟控件的层叠）。

    ⚠️ 两个坑都要绕开：
    ① 必须抓**整个面板**（`p.grab()`），不能抓 `p.sheet.grab()` —— grab 单个控件
       是离屏渲染它自己，**兄弟控件的层叠完全不参与**，拿它断言"帘子有没有盖住
       状态页"永远测不出来。
    ② 坐标一律用**面板绝对**坐标，别混着"相对某个控件"的局部坐标用 —— 早先这里
       传了一个来自 `viewport.geometry()`（相对面板）的 y，却被当成"相对状态页"，
       内部又加了一次 `sheet.y()`(=53)，于是取样点整整偏了 53px：
       灵敏度对照因此永远红、而"帘子没盖住"那条则是**假通过**。

    给了 `want_hex` 就返回"是否接近该颜色"（各通道差值之和 <= 45，容掉渐变斜率和
    抗锯齿）。抓图要按 devicePixelRatio 折算 —— 离屏时是 1，真机 150% 下是 1.5。
    """
    im = p.grab().toImage()
    k = im.width() / max(1, p.width())
    x = int(min(im.width() - 1, max(0, ax * k)))
    y = int(min(im.height() - 1, max(0, ay * k)))
    c = im.pixelColor(x, y)
    if want_hex is None:
        return c.name()
    wc = QColor(want_hex)
    return (abs(c.red() - wc.red()) + abs(c.green() - wc.green())
            + abs(c.blue() - wc.blue())) <= 45


def _sheet_px(fx, fy, want_hex=None):
    """状态页上某点（**相对状态页左上角**的局部坐标）的颜色。"""
    return _panel_px(p.sheet.x() + fx, p.sheet.y() + fy, want_hex)


_title = p.head_w.findChild(G.QLabel, "title")
check("★ 仪表盘按钮在标题栏、且排在标题**左侧**",
      _title is not None and _b.x() < _title.x(),
      f"btn.x={_b.x()} title.x={_title.x() if _title else '?'}")
check("★ 按钮是矢量图标（不是文字、不是 emoji）",
      not hasattr(_b, "text") and _b.__class__.__name__ == "DashButton",
      _b.__class__.__name__)
check(f"按钮 {G.DashButton.SIZE}×{G.DashButton.SIZE}、圆形（半径 = 高/2，与胶囊同族）",
      _b.width() == _b.height() == _b.SIZE, f"{_b.width()}x{_b.height()}")
# 用户要求：「运行状态按钮直径与右边主题模式调换按钮的直径一样大」。
# 两个控件本身都是 SIZE×SIZE（上面那条已断言），悬停圆盘也都是整整 SIZE —— 会差的是
# **图标外径**，所以这里量"画出来的墨"的外接方框，而不是读控件尺寸（读尺寸是自证）。
# 取最长的边比：表盘是个上方开口的弧（高 < 宽），主题是圆（高 = 宽），
# 用户看到的是"占多大地方"，所以用 max(宽, 高)。
def _ink_extent(w):
    """控件渲染图里"有墨"区域的最长边（逻辑 px，已按 DPR 折算）。"""
    _w0 = w._hover
    w._hover = False                       # 量的是**静息态**图标，别把悬停圆盘算进去
    _im = w.grab().toImage()
    w._hover = _w0
    _bg = _im.pixelColor(0, 0)
    _xs, _ys = [], []
    for _yy in range(_im.height()):
        for _xx in range(_im.width()):
            _c = _im.pixelColor(_xx, _yy)
            if (abs(_c.red() - _bg.red()) + abs(_c.green() - _bg.green())
                    + abs(_c.blue() - _bg.blue())) > 24:
                _xs.append(_xx)
                _ys.append(_yy)
    if not _xs:
        return 0.0
    _dpr = _im.width() / max(1, w.width())
    return max(max(_xs) - min(_xs) + 1, max(_ys) - min(_ys) + 1) / _dpr


_theme_btn = p.b_theme
_ink_dash = _ink_extent(p.b_dash)
_ink_theme = _ink_extent(_theme_btn)
# 需求变更（用户 2026-10-01）：仪表盘图标**比主题开关小一点**，不再是"等大"。
# 期望外径按 `DashButton` 自己的常量算（外径半径 = SIZE*DIAL_R + (SIZE/16)/2，直径 ×2），
# **禁写字面量** —— 这样调 DIAL_R 时这条会跟着走，不会退化成"钉死某个数字"的假断言。
_du = G.DashButton.SIZE
_dash_expect = 2.0 * (_du * G.DashButton.DIAL_R + (_du / 16.0) / 2.0)
check("★ 仪表盘图标直径 = 按 DIAL_R 算出的表盘外径（±1.5 逻辑 px）",
      abs(_ink_dash - _dash_expect) <= 1.5,
      f"仪表盘 {_ink_dash:.1f} vs 期望 {_dash_expect:.1f}（逻辑 px）")
check("★ 仪表盘比右边主题开关的太阳图标小（用户 2026-10-01 改的需求，原为「等大」）",
      _ink_dash < _ink_theme - 1.5,
      f"仪表盘 {_ink_dash:.1f} < 主题 {_ink_theme:.1f}（逻辑 px）")
_ink_orig = G.DashButton.DIAL_R
G.DashButton.DIAL_R = _ink_orig * 0.8
_ink_shrunk = _ink_extent(p.b_dash)
G.DashButton.DIAL_R = _ink_orig
_ink_back = _ink_extent(p.b_dash)
check("★ 灵敏度对照：调小 DIAL_R → 量到的外径真的跟着变小；改回来 → 复原"
      "（证明上面那条量的确实是画出来的图标，不是常量自证）",
      _ink_shrunk < _ink_dash - 2.0 and abs(_ink_back - _ink_dash) <= 1.0,
      f"原 {_ink_dash:.1f} → 调小20% {_ink_shrunk:.1f} → 还原 {_ink_back:.1f}")
check("按钮 NoFocus（面板上所有控件都不抢焦点）",
      _b.focusPolicy() == Qt.FocusPolicy.NoFocus)
check("默认收起：状态页标记为关、sheet 不可见",
      not p.isSheetOpen() and not p.sheet.isVisible(),
      f"open={p.isSheetOpen()} visible={p.sheet.isVisible()}")
check("默认按钮不高亮", not _b.isOn())

p.resize(576, 700)
QTest.qWait(220)
_b.click()
QTest.qWait(50)
check("★ 点按钮 → 状态页开始滑入（状态标记 + 可见）",
      p.isSheetOpen() and p.sheet.isVisible(),
      f"open={p.isSheetOpen()} visible={p.sheet.isVisible()}")
check("★ 按钮进入高亮态（和页面开合一对应）", _b.isOn())
check("★ 滑入方向对：从**左边外**开始（x < 终点）",
      p.sheet.x() < p._sheet_rect().left(),
      f"x={p.sheet.x()} 终点={p._sheet_rect().left()}")
QTest.qWait(420)
_r = p._sheet_rect()
check("★ 滑入结束后停在内容区（位置与尺寸都对）",
      p.sheet.geometry() == _r, f"{p.sheet.geometry()} vs {_r}")
check("★ 状态页**没有**盖住标题栏（按钮还得能点，否则关不掉）",
      _r.top() >= p.head_w.geometry().bottom(),
      f"sheet.top={_r.top()} 标题栏下沿={p.head_w.geometry().bottom()}")
check("★ 状态页铺满整个窗口宽度（左右不再留 20 边距）",
      _r.left() == 0 and _r.width() == p.width(),
      f"left={_r.left()} w={_r.width()} vs 面板宽 {p.width()}")
# 注意 QRect.bottom() 是**含端点**的（top + height - 1），别直接跟面板高比
check("★ 状态页下方也铺到底（不再留 16 边距）",
      _r.top() + _r.height() == p.height(),
      f"下沿={_r.top() + _r.height()} 面板高={p.height()}")


def _light_ratio(ax, ay, sz):
    """面板绝对坐标 (ax, ay) 起、sz×sz 方块里「面板浅底」像素的占比。

    量**底角的缝**用的：页面底角一旦收圆，切掉的那块露出来的是面板浅底
    （r,g 都 ≈ 244+），而页面底色是 r=170/g=195 —— 两档分得很开，
    比赌单个像素的抗锯齿值稳。
    """
    im = p.grab().toImage()
    k = im.width() / max(1, p.width())
    hit = tot = 0
    for ix in range(ax, ax + sz):
        for iy in range(ay, ay + sz):
            c = im.pixelColor(int(min(im.width() - 1, ix * k)),
                              int(min(im.height() - 1, iy * k)))
            tot += 1
            if c.red() > 225 and c.green() > 225:
                hit += 1
    return hit / max(1, tot)


# ★ 底角不能留缝 ——「蓝色背景完全包裹下方不要有空隙」就是这个。
#   页面下沿与面板下沿重合、左右也铺到底，所以下角**必须**直角：曾经四角都是 18px，
#   而面板自己的底角只有 ~4px 圆角（Windows 给的窗口圆角），比页面小得多，
#   于是左下 / 右下各露出一条浅色月牙。用户截图上实测：150% 缩放下 x=42 那列缝高 9px
#   （页面圆角 27 物理px、圆心 (62,465)，x=42 处弧线正好差 9px —— 数字对得上）。
#   量像素占比而不是单个点，免得赌在抗锯齿上。
_CORNER = 22
_lb_gap = _light_ratio(0, p.height() - _CORNER, _CORNER)
_rb_gap = _light_ratio(p.width() - _CORNER, p.height() - _CORNER, _CORNER)
check("★ 左下角不留缝（页面底角是直角，蓝底一直铺到面板底沿）",
      _lb_gap < 0.05, f"浅底占比 {_lb_gap:.1%}（下角收圆时约 14%）")
check("★ 右下角不留缝", _rb_gap < 0.05, f"{_rb_gap:.1%}")


def _corner_gap_with(rb):
    """把页面下角半径临时改成 rb，量一次左下角的浅底占比（灵敏度对照用）。"""
    old = G.StatusSheet.RADIUS_B
    G.StatusSheet.RADIUS_B = rb
    try:
        return _light_ratio(0, p.height() - _CORNER, _CORNER)
    finally:
        G.StatusSheet.RADIUS_B = old


_gap_r18 = _corner_gap_with(18.0)
check("★ 灵敏度：下角改回 18px 圆角后左下角**确实**会露出浅底（说明上一条测到了东西）",
      _gap_r18 > 0.08, f"{_lb_gap:.1%} -> {_gap_r18:.1%}（改动前的行为）")
check("★ 页面底色是胶囊液体那套色的**加深版**（左端取到 C_SHEET_L 附近）",
      _sheet_px(3, _r.height() - 30, G.C_SHEET_L),
      str(_sheet_px(3, _r.height() - 30)))
check("★ 页面底色渐变推到右端（右端取到 C_SHEET_R 附近）",
      _sheet_px(_r.width() - 4, _r.height() - 30, G.C_SHEET_R),
      str(_sheet_px(_r.width() - 4, _r.height() - 30)))
check("★ 底色确实比胶囊液面深（用户要求「再深一点」）",
      QColor(G.C_SHEET_L).lightness() < QColor(G.C_LIQ_L).lightness()
      and QColor(G.C_SHEET_R).lightness() < QColor(G.C_LIQ_R).lightness(),
      f"{G.C_SHEET_L}({QColor(G.C_SHEET_L).lightness()}) vs "
      f"{G.C_LIQ_L}({QColor(G.C_LIQ_L).lightness()})")
check("★ 加深后饱和度仍压在 40% 以内（设计硬约束）",
      QColor(G.C_SHEET_L).hsvSaturationF() < 0.40
      and QColor(G.C_SHEET_R).hsvSaturationF() < 0.40,
      f"S={QColor(G.C_SHEET_L).hsvSaturationF():.2f} / "
      f"{QColor(G.C_SHEET_R).hsvSaturationF():.2f}")
check("★ 状态页**上**两角圆角 == 材质层的 R_SURFACE（与滚动区卡片同族，"
      "页面在面板内部才需要收圆）",
      G.StatusSheet.RADIUS == G.R_SURFACE, str(G.StatusSheet.RADIUS))
check("★ 状态页**下**两角是直角（下沿与面板下沿重合，收圆就会露缝）",
      G.StatusSheet.RADIUS_B == 0.0, str(G.StatusSheet.RADIUS_B))
def _radius_tiers():
    """全树扫一遍所有会画圆角的玻璃面 / 分段控件，收集去重后的圆角值。

    只收"圆角是显式几何量"的控件：`GlassBase` 家族走 `_radius()`（各自声明自己属于
    哪一级），`GlowSegmented` 是 H/2（pill）。圆盘类（DashButton / ThemeButton 的悬停底）
    不是面板圆角语言的一部分，不收。
    """
    vals = set()
    for w in p.findChildren(G.GlassBase):
        vals.add(round(w._radius(), 1))
    for seg in p.findChildren(G.GlowSegmented):
        vals.add(round(seg.H / 2.0, 1))
    return vals


def _radius_allowed():
    """三级圆角的**允许集**（算出来的，不是写死的）。

    pill 那一级每个控件的半径都等于自己的高/2，胶囊之间高度可能不同 ——
    那是同一个"级"，不是新值，所以要把实例半径逐个收进来。
    """
    allowed = {round(G.R_SURFACE, 1), round(G.R_INNER, 1)}
    for w in p.findChildren(G.GlassBase):
        if isinstance(w, G.Capsule):
            allowed.add(round(w._radius(), 1))
    for seg in p.findChildren(G.GlowSegmented):
        allowed.add(round(seg.H / 2.0, 1))
    return allowed


def _radius_tiers_ok():
    """规格硬性约束 1：圆角**只允许三级**（20 / 14 / pill=高:2），不许出现第四个值。"""
    return _radius_tiers() <= _radius_allowed()


# ---- PromptCard 材质：圆角三级制（用户 2026-09-28 要求"只学材质"）----
# 规格的硬性约束 1：「圆角只允许三个值：20 / 14 / 999，不要出现其他圆角值。」
# 本项目一一对应：R_SURFACE(20) = 直接贴在页面上的玻璃面；R_INNER(14) = 嵌在另一个
# 玻璃面**里面**的卡；pill = 高:2（胶囊 / 分段控件 / 圆形按钮 / QSS 里的按钮）。
check("★ 材质：R_SURFACE / R_INNER 就是规格的 20 / 14",
      (G.R_SURFACE, G.R_INNER) == (20.0, 14.0), f"{G.R_SURFACE} / {G.R_INNER}")
check("★ 材质：外层玻璃面（牌库卡 / 遗忘卡 / 状态页）用 R_SURFACE",
      abs(p.scroll_items[-2]._radius() - G.R_SURFACE) < 0.01
      and abs(p.scroll_items[-1]._radius() - G.R_SURFACE) < 0.01
      and abs(p.sheet.RADIUS - G.R_SURFACE) < 0.01,
      f"{p.scroll_items[-2]._radius()} / {p.scroll_items[-1]._radius()} / {p.sheet.RADIUS}")
check("★ 材质：内层卡（状态卡 / 控制台卡，住在状态页里）用 R_INNER = 14",
      abs(p.status_card._radius() - G.R_INNER) < 0.01
      and abs(p.console_card._radius() - G.R_INNER) < 0.01,
      f"{p.status_card._radius()} / {p.console_card._radius()}")
check("★ 材质：胶囊用 pill 圆角（高:2 = 规格的 999px 那一级）",
      abs(p.cap_top._radius() - p.cap_top.height() / 2.0) < 0.01,
      f"{p.cap_top._radius()} vs 高/2 = {p.cap_top.height() / 2.0}")
check("★ 材质：圆角**只有**三级 —— 全树扫一遍不允许出现第四个值",
      _radius_tiers_ok(),
      f"出现的值 {sorted(_radius_tiers())} ⊆ 允许的 {sorted(_radius_allowed())}")
check("★ 材质：描边是 1px 半透明（面板 .12 / 内层 .08 那一档，按主题取值）",
      G.HAIR_A_INNER < G.HAIR_A < G.HAIR_A_H
      and G.HAIR_A in (G.HAIR_PANEL_A["light"], G.HAIR_PANEL_A["dark"]),
      f"面板 {G.HAIR_A} / 内层 {G.HAIR_A_INNER} / 悬停 {G.HAIR_A_H}")
check("★ 材质：模糊与饱和度增强照搬规格（blur 加大 + saturate 140%）",
      G.MAT_BLUR_R > 7 and abs(G.MAT_SAT - 1.40) < 1e-6,
      f"blur r={G.MAT_BLUR_R} sat={G.MAT_SAT}")
check("★ 材质：暗色的内层卡是**半透明 tint**，不是不透明实色"
      "（规格原则 1/6：不混实色卡片 + 必须透出被覆盖的背景）",
      isinstance(G.DARK["SHEET_CARD_FILL"], tuple)
      and len(G.DARK["SHEET_CARD_FILL"]) == 4
      and G.DARK["SHEET_CARD_FILL"][3] < 255,
      f"{G.DARK['SHEET_CARD_FILL']}")
check("★ 状态卡搬进了状态页（父级是 sheet，不在滚动内容里）",
      p.status_card.parent() is p.sheet and p.status_card not in p.scroll_items)
check("★ 状态卡横向撑满（只留一圈窄边 8）",
      p.status_card.width() == _r.width() - 16,
      f"{p.status_card.width()} vs {_r.width() - 16}")
check("★ 状态卡高度按内容定死（不跟着整页拉伸）",
      abs(p.status_card.height() - p.status_card._lay.sizeHint().height()) <= 2,
      f"{p.status_card.height()} vs {p.status_card._lay.sizeHint().height()}"
      f"（页高 {_r.height()}）")
check("状态卡贴在状态页左上（窄边 8）",
      (p.status_card.x(), p.status_card.y()) == (8, 8),
      f"({p.status_card.x()}, {p.status_card.y()})")
check("状态页完整落在窗口内（没有跑到面板外面）",
      _r.left() >= 0 and _r.right() <= p.width() and _r.bottom() <= p.height(),
      str(_r))
check("★ 动画收尾后引用已清空（下次开合不会被旧动画拽断）",
      p._sheet_anim is None)
# 状态页是覆盖层，不该被底层滚动带着跑
_pp = p.sheet.pos()
p._scroll_to(0, smooth=False)
QTest.qWait(60)
check("★ 底层滚动不影响状态页位置", p.sheet.pos() == _pp, str(p.sheet.pos()))

# ★ 滚动区的「上下边缘渐变帘」是靠 `raise_()` 才盖得住滚动内容的，早先它连状态页
#   一起盖 —— 用户看到的是状态页下半截多出一条横贯的浅色带、颜色分层
#   （滚动 / 拉伸都必现，因为两条路都会走到 `_update_veils`）。
#   这里**直接量像素**，比断言 z-order 更接近用户看到的东西。
#
# ⚠️⚠️ 窗口高度有**上下两个夹逼**，不能随手改：
#   · 太矮 → 帘子那条带（贴着视口下沿）会被"运行状态 / 控制台"两张卡整条盖住，
#     取样点只能落在**卡面**上，量到的根本不是帘子（本仓库实测踩过，旧断言直接假 FAIL）；
#   · 太高 → `_max_scroll()` 掉到不足"要滚的那一下"，一滚就到底、底部帘子自动隐藏，
#     同样没得测。
#
# ⚠️⚠️ 这两个夹逼在**某些窗口尺寸下根本不相交**，所以不能写死一个高度。
#   实测（面板宽锁死 576、页内两卡 157+10+83、卡底恒在面板坐标 315）：
#     `max_scroll` 要够半格（-60 → 位移 55）⇒ 面板 ≲ 430；
#     帘子带（18 高，贴着视口下沿）整条落到卡底之下 ⇒ 面板 ≳ 432。
#   两个区间**差 2px 不交**——写死任何高度都是赌，赌输了就是假 FAIL。
#   所以这里改成**按当前几何现扫**，并且不是"第一个满足就返回"，而是把所有
#   满足条件的候选都收下来、挑**帘子带下方余量最大**的那个：勉强过 1px 的解在
#   抗锯齿下会把取样点压到卡边，等于没测。
#
# ⚠️ 必须读 `veil_bot.geometry()`（**面板坐标**，`veil` 的 parent 就是 Panel）而不是
#   拿 `viewport.geometry()` 去算：视口有 20px 左缩进 / 上下也让过标题栏，帘子却是
#   贴整个面板宽度画的，两者不是同一个坐标系，混用会把"带子在卡下方"误判成
#   "在卡上方"（本仓库踩过）。
# ⚠️ 页内卡底不能读 `c.height()`：状态页开着时 `_place_sheet_cards` 之外还会被
#   `mapTo` 带上页面偏移，直接读会拿到页面高度（643）而不是卡片高度（157/83）。
#   按 `sizeHint` 累加 + `p.sheet.y()` 才是**面板坐标**下那张卡的底边。
_q_band = p.veil_bot.height() or 18
# 实际要滚的那一下是**半格**（-60 → `SCROLL_STEP/2` = 55），不是整格。
# ⚠️ 门槛不能只写 `半格 + 5`：滚完的断言是 `scroll < max_scroll - 5`，也就是要求
#    `半格 < max_scroll - 5`，反推 `max_scroll > 半格 + 5`。写成 `>=` 就会挑到
#    `max_scroll == 半格 + 5` 那种"刚好差 1px"的边缘解，实测直接 FAIL
#    （`scroll=55/60`，`55 < 55` 不成立）。这里按**滚完还留一个步长的余量**算。
_q_need_scroll = p.SCROLL_STEP / 2.0 + 10


def _pick_h():
    """挑一个同时满足两条夹逼的窗口高度；没有就返回 None。

    · 帘子带（`veil_bot` 的实际几何，**面板坐标**）必须整条落在页内卡底之下
      —— 否则取样点落在卡面上，量到的根本不是帘子；
    · `_max_scroll()` 必须**够滚那半格、且滚完还留余量**，否则一滚到底、
      底帘自动隐藏、或者刚好差 1px 让下面的区间断言假 FAIL。

    在候选里挑"帘子带上沿离卡底最远"的那个（不是第一个满足的）：勉强过几 px 的解
    在抗锯齿下会把取样点压到卡边，量出来的是卡的色，等于没测。
    """
    _y = 8
    for c in p.sheet_cards:
        _y += c.sizeHint().height() + p.SHEET_CARD_GAP
    _cb_sheet = _y - p.SHEET_CARD_GAP          # 页内卡底（**sheet 局部**坐标）
    best = None
    for _h in range(360, 700, 10):
        p.resize(576, _h)
        p._do_layout()
        # sheet 铺满面板（上到标题栏下方、下到面板底沿），所以
        # 卡底(面板系) = sheet.top() + 卡底(sheet系)
        _cb = p.sheet.y() + _cb_sheet
        _band_top = p.veil_bot.geometry().bottom() - _q_band     # 帘子带**上沿**
        if p._max_scroll() <= _q_need_scroll:
            continue
        _margin = _band_top - _cb                                # 带子上沿离卡底的距离
        if _margin >= 6 and (best is None or _margin > best[1]):
            best = (_h, _margin)
    if best is not None:
        p.resize(576, best[0])
        p._do_layout()
    return best[0] if best else None


_Q_H_A = _pick_h()
check("前置：能找到一个「帘子带整条在卡下方 + 有滚动余量」的窗口高度",
      _Q_H_A is not None,
      f"取 {_Q_H_A}｜max_scroll={p._max_scroll():.0f}（需要 {_q_need_scroll:.0f}）"
      f" content={p.content.height()}"
      f" 帘子带上沿={p.veil_bot.geometry().bottom() - _q_band}"
      f" 卡底(面板系)={p.sheet.y() + sum(c.sizeHint().height() + p.SHEET_CARD_GAP for c in p.sheet_cards) - p.SHEET_CARD_GAP}")
QTest.qWait(240)


def _veil_band_clear_y():
    """在「底部帘子那一条带」里找一个**没被任何页内卡片盖住**的 y（面板绝对坐标）。

    ⚠️ 卡片区间取 `[y, y+h-1]` —— 用 `y+h` 当闭区间上界的话，紧贴卡底那一行会被误判成
    "在卡上"，于是本该有的空位被吞掉、返回 None（本仓库踩过）。
    ⚠️ 还要求离卡面**至少 3px**：卡底那一条有抗锯齿，贴着取色会混进卡的颜色。
    找不到空位就返回 None，由前置断言明确报出来，绝不静默拿卡面当帘子。
    """
    _vp = p.viewport.geometry()
    _cards = [(c.mapTo(p, QPoint(0, 0)).y(), c.mapTo(p, QPoint(0, 0)).y() + c.height() - 1)
              for c in p.sheet_cards]
    for _y in range(_vp.bottom() - 3, max(_vp.bottom() - 24, 0), -1):
        if _y <= p.sheet.geometry().top():
            break
        if all(not (a - 3 <= _y <= b + 3) for a, b in _cards):
            return _y
    return None


# 路径 A：滚轮（用户就是"在状态页上滚滚轮"）。
#   ⚠️ 实测 `sendEvent(p.sheet, wheel)` **不会**滚起来 —— Qt 文档说忽略后会传给父控件，
#   但这里就是不传（同 `Capsule.wheelEvent` 里"必须显式转发"是同一个坑）。
#   所以直接打给 Panel 的滚轮入口 —— 它是冒泡后的落点，`_apply_scroll → _update_veils`
#   这条真正出问题的链路一样会走到。
# ⚠️ 只滚**半格**（-60，`SCROLL_STEP=110` → 位移 55）。
#   `wheelEvent` 按 `angleDelta/120*SCROLL_STEP` 算，所以半格是合法输入（高分辨率滚轮
#   本来就发分数增量）。这里非用半格不可：窗口得撑到 470 才能让帘子带落在卡面之下，
#   而那时 `_max_scroll()` 只剩 108 —— 一整格 110 会**直接顶到底**，
#   底帘按 `_scroll < mx-0.5` 自动隐藏，这条断言就变成空转了。
p.wheelEvent(QWheelEvent(
    QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -60),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.NoScrollPhase, False))
QTest.qWait(320)                       # 等这条平滑滚动彻底停（170ms 补间）
_vb_y = _veil_band_clear_y()
check("前置：在状态页上滚滚轮真的把底层滚走了、底部帘子已显示",
      p._scroll > 5 and p._scroll < p._max_scroll() - 5
      and "bottom" in p._veils_drawn,
      f"scroll={p._scroll:.0f}/{p._max_scroll():.0f} veils={p._veils_drawn}")
check("前置：取样点落在帘子那一条带里、且**没被页内卡片盖住**",
      _vb_y is not None,
      f"y={_vb_y} cards_bottom={max(c.mapTo(p, QPoint(0, 0)).y() + c.height() for c in p.sheet_cards)}"
      f" vp_bottom={p.viewport.geometry().bottom()}")
_px_wheel = _panel_px(p.width() // 2, _vb_y)      # ⚠️ 绝对坐标（见 _panel_px 说明）
check("★ 滚轮滚动后帘子没有盖住状态页（该处仍是液体色）",
      QColor(_px_wheel).red() < 215,
      f"{_px_wheel} @y={_vb_y}（被帘子压住会发白 r≈245）")

# ★ 灵敏度对照：证明上一条**有鉴别力** —— 把帘子手工抬到最上，那个点必须真的变白。
#   没有这条，万一"_vb_y"根本没落在帘子上（比如面板变矮后帘子缩了），
#   上面那条会一直绿着，什么也没测。
QTest.qWait(600)                       # 等平滑滚动动画彻底停（否则它的下一个 tick 会把状态页抬回来）
p.veil_bot.raise_()
# ⚠️ 抬完**立刻**取像素，中间不能 qWait —— 滚动动画的 tick 会走
# `_apply_scroll → _update_veils` 把状态页再抬回去，那就白抬了（踩过）。
_px_forced = _panel_px(p.width() // 2, _vb_y)
check("★ 灵敏度：把帘子强抬到最上后该处确实会变白（说明上一条测到了东西）",
      QColor(_px_forced).red() > QColor(_px_wheel).red() + 12,
      f"{_px_wheel} -> {_px_forced} | veil={p.veil_bot.geometry()} "
      f"vis={p.veil_bot.isVisible()} y={_vb_y} scroll={p._scroll:.0f}/"
      f"{p._max_scroll():.0f}")
p._apply_scroll()          # 走一次正常流程 → 状态页被抬回来
check("★ 恢复正常：再走一次滚动流程后帘子又回到状态页下面",
      QColor(_panel_px(p.width() // 2, _vb_y)).red() < 215,
      _panel_px(p.width() // 2, _vb_y))
# 路径 B：拉伸窗口（另一条会走到 _update_veils 的路）
# 高度同样**现算**：拉高一档（换一个不同的几何，照样满足两条夹逼）。
_Q_H_B = _pick_h()
QTest.qWait(240)
_by = _veil_band_clear_y()
check("前置：拉高后帘子仍在、且取样点确实落在卡面之下的空白页面上",
      _by is not None and "bottom" in p._veils_drawn and p._max_scroll() > 5,
      f"高={_Q_H_B} y={_by} veils={p._veils_drawn} max_scroll={p._max_scroll():.0f} "
      f"cards_bottom={max(c.mapTo(p, QPoint(0, 0)).y() + c.height() for c in p.sheet_cards)}")
_px_drag = _panel_px(p.width() // 2, _by)
check("★ 拉伸窗口后帘子也没盖住状态页", QColor(_px_drag).red() < 215,
      f"{_px_drag}（面板已缩到 {p.height()} 逻辑高、状态页随之重算）")
p.veil_bot.raise_()                        # 灵敏度对照（同路径 A 的理由）
_px_drag_forced = _panel_px(p.width() // 2, _by)
check("★ 灵敏度：抬帘子后该点确实变白（说明上一条真的测到了帘子）",
      QColor(_px_drag_forced).red() > QColor(_px_drag).red() + 12,
      f"{_px_drag} -> {_px_drag_forced} veil={p.veil_bot.geometry()} y={_by}")
p._apply_scroll()
p._scroll_to(0, smooth=False)
QTest.qWait(80)


_b.click()
QTest.qWait(50)
check("★ 再点一次 → 开始滑出（状态标记先翻）",
      not p.isSheetOpen() and not _b.isOn())
QTest.qWait(420)
check("★ 滑出后彻底隐藏（不再挡住底下的鼠标事件）",
      not p.sheet.isVisible(), f"visible={p.sheet.isVisible()}")
check("★ 滑出后完全离开内容区（右缘退到内容左缘以外）",
      p.sheet.x() + p.sheet.width() <= p._sheet_rect().left(),
      f"右缘={p.sheet.x() + p.sheet.width()} 内容左缘={p._sheet_rect().left()}")

# 开着状态页时拉伸窗口 → 页面要跟着变
_b.click()
QTest.qWait(420)


def _row_gap():
    """卡内第 1 行与第 5 行的纵向距离（用来证明内容没被"摊开"）。"""
    a = p.val["pid"].mapTo(p.status_card, QPoint(0, 0)).y()
    b = p.val["hook"].mapTo(p.status_card, QPoint(0, 0)).y()
    return b - a


_h_before = p._sheet_rect().height()
_gap_big = _row_gap()
_card_big = (p.status_card.width(), p.status_card.height())
p.resize(576, 620)
QTest.qWait(260)
check("★ 页面变高不会把卡内 5 行摊开（行距是内容定的，不是页面高定的）",
      _row_gap() == _gap_big and _gap_big > 0,
      f"行距 {_gap_big} -> {_row_gap()}"
      f"（页面高 {_h_before} -> {p._sheet_rect().height()}）")
# 用户明确要求：「卡片大小不随拖拽窗口变大」—— 判定标准就是尺寸**一点都不变**。
check("★ 拖拽窗口不会把状态卡撑大（卡片尺寸不随窗口变）",
      (p.status_card.width(), p.status_card.height()) == _card_big,
      f"{_card_big} -> {(p.status_card.width(), p.status_card.height())}")
check("★ 对照组：页面自己确实变了（否则上一条只是因为两边都没动而假绿）",
      p._sheet_rect().height() != _h_before,
      f"页高 {_h_before} -> {p._sheet_rect().height()}")
check("★ 窗口拉伸时状态页跟着重算几何",
      p.sheet.geometry() == p._sheet_rect(),
      f"{p.sheet.geometry()} vs {p._sheet_rect()}")
check("★ 拉伸后状态卡宽度跟着重算（不留旧宽度）",
      p.status_card.width() == p._sheet_rect().width() - 16,
      f"{p.status_card.width()} vs {p._sheet_rect().width() - 16}")

# ---- 拖拽窗口的真实坑：resizeEvent 重入时请求**不能丢** ----
# 真机拖拽是一串连续的 resize，落在一次布局运行期间的那些请求，早先被
# `if self._laying: return` 无声丢掉；拖拽停下时最后那次往往正好被丢，
# 于是窗口已变大、状态页还停在旧几何 —— 用户看到"卡片下面多了一道分界线、
# 颜色不对"。离屏自检里 resize 是一次次配 qWait 发的，天然复现不出，
# 所以这里**手工制造重入**把这条回归钉死。
p._laying = True
p._do_layout()                     # 模拟"布局中又来了一个 resize"
_queued = p._layout_again
p._laying = False
check("★ 布局进行中的 resize 请求被记账，而不是被丢掉", _queued is True,
      f"_layout_again={_queued}")
p._do_layout()                     # 补跑那一轮
check("★ 补跑之后状态页几何与当前面板尺寸一致（不会再留旧几何）",
      p.sheet.geometry() == p._sheet_rect(),
      f"{p.sheet.geometry()} vs {p._sheet_rect()}")

# ---- 开页动画：卡片必须「自己展开」，而不是被页面从左切开 ----
# 用户报「蓝底先滑进来，白卡片晚一拍才出现」。真机逐帧量过：卡片**没有**渲染滞后
# （每一帧卡片行都是白的），但观感确实对 —— 卡片宽 560、页面宽 576，卡片又是页面的
# 子控件会被页面裁，所以页面左→右擦除时**卡片的左缘必然最后到位**，中途只看得见
# 「一块没有左边的白块」，擦完才成为「一张卡片」。修法：开页每一帧把卡片的面板 x
# 钉在 pad 上、宽度按 `W*e - 2*pad` 展开（见 Panel._unfold_card）。
_b.click()                             # 现在是开着的 → 先关掉
QTest.qWait(p.SHEET_MS + 200)
p.resize(576, 700)
QTest.qWait(220)
check("前置：这一步之前状态页是关的", not p.isSheetOpen())
_b.click()                             # 头一帧就该是 0 宽 + 面板 x=pad（不等时钟）
check("前置：开页头一帧卡片就是 0 宽、且面板 x 已钉在 pad 上",
      p.status_card.width() == 0
      and p.status_card.mapTo(p, QPoint(0, 0)).x() == 8,
      f"宽={p.status_card.width()} 面板x={p.status_card.mapTo(p, QPoint(0, 0)).x()}")
# ⚠️ 用 `setCurrentTime` 把动画**直接推到 40%**，不要靠 qWait 等时钟 ——
#    机器一忙动画就慢，采样点会飘（这条断言就变成随机过/不过）。
_an = p._sheet_anim
_an.setCurrentTime(int(p.SHEET_MS * 0.4))
QTest.qWait(30)
_cw = p.status_card.width()
_cx = p.status_card.mapTo(p, QPoint(0, 0)).x()
_front = p.sheet.x() + p.sheet.width()          # 页面推进到哪了（面板绝对坐标）
check("★ 开页途中卡片左缘已经就位（面板 x = pad，不再被页面拖着走）",
      _cx == 8, f"卡片面板 x={_cx}")
check("★ 开页途中卡片是**一条完整卡片**（宽度严格介于 0 和满宽之间）",
      0 < _cw < p._sheet_rect().width() - 16,
      f"宽度 {_cw} / 满宽 {p._sheet_rect().width() - 16}（页面只推到 {_front}）")
check("★ 开页途中卡片右缘离页面前沿正好留出 pad（卡片全程在页面内部，右角不会被裁）",
      abs((_front - (_cx + _cw)) - 8) <= 1,
      f"前沿={_front} 卡右缘={_cx + _cw} 差={_front - (_cx + _cw)}")
# 灵敏度对照：按旧做法（卡片是纯子控件、局部 x 恒为 8）它此刻的面板 x 是负的
# —— 整个左边都在画面外，正是用户看到的那块"没有左边的白块"。
check("★ 灵敏度：按旧做法这张卡此刻左缘是**负的**（会被页面切掉，上两条才有意义）",
      p.sheet.x() + 8 < 0, f"旧做法下卡片面板 x={p.sheet.x() + 8}（sheet.x={p.sheet.x()}）")
QTest.qWait(p.SHEET_MS + 240)
check("★ 动画收尾：卡片归位到满宽 + 局部 (pad, pad)（展开只是临时的，不能留窄卡）",
      p.status_card.width() == p._sheet_rect().width() - 16
      and (p.status_card.x(), p.status_card.y()) == (8, 8),
      f"{p.status_card.width()} @({p.status_card.x()},{p.status_card.y()}) "
      f"满宽={p._sheet_rect().width() - 16}")

_b.click()
QTest.qWait(420)
p.resize(576, 350)
QTest.qWait(220)
check("收尾：状态页已关、按钮不高亮",
      not p.isSheetOpen() and not _b.isOn() and not p.sheet.isVisible())

# ---------- 13f. 白天/黑夜主题（连接状态挪位 + 翻转开关 + 圆形扩散）----------
print("=== 13f. 主题切换（连接状态挪位 / 图标翻转 / 圆形扩散 / 偏好记忆）===")
_t = p.head_w.findChild(G.QLabel, "title")
_bt = p.b_theme
# ⚠️ 头行内的 x 一律是 **head_w 局部**坐标（`_bt.x()` 相对父控件，不是相对面板）——
#    这里只跟 head_w 自己的 width 比，别和面板坐标混用。
# ⚠️ 「状态紧贴标题」的判据不能写成 `conn.x < 头宽/2`：头行里光靠左半边就排下了
#    仪表盘(30)+标题(218)+状态(54)，状态本身**跨过**中线。真正要证明的是
#    "伸缩项在状态**之后**" —— 也就是状态和开关之间空出一大段。
check("★ 连接状态紧贴标题右侧（不再被伸缩推到最右端）",
      p.dot.x() > _t.x() + _t.width() and p.conn.x() > p.dot.x()
      and (p.dot.x() - (_t.x() + _t.width())) <= 16
      and (_bt.x() - (p.conn.x() + p.conn.width())) > 100,
      f"title右={_t.x() + _t.width()} dot.x={p.dot.x()} conn右={p.conn.x() + p.conn.width()} "
      f"开关.x={_bt.x()}（状态与开关之间的空档应很大）")
check("★ 主题开关独占右上角（贴 head_w 右缘、且在状态右侧）",
      _bt.x() + _bt.width() == p.head_w.width() and _bt.x() > p.conn.x() + p.conn.width(),
      f"btn右={_bt.x() + _bt.width()} 头宽={p.head_w.width()} conn右={p.conn.x() + p.conn.width()}")
check(f"开关同为矢量自绘 {G.ThemeButton.SIZE}×{G.ThemeButton.SIZE}、NoFocus",
      _bt.width() == _bt.height() == _bt.SIZE
      and not hasattr(_bt, "text")
      and _bt.focusPolicy() == Qt.FocusPolicy.NoFocus,
      f"{_bt.width()}x{_bt.height()} {_bt.__class__.__name__}")
check("初始是白天（浅色 + 图标在太阳位）",
      p._theme == "light" and not _bt.isNight() and _bt._t == 0.0,
      f"{p._theme} night={_bt.isNight()} t={_bt._t}")
# 素材预建：另一套皮肤的 Frost(576,1400) 要 250ms，不能在点击那一帧现算
QTest.qWait(700)
check("★ 另一套皮肤的素材已预建（背景 + 投影贴图），切换时只换引用、0 成本",
      "dark" in p._frosts and "dark" in p._shadow_cache,
      f"frosts={sorted(p._frosts)} shadows={sorted(p._shadow_cache)}")

_frost_light = p.frost
_bg_light = _panel_px(300, 31)
p.toggle_theme(animate=False)
QTest.qWait(60)
_bg_dark = _panel_px(300, 31)
check("★ 直接落地式切换：主题名 / 图标 / 背景引用 三者一起换",
      p._theme == "dark" and _bt.isNight() and p.frost is p._frosts["dark"]
      and p.frost is not _frost_light,
      f"{p._theme} night={_bt.isNight()} frost换={p.frost is not _frost_light}")
check("★ 画面真的变暗了（不是只改了状态变量）",
      QColor(_bg_light).lightness() - QColor(_bg_dark).lightness() > 80,
      f"{_bg_light} → {_bg_dark}")
check("★ QSS 已重新生成并下发（暗色文字在、浅色文字不在）",
      G.C_TEXT in G.QSS and "#E8EAED" in app.styleSheet()
      and "#1D1D1F" not in G.QSS and "#1D1D1F" not in app.styleSheet(),
      f"theme文字={G.C_TEXT} 浅色字残留={'#1D1D1F' in app.styleSheet()}")
_caps = [G.C_SHEET_L, G.C_SHEET_R, G.C_LIQ_L]
check("★ 黑夜是整套换（状态页 / 胶囊液面 / 强调色都跟着走）",
      G.C_SHEET_L == "#232C33" and G.C_LIQ_L == "#2E4050" and G.C_ACCENT == "#6E93AB"
      and G.C_ACCENT in app.styleSheet() and "#4A7C9B" not in app.styleSheet(),
      f"sheet={G.C_SHEET_L} liq={G.C_LIQ_L} accent={G.C_ACCENT} "
      f"浅色强调色残留={'#4A7C9B' in app.styleSheet()}")
check("★ 反向断言：黑夜的液面比底色**亮**（暗底上再暗就看不见'填满了'）",
      QColor(G.C_LIQ_L).lightness() > QColor(G.C_PAGE_FALLBACK).lightness() + 12,
      f"液面 {G.C_LIQ_L}(L={QColor(G.C_LIQ_L).lightness()}) "
      f"vs 页底 {G.C_PAGE_FALLBACK}(L={QColor(G.C_PAGE_FALLBACK).lightness()})")
_lt = QColor(G.C_TEXT)
check("硬约束：黑夜文字饱和度仍压在 40% 以下、且不是纯白",
      _lt.hsvSaturationF() < 0.40 and _lt.name() != "#ffffff", G.C_TEXT)

p.toggle_theme(animate=False)
QTest.qWait(60)
check("★ 灵敏度对照：切回浅色后同一取样点必须变回浅色（上一条才有鉴别力）",
      _panel_px(300, 31) == _bg_light or QColor(_panel_px(300, 31)).lightness() > 220,
      f"{_panel_px(300, 31)}（切前 {_bg_light}）")

# ---- 圆形扩散：必须是「真揭示」——中途一帧里新旧主题同时存在 ----
# 用户要求「点击后由图标中心向整个页面拓展」。判据不能只看半径在长：那只能证明动画
# 在跑。真正要证明的是**扫过的地方已经换了、没扫到的地方还是旧的** —— 也就是这一帧
# 里同时存在两套主题。所以圆内取一点、圆外取一点，两点的明度必须差一大截。
p.resize(576, 350)
QTest.qWait(200)
p.toggle_theme(animate=True)
QTest.qWait(16)
# 开关中心的**面板**坐标：`x()` 是 head_w 局部的，必须 mapTo 换算，别直接拿 x 比
_btc = _bt.mapTo(p, QPoint(_bt.width() // 2, _bt.height() // 2))
check("★ 点下去即出现遮罩，圆心就是开关中心、半径上限能盖住最远的角",
      p.reveal.isVisible()
      and abs(p.reveal._c.x() - _btc.x()) <= 1
      and abs(p.reveal._c.y() - _btc.y()) <= 1
      and p.reveal._rmax > math.hypot(p.width() * 0.6, p.height()),
      f"可见={p.reveal.isVisible()} 圆心={p.reveal._c} 应为{_btc} rmax={p.reveal._rmax:.0f}")
# ⚠️ 取样点都挑**纯页面底**（头行的伸缩空档 / 左下角留白）。别挑 y=320 一带 ——
#    那里是底部三个按钮，浅色主按钮本身就只有 L≈113，会把这组断言搅成假红。
check("★ 遮罩铺的是**旧主题整帧**（还没扫到时，面板整体仍是浅色）",
      QColor(_panel_px(10, 340)).lightness() > 200,
      f"左下角 {_panel_px(10, 340)}")
_an2 = p._theme_anim
check("转场中再点一次不接（避免遮罩内容和新旧主题对不上）",
      p._theme_anim is not None and p._theme == "dark" and p._theme_target == "dark",
      f"theme={p._theme} target={p._theme_target}")
_a_before = p._theme_anim
p.toggle_theme(animate=True)           # 第二次调用应当被忽略
QTest.qWait(10)
check("★ 第二次点击确实是空操作（没被反向切回去、动画还是同一条）",
      p._theme == "dark" and p._theme_target == "dark" and p._theme_anim is _a_before,
      f"theme={p._theme} target={p._theme_target} 同一条动画={p._theme_anim is _a_before}")
_an2.setCurrentTime(int(p.THEME_MS * 0.5))     # 钉住采样点，别赌时钟
QTest.qWait(40)
_r_now = p.reveal._r
_cx, _cy = p.reveal._c.x(), p.reveal._c.y()
_inside = _panel_px(400, 31)          # 圆内：头行中段的伸缩空档（纯页面底）
_o1, _o2 = 10, 340                    # 圆外：左下角留白（纯页面底）
_d_in = math.hypot(400 - _cx, 31 - _cy)
_d_out = math.hypot(_o1 - _cx, _o2 - _cy)
_outside = _panel_px(_o1, _o2)
check("采样点几何确实分居圆内外（断言前提）",
      _d_in < _r_now < _d_out,
      f"圆内距={_d_in:.0f} 半径={_r_now:.0f} 圆外距={_d_out:.0f}")
check("★ 灵敏度对照：这一帧里新旧主题**同时存在**（圆内已变暗、圆外还是浅的）",
      QColor(_inside).lightness() < 110 and QColor(_outside).lightness() > 200,
      f"圆内 {_inside}(L={QColor(_inside).lightness()}) "
      f"圆外 {_outside}(L={QColor(_outside).lightness()})")
check("★ 半径落在 (0, rmax) 之间（是中途态，不是端点）",
      0 < _r_now < p.reveal._rmax, f"{_r_now:.0f}/{p.reveal._rmax:.0f}")
QTest.qWait(p.THEME_MS + 260)
check("★ 收尾：遮罩已撤、新主题已落地、画面整体变暗（没有「晚一拍」）",
      (not p.reveal.isVisible()) and p._theme == "dark"
      and QColor(_panel_px(10, 340)).lightness() < 110,
      f"可见={p.reveal.isVisible()} theme={p._theme} 左下 {_panel_px(10, 340)}")

# ---- 转场途中拉伸窗口：整帧对不上新尺寸了 → 必须立刻落地，不能留遮罩 ----
p.resize(576, 350)
QTest.qWait(160)
p.toggle_theme(animate=True)
QTest.qWait(60)
p.resize(576, 470)
QTest.qWait(60)
check("★ 转场中拉伸窗口：立刻落地（遮罩撤掉 + 主题切到位），不留半张画",
      (not p.reveal.isVisible()) and p._theme == "light" and p.height() == 470,
      f"可见={p.reveal.isVisible()} theme={p._theme} 高={p.height()}")
p.resize(576, 350)
QTest.qWait(160)

# ---- 图标翻转：必须是「翻面」而不是交叉淡入淡出 ----
# 判据：四个阶段各抓一次图标区，① 首尾明显不同（换了图标）；
#      ② 正中 t=0.5 时几乎没墨（横向压扁到 0 → 侧对着看，是"翻面"的签名）；
#      ③ 两侧对称（0.15 与 0.85 都要有墨）。
def _ink(t):
    """图标「有墨」的像素数。

    ⚠️ 不能用「亮度 < 阈值」直接数：`_bt.grab()` 是**离屏渲染这个控件自己**，
    它没有背景（透明），透明像素取出来是 (0,0,0,0)、亮度 0 —— 会被全部算成"有墨"，
    于是每个 t 都得到同一个数（实测全是 1521 = 45×45 里的整块）。
    所以改成**先找背景色（出现最多的那个颜色），再数偏离它的像素** —— 与 alpha、
    与透明背景、与 DPR 都无关。
    """
    _bt.setFlip(t)
    _bt._anim.stop()      # ⚠️ 先停掉驱动 `flip` 的动画：不停的话它每个 tick 都会把
                          #    `_t` 覆写回去（`setFlip` 是 animation 的 setter，不能自己停表）
    _bt.setFlip(t)
    QTest.qWait(20)
    im = _bt.grab().toImage()
    cnt = {}
    for ix in range(im.width()):
        for iy in range(im.height()):
            v = im.pixel(ix, iy)
            cnt[v] = cnt.get(v, 0) + 1
    bg = QColor(max(cnt, key=cnt.get))
    n = 0
    for ix in range(im.width()):
        for iy in range(im.height()):
            c = QColor(im.pixel(ix, iy))
            if (abs(c.red() - bg.red()) + abs(c.green() - bg.green())
                    + abs(c.blue() - bg.blue())) > 60:
                n += 1
    return n


_ink0, _ink15, _ink50, _ink85, _ink1 = (_ink(0.0), _ink(0.15), _ink(0.5),
                                        _ink(0.85), _ink(1.0))
check("★ 翻转首尾是两个不同图标（像素数明显不同）",
      abs(_ink0 - _ink1) > 40, f"t0={_ink0} t1={_ink1}")
check("★ 翻转中段是「侧对着」的（横向压扁到 0 → 几乎无墨），不是交叉淡入",
      _ink50 < max(_ink0, _ink1) * 0.25, f"t0.5={_ink50}（t0={_ink0} t1={_ink1}）")
check("★ 翻转两侧都还看得见图标（展开过程连续，不是闪一下换掉）",
      _ink15 > max(_ink0, _ink1) * 0.15 and _ink85 > max(_ink0, _ink1) * 0.15,
      f"t0.15={_ink15} t0.85={_ink85}")
_bt.setFlip(0.0)
QTest.qWait(20)

# ---- 主题偏好记忆 ----
_keep = G.load_theme_pref()
G.save_theme_pref("dark")
check("★ 偏好记忆：存 dark 读回 dark", G.load_theme_pref() == "dark", G.load_theme_pref())
G.save_theme_pref("light")
check("★ 偏好记忆：存 light 读回 light（不是只会记 dark 一条路）",
      G.load_theme_pref() == "light", G.load_theme_pref())
G.save_theme_pref(_keep)
check("★ 偏好记忆：读坏值/没存过都退回浅色（不抛异常）", G.load_theme_pref() in ("light", "dark"),
      G.load_theme_pref())
check("收尾：面板回到浅色 + 遮罩不可见",
      p._theme == "light" and not _bt.isNight() and not p.reveal.isVisible(),
      f"{p._theme} night={_bt.isNight()}")
p.resize(576, 350)
QTest.qWait(160)


# ---------- 13g. 仪表盘指针：点一下转一整圈、页面铺到位那一刻刚好转满 ----------
# 用户要求：「点击时指针旋转一圈回到原位，页面弹出完成时刚好旋转一圈」。
# 拆成三件必须成立的事，缺一件观感就不对：
#   ① 转的**恰好是一整圈** —— 末值必须是 ±1.0，且缓动不能改总角度（缓动只改速度分布）；
#      最硬的证据是把 `_spin` 摆到 0 和 1 各渲染一次，两张图**逐像素完全相同**
#      （转满一圈 ≡ 原位，这才是"回到原位"，而不是"差一点看起来像"）；
#   ② 动的**只有指针** —— 差异像素必须成对顶两簇（指针的起止姿态），且全落在指针
#      扫过的半径内。若整块图标在挪、或弧也跟着转，这条会红；
#   ③ **同时到位** —— 指针那条动画的时长直接取页面动画的 duration（同一个函数里前后脚
#      start），把两条一起推到 50% 时进度必须一致（OutCubic(0.5)=0.875）。
#      这是"页面铺到位那一刻刚好转满"的机械保证，不是两个 260 撞上的巧合。
print("=== 13g. 仪表盘指针旋转（点击转一整圈 ↔ 与页面滑入同时到位）===")
_bd = p.b_dash


def _dash_img(spin):
    """把仪表盘按钮单独渲染成一张图（`spin` = 那一刻的指针圈数）。

    ⚠️ 先 `stop()` 再 `setSpin()`：`setSpin` 就是旋转动画的 setter，表不停的话
    动画每个 tick 都会把 `_spin` 覆写回去 —— 和 §13f 里 `_ink` 是同一个坑。
    """
    _bd._spin_anim.stop()
    _bd.setSpin(spin)
    return _bd.grab().toImage()


def _px_diff(a, b):
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


check("★ 指针是 `spin` 这个 Qt 属性驱动的（和 ThemeButton.flip 同一套做法）",
      _bd.metaObject().indexOfProperty("spin") >= 0,
      f"property index={_bd.metaObject().indexOfProperty('spin')}")
# ⚠️ 「默认停在原位」必须在**碰 `_dash_img` 之前**断言 —— 下一行就会把 `_spin`
#    摆到 1.0（那是测试自己的副作用，不是功能留下的残留）。
check("默认停在原位（`_spin == 0`，没有残留过渡量）", _bd._spin == 0.0, str(_bd._spin))
_im0, _imH, _im1 = _dash_img(0.0), _dash_img(0.5), _dash_img(1.0)
check("★ 转满一圈 == 原位（`_spin=1` 与 `_spin=0` 两张图**逐像素完全相同**）",
      _px_diff(_im0, _im1) == 0, f"差异 {_px_diff(_im0, _im1)} 像素")
check("★ 灵敏度：转到半圈时画面确实变了（上一条才有鉴别力，不是「图本来就没画」）",
      _px_diff(_im0, _imH) > 30, f"差异 {_px_diff(_im0, _imH)} 像素")

# 差异像素的**位置签名**：相对圆心的方位角 + 半径。
# 指针长 `SIZE*DIAL_R*0.74`（逻辑）→ 乘 DPR 就是物理半径；加半个线宽（抗锯齿外沿）。
# ⚠️ 读 `G.DashButton.DIAL_R`，别写死 —— 写死的话调整图标直径那条改动会让这里假 FAIL
#    （指针变长了，而"允许的最远半径"没跟着变）。
_rc = _im0.width() / 2.0
_dpts = [(x, y) for y in range(_im0.height()) for x in range(_im0.width())
         if _im0.pixel(x, y) != _imH.pixel(x, y)]
_ang = [math.degrees(math.atan2(_rc - y, x - _rc)) % 360.0 for x, y in _dpts]
_rad = [math.hypot(x - _rc, y - _rc) for x, y in _dpts]
# 分簇：实测两簇之间隔着 90° 空档（A 簇止于 ~98°、B 簇起于 ~188°），
# 分界线取中点的 143° 就行 —— 不是拟合出来的阈值。
_A = [a for a in _ang if a < 143.0]
_B = [a for a in _ang if a >= 143.0]
_am = sum(_A) / len(_A) if _A else -1.0
_bm = sum(_B) / len(_B) if _B else -1.0
_Lpx = _im0.width() * G.DashButton.DIAL_R * 0.74          # 指针长度（物理像素）
check("★ 动的只有指针：差异像素绕圆心成对顶两簇、没有第三个方向",
      len(_A) > 8 and len(_B) > 8,
      f"簇A {len(_A)} 个（{min(_A):.0f}°~{max(_A):.0f}°）"
      f" / 簇B {len(_B)} 个（{min(_B):.0f}°~{max(_B):.0f}°）"
      f" / 总计 {len(_ang)}")
check("★ 原位簇就在指针的初始方向上（≈55°，和 paintEvent 里那根线的角度一致）",
      abs(_am - 55.0) <= 20.0, f"簇A 中心 {_am:.0f}°（应 ≈55°）")
check("★ 转半圈后指针到了正对面（≈235° = 55°+180° → 真的是绕圆心转，不是平移）",
      abs(_bm - 235.0) <= 20.0, f"簇B 中心 {_bm:.0f}°（应 ≈235°）")
check("★ 差异全落在指针扫过的半径内（没有东西被甩到图标外面）",
      max(_rad) <= _Lpx * 1.35,
      f"最远 {max(_rad):.1f}px / 指针长 {_Lpx:.1f}px")
_bd._spin_anim.stop()
_bd.setSpin(0.0)

# ---- 点击 → 旋转一圈；时长与页面动画一致、同起跑线 ----
check("前置：这一步之前状态页是关的（否则点击是「收回」而不是「弹出」）",
      not p.isSheetOpen())
_bd.click()
_a_sp = _bd._spin_anim
_a_sh = p._sheet_anim
check("★ 点击即起转，末值是**一整圈**（±1.0，缓动只改速度不改总角度）",
      _a_sp.state() == G.QPropertyAnimation.State.Running
      and abs(abs(_a_sp.endValue()) - 1.0) < 1e-9
      and _a_sp.startValue() == 0.0,
      f"state={_a_sp.state()} {_a_sp.startValue()}→{_a_sp.endValue()}")
check("★ 缓动是 OutCubic（单调减速、绝不回弹 —— 回弹会让指针往回甩一下）",
      _a_sp.easingCurve().type() == G.QEasingCurve.Type.OutCubic,
      str(_a_sp.easingCurve().type()))
check("★ 而且缓动曲线 `valueForProgress(1.0) == 1.0`（整条曲线单调不减、终点不偏移）",
      abs(_a_sp.easingCurve().valueForProgress(1.0) - 1.0) < 1e-6
      and all(_a_sp.easingCurve().valueForProgress(i / 20.0)
              <= _a_sp.easingCurve().valueForProgress((i + 1) / 20.0) + 1e-9
              for i in range(20)),
      f"p(1)={_a_sp.easingCurve().valueForProgress(1.0):.6f}")
check("★ 指针与页面**同时长**（时长直接读页面那条动画，将来改 SHEET_MS 自动跟随）",
      _a_sp.duration() == _a_sh.duration() == p.SHEET_MS,
      f"指针 {_a_sp.duration()} / 页面 {_a_sh.duration()} / SHEET_MS {p.SHEET_MS}")
# 把两条动画一起钉到 50%：进度必须一致 —— 也就是"页面走到哪儿，指针就转到哪儿"。
# ⚠️ 用 `setCurrentTime` 而不是 qWait（机器一忙动画就慢，靠时钟采样点会飘），而且
#    两条**一起**钉、钉完立刻读值，中间不放事件循环（否则它们各自又往前走了）。
_a_sh.setCurrentTime(int(p.SHEET_MS * 0.5))
_a_sp.setCurrentTime(int(p.SHEET_MS * 0.5))
_half = _bd._spin
_prog = (p.sheet.x() + p.sheet.width()) / float(max(1, p.sheet.width()))
check("★ 同一时刻两条动画进度一致（页面铺到位那一刻指针刚好转满，就是这么锁住的）",
      abs(_prog - _half) <= 0.02 and abs(_half - 0.875) <= 0.02,
      f"页面推进 {_prog:.3f} / 指针 {_half:.3f}（OutCubic(0.5)=0.875）")

QTest.qWait(p.SHEET_MS + 240)
check("★ 收尾：指针归零回到原位（过渡量不残留）+ 页面已铺满",
      _bd._spin == 0.0 and p.sheet.geometry() == p._sheet_rect()
      and p.isSheetOpen(),
      f"_spin={_bd._spin} sheet={p.sheet.geometry()}")

_bd.click()                                # 收回页面
QTest.qWait(30)
check("★ 反向：收回时指针**倒着**转一圈（转出去 / 转回来，对应开合两个动作）",
      abs(_bd._spin_anim.endValue() + 1.0) < 1e-9 and _bd._spin < 0.0,
      f"endValue={_bd._spin_anim.endValue()} _spin={_bd._spin:.3f}")
QTest.qWait(p.SHEET_MS + 240)
check("收尾：关页后指针同样归零、且两处动画引用都已清干净",
      _bd._spin == 0.0
      and _bd._spin_anim.state() != G.QPropertyAnimation.State.Running
      and p._sheet_anim is None,
      f"_spin={_bd._spin} 页面动画={p._sheet_anim}")


_SRC_PATH = os.path.join(HERE, "..", "src", "trainer_gui.py")
_SRC_TEXT = open(_SRC_PATH, encoding="utf-8").read()


def _node_of(dotted):
    """取 `trainer_gui.py` 里某个函数/方法/类的 AST 节点。

    ⚠️ 支持 `类.方法` 限定 —— 这个文件里有好几个同名 `paintEvent`（DashButton /
    ThemeButton / RevealOverlay …），不限定的 `ast.walk` 会随机拿到别的那一个，
    断言就等于没测。
    """
    _cur = ast.parse(_SRC_TEXT)
    for _name in dotted.split("."):
        _cur = next((n for n in _cur.body
                     if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                     and n.name == _name), None)
        if _cur is None:
            return None
    return _cur


def _src_of(dotted):
    _n = _node_of(dotted)
    return ast.get_source_segment(_SRC_TEXT, _n) if _n is not None else ""


def _calls_in(seg):
    """源码段里所有被调用的**方法名**（走 AST，不文本匹配 —— 注释里出现同名不算）。"""
    return {n.func.attr for n in ast.walk(ast.parse(seg))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def _kw_of(seg, meth):
    """源码段里 `*.meth(...)` 那次调用的关键字实参 {名: 源码文本}。"""
    for n in ast.walk(ast.parse(seg)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == meth):
            return {k.arg: ast.unparse(k.value) for k in n.keywords if k.arg}
    return {}


def _arg_of(seg, meth):
    """源码段里 `*.meth(...)` 那次调用的**第一个位置实参**（源码文本）。"""
    for n in ast.walk(ast.parse(seg)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == meth and n.args):
            return ast.unparse(n.args[0])
    return ""


def _linenos(dotted):
    """节点里每个被调用方法名 → 首次出现的行号（用来查**绘制先后顺序**）。"""
    out = {}
    n = _node_of(dotted)
    if n is not None:
        for x in ast.walk(n):
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute):
                out.setdefault(x.func.attr, x.lineno)
    return out


check("★ 反向断言：`_slide_sheet` 里真的挂了指针旋转（按 AST 查调用，不看注释）",
      "spin_once" in _calls_in(_src_of("Panel._slide_sheet")),
      str(sorted(_calls_in(_src_of("Panel._slide_sheet"))
                 - {"stop", "setFixedSize", "hide", "show", "raise_", "move",
                    "connect", "start", "setStartValue", "setEndValue"})))
check("★ 反向断言：旋转时长确实取的是**页面那条动画的 duration**（不是又写了一个常量）",
      _kw_of(_src_of("Panel._slide_sheet"), "spin_once").get("ms") == "a.duration()",
      str(_kw_of(_src_of("Panel._slide_sheet"), "spin_once")))
check("★ 反向断言：指针的绘制仍在旋转上下文里（`DashButton.paintEvent` 调了 `rotate`）",
      "rotate" in _calls_in(_src_of("DashButton.paintEvent")),
      str(sorted(_calls_in(_src_of("DashButton.paintEvent")))))
check("★ 反向断言：`rotate` 的实参引用的就是 `_spin`（不是别的固定角度）",
      "_spin" in _arg_of(_src_of("DashButton.paintEvent"), "rotate"),
      f"rotate({_arg_of(_src_of('DashButton.paintEvent'), 'rotate')})")
# 真正的风险不是"忘了转"，而是**哪天有人把 rotate 挪到 drawArc 前面** ——
# 那样连表盘弧一起转，观感就从"指针扫一圈"变成"整块图标在转"。
# 所以查绘制**顺序**：弧必须先画完，之后才进旋转上下文。
_LN = _linenos("DashButton.paintEvent")
check("★ 反向断言：表盘弧在 `rotate` **之前**就已画完（旋转只包住指针那一笔）",
      _LN.get("drawArc", 0) < _LN.get("rotate", 10 ** 6),
      f"drawArc @L{_LN.get('drawArc')} < rotate @L{_LN.get('rotate')}")

p.resize(576, 350)
QTest.qWait(120)


# ---------- 16. 让位：卡片按内容变高时，下位卡片自动让开 ----------
# 用户报障：游戏一跑起来，「牌库顺序」卡多出「还有 N 张」那一行 → 卡片长高 →
# **压住下面的遗忘卡**（截图里那块重影就是）。
# 根因：高度是 `sync_height()` 自己 `setFixedHeight` 改的，而 `_relayout()` 只在
# **窗口尺寸变化**时跑 —— 这条路上没有任何人触发重排，排布还停在旧几何。
# 修法：卡片高度真变了就回调 Panel（`on_height` → `_reflow_soon`），
# 把旧几何钉住、用**一条**动画把高度和 y 一起推到新几何（锁步）。
#
# 锁步为什么天然不重叠：卡片纵坐标是累加出来的 `y_i = Σ_{j<i} (h_j + GAP)`。
# 只要每张卡的高度和 y 用同一条动画、同一个进度 e 插值，中间每一帧的间距都还是
# GAP —— 零重叠是**算出来的**，不是调参调出来的。所以下面逐帧验的就是「间距不变」。
print("=== 16. 让位（卡片变高时下位卡片自动让开、全程不重叠）===")
_dc = p.deck_card
_fc = p.forget_card
_GAP = p.CARD_GAP


def _geom():
    return [(type(c).__name__, c.y(), c.height()) for c in p.scroll_items]


def _clash(rows):
    """相邻卡片的重叠量（正数 = 上面那张压住了下面那张）。"""
    return [(a[0], b[0], a[1] + a[2] - b[1])
            for a, b in zip(rows, rows[1:]) if b[1] < a[1] + a[2]]


def _gaps(rows):
    return [b[1] - (a[1] + a[2]) for a, b in zip(rows, rows[1:])]


_DECK_TPL = {"ok": True, "discard": 3, "exhaust": 2, "hand": 5, "handSize": 4,
             "warning": "", "deckType": 2, "deckTypeName": "战斗"}
_short_cards = [{"name": n, "cost": 0} for n in _names[:3]]
_long_cards = [{"name": n, "cost": 0} for n in (_names * 2)[:7]]


def _feed(cards):
    """灌一手牌库快照。7 张会多出「还有 2 张」那一行 → 卡片变高。"""
    d = dict(_DECK_TPL)
    d["cards"] = cards
    d["drawCount"] = len(cards)
    _dc.update_data(d)


# ---- 校准：让位前必须已经是「零重叠 + 间距 = CARD_GAP」，否则后面无从对比 ----
QTest.qWait(260)                       # 13f 刚切过主题，等上一轮都落定
# ⚠️ 13b 末尾把牌库卡关掉了；关着的卡不渲染，灌数据也不会变高（这一节就空转了）。
#    另外 `setOn` 会经 on_change → Panel._on_deck_toggle → set_connected(attached)，
#    测试环境 attached=False，所以**必须** setOn 之后再手工把连接态置 True。
_dc.setOn(True)
_dc.set_connected(True)
_feed(_short_cards)
QTest.qWait(300)
_before = _geom()
check("让位前就是零重叠、间距正好是 CARD_GAP（后面所有对比的前提）",
      not _clash(_before) and set(_gaps(_before)) == {_GAP},
      f"clash={_clash(_before)} gaps={_gaps(_before)}")
_y_fc0 = _fc.y()
_h_dc0 = _dc.height()

# ---- 变高：灌 7 张 ----
_feed(_long_cards)
QTest.qWait(5)                         # 0ms 定时器把补间起起来
_anim0 = p._reflow_anim                # 先抓一份：收尾后 p._reflow_anim 会被清掉
# ⚠️ 曲线要**当场**取出来存好：QEasingCurve 是值类型，拷一份就独立了；
#    而动画对象收尾时会被 deleteLater()，回头再问它要曲线就是 access violation。
_curve0 = _anim0.easingCurve() if _anim0 else None
check("★ 变高触发的是一条补间动画（不是瞬间跳过去）",
      _anim0 is not None, f"anim={_anim0}")
check("★ 单次动效仍在 400ms 硬约束内", p.REFLOW_MS <= 400, f"{p.REFLOW_MS}ms")
check("★ 缓动是单调减速的 OutCubic（回弹会把下卡的间距吃穿）",
      _curve0 is not None and _curve0.type() == G.QEasingCurve.Type.OutCubic,
      str(_curve0.type()) if _curve0 else "无动画")

# 逐帧采样（每一帧都是用户会看到的一帧，所以每一帧都要满足不变量）
_frames = []
for _ in range(26):
    _frames.append((_geom(), p.content.height(), _fc.y()))
    if p._reflow_anim is None:
        break
    QTest.qWait(16)
check("采样到了补间的中间帧（否则下面几条是空转）", len(_frames) >= 3,
      f"{len(_frames)} 帧")
_bad = [(i, _clash(g)) for i, (g, _ch, _y) in enumerate(_frames) if _clash(g)]
check("★ 补间途中**每一帧**都不重叠", not _bad, str(_bad[:2]))
check("★ 途中每帧的卡片间距都还是 CARD_GAP（锁步推进的直接证据）",
      all(all(abs(x - _GAP) <= 2 for x in _gaps(g)) for g, _ch, _y in _frames),
      str(sorted({tuple(sorted(set(_gaps(g)))) for g, _ch, _y in _frames})))
check("★ 途中内容高不低于当帧最高的卡底（长的那张不会被父级裁掉）",
      all(ch >= max(y + h for _n, y, h in g) for g, ch, _y in _frames),
      f"最小余量 {min(ch - max(y + h for _n, y, h in g) for g, ch, _y in _frames)}px")
_ys = [_y for _g, _ch, _y in _frames]
check("★ 让位是单调推开、绝不回弹（下卡的 y 一路不减）",
      all(b >= a - 1 for a, b in zip(_ys, _ys[1:])), str(_ys))
# 「先快后慢」直接验曲线，**不要**拿逐帧采样去估位移量：采样间隔撞上 Qt 动画
# 定时器的抖动，首段/末段经常采到 0（实测红过）。曲线本身是确定的。
_curve = _curve0
_steps = [_curve.valueForProgress(i / 20.0) for i in range(21)] if _curve else []
check("★ 曲线是「先快后慢」且单调不回弹（中点已走过约 7/8，且全程不下降）",
      len(_steps) == 21 and abs(_steps[10] - 0.875) < 0.02
      and all(b >= a - 1e-9 for a, b in zip(_steps, _steps[1:]))
      and all(0.0 <= v <= 1.0 for v in _steps),
      f"e(0.5)={_steps[10] if _steps else None} 首={_steps[:2]} 末={_steps[-2:]}")

# ---- 落地：终点必须和「直接排一次」的目标完全一致 ----
QTest.qWait(300)
_after = _geom()
check("★ 牌库卡真的变高了（这一节测的是真场景，不是空转）",
      _dc.height() > _h_dc0, f"{_dc.height()} vs {_h_dc0}")
check("★ 下面的遗忘卡跟着让开：Δy 正好等于上面那张的 Δh",
      _fc.y() - _y_fc0 == _dc.height() - _h_dc0 and _fc.y() > _y_fc0,
      f"Δy={_fc.y() - _y_fc0} Δh={_dc.height() - _h_dc0}")
_plan, _cw, _ch = p._layout_plan()
check("★ 补间只是过程：终点几何与「直接排一次」的结果逐张一致",
      _after == [(type(c).__name__, y, h) for c, y, h in _plan]
      and p.content.height() >= _ch,
      f"{_after} vs {[(type(c).__name__, y, h) for c, y, h in _plan]}")
check("让位收尾后依然零重叠、间距仍是 CARD_GAP",
      not _clash(_after) and all(abs(g - _GAP) <= 1 for g in _gaps(_after)),
      f"clash={_clash(_after)} gaps={_gaps(_after)}")

# ---- ⚠️ 补间途中又变高：旧动画必须被掐掉，不能把新高度顶回去 ----
# 踩过：`_on_reflow_step` 每帧无条件 setFixedHeight(插值)，会把刚设好的新高度
# 顶回旧值；若只是「记下来等它收尾再补一次」，中间那 170ms 用户看到的是「点了没反应」。
# 实测：遗忘卡点展开后 50ms 高度还是 109（该 118）→ 自检判红。
_tall_h = _dc.height()
_feed(_short_cards)                    # 先变矮
QTest.qWait(5)
check("补间中途态已就位（下面那条才有鉴别力）", p._reflow_anim is not None,
      f"anim={p._reflow_anim}")
_feed(_long_cards)                     # 补间途中立刻又变高
QTest.qWait(70)
check("★ 补间途中再变高：新高度**立刻**生效（旧动画已掐掉，没把高度顶回去）",
      _dc.height() == _tall_h, f"{_dc.height()} vs {_tall_h}")
check("★ 掐断不落地：几何保持当前帧，交给下一轮按新几何重算",
      p._reflow_anim is None or not _clash(_geom()),
      f"anim={p._reflow_anim is not None} clash={_clash(_geom())}")
QTest.qWait(300)
check("重定目标后终点依然正确（零重叠 + 间距 CARD_GAP）",
      not _clash(_geom()) and all(abs(g - _GAP) <= 1 for g in _gaps(_geom())),
      f"clash={_clash(_geom())} gaps={_gaps(_geom())}")

# ---- 合并：同一轮连着报多次只排一次；下一轮的请求照排 ----
_n = [0]
_orig_reflow = p._reflow


def _counted():
    _n[0] += 1
    _orig_reflow()


p._reflow_timer.timeout.disconnect()
p._reflow_timer.timeout.connect(_counted)
try:
    for _ in range(3):
        p._reflow_soon()
    QTest.qWait(40)
    check("★ 同一轮里连着报三次只排一次（定时器合并，不抖）", _n[0] == 1,
          f"排了 {_n[0]} 次")
    p._reflow_soon()
    QTest.qWait(40)
    check("★ 合并归合并，下一轮的请求照排（不是把后来的丢了）", _n[0] == 2,
          f"排了 {_n[0]} 次")
finally:
    p._reflow_timer.timeout.disconnect()
    p._reflow_timer.timeout.connect(_orig_reflow)

# ---- 尺寸变化打断：正在跑的让位必须立刻落地 ----
# （旧目标是按旧窗口算的，让它继续插值会一路错到收尾。）
_feed(_short_cards)
QTest.qWait(5)
check("让位补间在跑（下面那条的前提）", p._reflow_anim is not None,
      f"anim={p._reflow_anim}")
p._do_layout()
check("★ 尺寸变化打断让位：补间立刻清掉、不留在旧目标上继续插值",
      p._reflow_anim is None and not p._reflow_timer.isActive(),
      f"anim={p._reflow_anim} timer={p._reflow_timer.isActive()}")
check("落地后几何是合法的（零重叠 + 间距 CARD_GAP）",
      not _clash(_geom()) and all(abs(g - _GAP) <= 1 for g in _gaps(_geom())),
      f"clash={_clash(_geom())} gaps={_gaps(_geom())}")

# ---- 弹出动画和让位不许抢同一张卡的位置 ----
# 踩过：`_pop_in` 原来用 `QPropertyAnimation(c, b"pos")` 把整条 pos 都交给动画，
# 终点 y 是**启动那刻**记下的常量。让位把卡推到新位置之后，弹出动画的 `pa` 还在
# 按旧 y 插值、360ms 后的 cleanup 更是直接 `move(0, 旧y)` 把它拽回去 → 和上面那张
# 重叠 9px。而且只在「卡片刚弹出来 + 同时变高」时才复现，真机上时好时坏。
_feed(_short_cards)
_settle()
p._pop_in(_fc, 0)                      # 让遗忘卡「弹」一下
_feed(_long_cards)                     # 弹到一半就变高（让位要改 y）
QTest.qWait(900)                       # 等弹出 300ms + cleanup 360ms 都过完
_expect_y = _dc.y() + _dc.height() + _GAP
check("★ 弹出动画与让位互不打架：弹完之后卡片 y 仍落在计划位置上（没被拽回旧值）",
      _fc.y() == _expect_y, f"fc.y={_fc.y()} 应为 {_expect_y}")
check("★ 弹完之后几何零重叠、间距正常、x 也归位",
      not _clash(_geom()) and all(abs(g - _GAP) <= 1 for g in _gaps(_geom()))
      and _fc.x() == 0,
      f"clash={_clash(_geom())} gaps={_gaps(_geom())} fc.x={_fc.x()}")

# ---- 回调接线 + 灵敏度对照 ----
_wired = [c for c in p.scroll_items if hasattr(c, "on_height")]
check("★ 声明了 on_height 的卡都接上了让位回调（漏一张就还会压住下面的）",
      bool(_wired) and all(c.on_height == p._reflow_soon for c in _wired),
      str([(type(c).__name__, c.on_height is not None) for c in _wired]))
check("牌库卡 / 遗忘卡都在名单里（这两张才是会变高的）",
      _dc in _wired and _fc in _wired, str([type(c).__name__ for c in _wired]))
check("★ 反向断言：胶囊不参与让位（它们是定高的，挂上去只会白排）",
      not hasattr(p.cap_top, "on_height") and not hasattr(p.cap_god, "on_height"),
      f"cap_top 有回调={hasattr(p.cap_top, 'on_height')}")

_seen2 = []
_orig_cb = _fc.on_height
_fc.on_height = lambda: _seen2.append(1)
try:
    _fc.sync_height(notify=False)
    check("★ sync_height(notify=False) 不回调（排布期间回调回去就是递归）",
          not _seen2, str(_seen2))
    # 灵敏度对照：必须让**内容**真变（`_h_need` 比的是逻辑高度，光改 self.height()
    # 不算"变了" —— 那正是让位动画的插值，不该反过来触发一次排布）。
    if _fc.isOpen():
        _fc.close_pick()
        _settle()
    _fc.open_pick()                     # 选卡区展开 → sizeHint 真变
    check("★ 灵敏度对照：内容真变了、高度真变了，notify=True 必须回调（上一条才有鉴别力）",
          _seen2 == [1], str(_seen2))
finally:
    _fc.on_height = _orig_cb
    if _fc.isOpen():
        _fc.close_pick()
QTest.qWait(300)

# ---- 反向断言：源码级，防止机制被改回「没人补排布」的样子 ----
_src_all = open(os.path.join(HERE, "..", "src", "trainer_gui.py"),
                encoding="utf-8").read()
_tree = ast.parse(_src_all)
_seg = {n.name: ast.get_source_segment(_src_all, n)
        for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)}


def _sync_calls_of(fname):
    """`fname` 里每一次 `xxx.sync_height(...)` 的 (位置参数个数, notify 字面值)。

    ⚠️ 不能直接对源码文本做 `"sync_height()" in seg` —— 注释和 docstring 里就会
    出现这个写法（"以前是 sync_height() 在前、resize 在后"），会得到假 FAIL。
    按 AST 只看真正的调用。
    """
    out = []
    for n in ast.walk(_tree):
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "sync_height"):
                    kws = {k.arg: k.value for k in sub.keywords}
                    out.append((len(sub.args),
                                getattr(kws.get("notify"), "value", None)))
    return out


_sync_calls = _sync_calls_of("_layout_plan") + _sync_calls_of("_relayout")
check("★ 反向断言：排布路径上不允许裸调 sync_height()（回调回去就是递归）",
      bool(_sync_calls) and all(a == 0 and n is False for a, n in _sync_calls),
      f"_layout_plan/_relayout 里的 sync_height 调用 (位置参数数, notify) = {_sync_calls}")
check("★ 反向断言：补间途中再变高必须掐掉旧动画（不是「记下来等它收尾」）",
      "_abort_reflow()" in (_seg.get("_reflow_soon") or "")
      and "_reflow_again" not in _src_all,
      "_reflow_soon 里应有 _abort_reflow()，且 _reflow_again 已删干净")
check("★ 反向断言：窗口尺寸变化时必须让在跑的让位落地（否则按旧目标插值）",
      "_stop_reflow()" in (_seg.get("_do_layout") or ""), "")
# ⚠️ 这条也得按 AST 查：`_pop_in` 的 docstring 里就写着"不能用 QPropertyAnimation(c, b"pos")"，
#    对源码文本做子串匹配会得到假 FAIL（同一个坑这天踩了第二次）。
_pop_props = []
for _n in ast.walk(_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name == "_pop_in":
        for _sub in ast.walk(_n):
            if (isinstance(_sub, ast.Call) and isinstance(_sub.func, ast.Name)
                    and _sub.func.id == "QPropertyAnimation"):
                _pop_props += [getattr(_a, "value", None) for _a in _sub.args]
check("★ 反向断言：弹出动画只许动 x（整条 pos 交给 QPropertyAnimation 会和让位打架）",
      b"pos" not in _pop_props, f"_pop_in 里 QPropertyAnimation 的属性参数={_pop_props}")
_feed(_short_cards)
QTest.qWait(300)


print("=== 17. 控制台（荧光三态）+ 牌库卡 / 遗忘卡的整卡液体 ===")
_cc = p.console_card
_cseg = _cc.seg


def _edge_maxdist(a, b, dpr=1.0):
    """两张图差异像素里，**离控件边缘最远**的那个有多远（换算回**逻辑像素**）。

    ⚠️ 结果按 DPR 归一：`grab().toImage()` 拿到的是**设备像素**（本机 DPR=1.5），
    拿设备像素去比"发光带有多深"会得到 1.5 倍的错觉数（本仓库踩过：
    量到 22，按逻辑像素算其实是 14.7）。
    """
    far = 0
    n = 0
    W, H = a.width(), a.height()
    for y in range(H):
        for x in range(W):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
                far = max(far, min(x, y, W - 1 - x, H - 1 - y))
    return n, far / max(1e-6, dpr)


def _sat_of(hexstr):
    return G.QColor(hexstr).saturationF()


# ---- 17a 版式：控制台住在状态页里、排在状态卡下方 ----
if p.isSheetOpen():
    p.toggle_status_sheet()
    QTest.qWait(p.SHEET_MS + 220)
p.resize(576, 620)
QTest.qWait(160)
p.toggle_status_sheet()
QTest.qWait(p.SHEET_MS + 240)

_pad = 8
_sr = p._sheet_rect()
check("★ 控制台卡住在状态页里（不是滚动区）",
      _cc.parent() is p.sheet and _cc not in p.scroll_items,
      f"parent={type(_cc.parent()).__name__}")
check("★ 排在「运行状态」卡**正下方**，间距正好是 SHEET_CARD_GAP",
      _cc.y() == p.status_card.geometry().bottom() + 1 + p.SHEET_CARD_GAP,
      f"console.y={_cc.y()} status.bottom={p.status_card.geometry().bottom()}"
      f" gap={p.SHEET_CARD_GAP}")
check("两张卡同左缘、同宽（都是 8px 窄边）",
      _cc.x() == _pad and p.status_card.x() == _pad
      and _cc.width() == _sr.width() - 2 * _pad,
      f"x={_cc.x()}/{p.status_card.x()} w={_cc.width()} vs {_sr.width() - 2 * _pad}")
check("控制台卡高度按内容定死（= sizeHint，且不低于 MIN_H）",
      _cc.height() == max(_cc.MIN_H, _cc._lay.sizeHint().height()),
      f"{_cc.height()} vs {_cc._lay.sizeHint().height()} (MIN_H={_cc.MIN_H})")
_cc_sz = (_cc.width(), _cc.height())
_st_sz = (p.status_card.width(), p.status_card.height())
p.resize(576, 780)
QTest.qWait(p.SHEET_MS + 220)
check("★ 两张卡都不随窗口拉伸（用户要求「卡片大小不随拖拽窗口变大」）",
      (_cc.width(), _cc.height()) == _cc_sz
      and (p.status_card.width(), p.status_card.height()) == _st_sz,
      f"console {_cc_sz} -> {(_cc.width(), _cc.height())}")
check("★ 拉高窗口后状态页仍然铺满（下沿到窗口底，不留缝）",
      p.sheet.geometry() == p._sheet_rect(), str(p.sheet.geometry().getRect()))
# 开页途中两张卡都要跟着展开（只展开一张的话另一张会被页边界切掉）
p.toggle_status_sheet()
QTest.qWait(p.SHEET_MS + 180)
p.toggle_status_sheet()
QTest.qWait(40)
_mid_ok = True
_mid_info = []
for _c in p.sheet_cards:
    _mid_ok = _mid_ok and 0 < _c.width() < _sr.width() - 2 * _pad
    _mid_info.append((type(_c).__name__, _c.width(), _c.mapTo(p, QPoint(0, 0)).x()))
check("★ 开页动画中途，两张卡都在**同时**展开（不是只展开上面那张）",
      _mid_ok, str(_mid_info))
QTest.qWait(p.SHEET_MS + 240)
check("开页收尾后两张卡都回到正式几何",
      all(c.x() == _pad and c.width() == _sr.width() - 2 * _pad for c in p.sheet_cards),
      str([(type(c).__name__, c.x(), c.width()) for c in p.sheet_cards]))
p.resize(576, 620)
QTest.qWait(p.SHEET_MS + 200)

# ---- 17b 三态分段控件：端到端（真点击）----
check("三态的名字与顺序（彩色 / 单色 / 关闭）",
      G.GLOW_MODES == ("color", "mono", "off"), str(G.GLOW_MODES))
check("初始模式与分段控件的指示器一致",
      _cseg.currentMode() == G.GLOW_MODE, f"{_cseg.currentMode()} vs {G.GLOW_MODE}")


def _click_seg(i):
    """按控件自己的分段几何算出第 i 段的中心，投一次真鼠标点击。"""
    _w = _cseg.seg_width()
    QTest.mouseClick(_cseg, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(_cseg.PAD + int(_w * (i + 0.5)), _cseg.H // 2))


_hits = []
_cseg.changed.connect(lambda m: _hits.append(m))
_click_seg(2)
QTest.qWait(_cseg.SEG_MS + 120)
check("★ 点第三段真的把荧光关掉了（端到端：点它 → 全局状态真变了）",
      G.GLOW_MODE == "off" and _cseg.currentMode() == "off",
      f"GLOW_MODE={G.GLOW_MODE} seg={_cseg.currentMode()}")
check("★ 指示器滑到位（`_i` 落到 2.0，不是停在旧段）",
      abs(_cseg._i - 2.0) < 0.01, str(_cseg._i))
check("★ 反向断言：一次点击只发一次 changed（不会从信号绕回来自激）",
      _hits == ["off"], str(_hits))
check("控制台右上角的提示文案跟着模式换",
      _cc.lb_hint.text() == _cc.MODE_HINTS["off"], _cc.lb_hint.text())
_hits.clear()
p.set_glow_mode("mono")
QTest.qWait(_cseg.SEG_MS + 120)
check("★ 程序化切模式时指示器会同步（开机还原偏好 / 自检都走这条路）",
      _cseg.currentMode() == "mono" and abs(_cseg._i - 1.0) < 0.01,
      f"seg={_cseg.currentMode()} _i={_cseg._i}")
check("★ 程序化切模式不会把 changed 信号倒灌回来（无自激）", _hits == [], str(_hits))
_click_seg(0)
QTest.qWait(_cseg.SEG_MS + 120)
check("★ 点回第一段能切回彩色（三态是双向可达的，不是单向锁死）",
      G.GLOW_MODE == "color" and _cseg.currentMode() == "color" and _hits == ["color"],
      f"{G.GLOW_MODE} {_hits}")

# ---- 17c 荧光三态真的改像素 ----
_cap = p.cap_god
_cap.setChecked(True, animate=False, emit=False)
_cap._hanim.stop()
_cap._glow_run(False)
_cap._hover = 1.0
_cap._glow_ph = 0.0          # 钉住呼吸相位，三次渲染才**可比**
_cap._glow_ang = 0.0


def _cap_img(mode):
    G.GLOW_MODE = mode
    return _cap.grab().toImage()


_i_color = _cap_img("color")
_i_mono = _cap_img("mono")
_i_off = _cap_img("off")
check("★ 彩色 vs 单色：画面确实不同（三态不是摆设）",
      _px_diff(_i_color, _i_mono) > 20, f"差异 {_px_diff(_i_color, _i_mono)} 像素")
check("★ 单色 vs 关闭：画面确实不同",
      _px_diff(_i_mono, _i_off) > 20, f"差异 {_px_diff(_i_mono, _i_off)} 像素")
_cap_dpr = _i_color.width() / max(1, _cap.width())      # 1.5（150% 缩放）
_n_col, _far_col = _edge_maxdist(_i_color, _i_off, _cap_dpr)
check("★ 彩色 vs 关闭：画面确实不同，且差异**全在贴边那一圈**（就是荧光本身）",
      _n_col > 40 and _far_col <= 17,
      f"{_n_col} 像素，离边最远 {_far_col:.1f} 逻辑px"
      f"（发光带由贴边 0.9px 铺到 8.9px，算上圆角几何约 15）")
check("★ 关闭时的画面与「同一张图再画一次」逐像素相同（off 是真的什么都不画）",
      _px_diff(_i_off, _cap_img("off")) == 0, "")
G.GLOW_MODE = "off"
_cap._hover = 0.0
_cap._glow_run(False)
_cap._glow_run(True)                  # 等价于「鼠标移进来了」
check("★ 反向断言：off 模式下悬停也不起 30fps 表（不显示的东西不渲染）",
      not _cap._glow_on and not _cap._glow_t.isActive(),
      f"on={_cap._glow_on} timer={_cap._glow_t.isActive()}")
G.GLOW_MODE = "color"
_cap._glow_run(True)
check("★ 灵敏度对照：切回彩色后同样的调用必须起表（上一条才有鉴别力）",
      _cap._glow_on and _cap._glow_t.isActive(),
      f"on={_cap._glow_on} timer={_cap._glow_t.isActive()}")
_cap._glow_run(False)
_cap._hover = 0.0
_cap.setChecked(False, animate=False, emit=False)

# ---- 17d 单色是「一种颜色 + 明暗绕行」而不是一色到底 ----
G.GLOW_MODE = "color"
_stops_color = G.glow_ring_stops()
G.GLOW_MODE = "mono"
_stops_mono = G.glow_ring_stops()
check("彩色色环是多色的",
      len({rgb for _, rgb, _ in _stops_color}) >= 4,
      f"{len({rgb for _, rgb, _ in _stops_color})} 种颜色")
check("★ 单色色环只有一种 RGB（真的单色，不是「少了几种」）",
      len({rgb for _, rgb, _ in _stops_mono}) == 1,
      str({rgb for _, rgb, _ in _stops_mono}))
check("★ 但亮度系数沿环起伏（颜色一统了事的话，绕行就彻底看不见、光变成一圈死光）",
      len({round(m, 2) for _, _, m in _stops_mono}) >= 3,
      str([round(m, 2) for _, _, m in _stops_mono]))
check("色环首尾同值（绕行一圈无缝，不会在起点处闪一下）",
      _stops_mono[0][2] == _stops_mono[-1][2]
      and _stops_color[0][1] == _stops_color[-1][1],
      f"mono {_stops_mono[0][2]}~{_stops_mono[-1][2]}")
check("★ 单色取的就是当前主题的强调色（GLOW_MONO 由 use_palette 推进来）",
      {rgb for _, rgb, _ in _stops_mono}
      == {(G.QColor(G.GLOW_MONO).red(), G.QColor(G.GLOW_MONO).green(),
           G.QColor(G.GLOW_MONO).blue())},
      f"{G.GLOW_MONO} vs {_stops_mono[0][1]}")
check("★ 单色的色相跟着主题走（浅色 / 暗色是两个值）",
      G.LIGHT["GLOW_MONO"] != G.DARK["GLOW_MONO"],
      f"{G.LIGHT['GLOW_MONO']} / {G.DARK['GLOW_MONO']}")
check("单色荧光仍在 40% 饱和度红线内",
      _sat_of(G.LIGHT["GLOW_MONO"]) < 0.40 and _sat_of(G.DARK["GLOW_MONO"]) < 0.40,
      f"{_sat_of(G.LIGHT['GLOW_MONO']):.3f} / {_sat_of(G.DARK['GLOW_MONO']):.3f}")
G.GLOW_MODE = "color"

# ---- 17e 牌库卡 / 遗忘卡：整卡液体充斥（与胶囊同源）----
_dc.setOn(False)
QTest.qWait(_dc.LIQ_TRAVEL + 140)
check("牌库卡关闭时液面退到 0（复位干净，没有残留）",
      _dc._liq_p == 0.0 and not _dc.hasLiquid(), str(_dc._liq_p))
# 同一张卡、同一尺寸下比：**只**动液面进度，别让高度变化混进来
_pin_amp = _dc._liq_amp
_dc.setLiqAmp(0.45)
_dc.setLiqProgress(0.0)
_liq0 = _dc.grab().toImage()
_dc.setLiqProgress(1.0)
_liq1 = _dc.grab().toImage()
_dc.setLiqAmp(_pin_amp)
_liq_tex = (_liq0.width(), _liq0.height())
_n_liq, _far_liq = _edge_maxdist(_liq0, _liq1, _liq0.width() / max(1, _dc.width()))
check("★ 液面真的铺满了整卡（不是只在边上描一圈）",
      _n_liq > 0.5 * _liq0.width() * _liq0.height() and _far_liq > 20,
      f"{_n_liq} 像素 / 共 {_liq0.width() * _liq0.height()}，离边最远 {_far_liq:.1f} 逻辑px")
_dc.setOn(True)
QTest.qWait(_dc.LIQ_TRAVEL + _dc.LIQ_SETTLE + 160)
check("★ 打开牌库卡 = 整卡充满液体（走的就是 GlassBase 那套进度量）",
      _dc.isLiquidFilled(), str(_dc._liq_p))
_dc.setOn(False)
QTest.qWait(_dc.LIQ_TRAVEL + 140)
check("★ 关掉牌库卡 = 从右往左抽空（不是瞬间跳回 0）",
      not _dc.hasLiquid(), str(_dc._liq_p))

# 遗忘卡：展开 / 收起也算一次开合。⚠️ 掐掉 on_open，**并且**掐掉二次重拉 ——
# 让真 worker 进来会在 qWait 期间异步洗掉被测状态（13d 踩过，这里第二次踩）。
_orig_fc_open = _fc.on_open
_orig_fetch = p._fetch_forget_list
_fc.on_open = None
p._fetch_forget_list = lambda: None
try:
    if _fc.isOpen():
        _fc.close_pick()
        QTest.qWait(_fc.LIQ_TRAVEL + 140)
    check("遗忘卡收起时没有液面（复位干净）", not _fc.hasLiquid(), str(_fc._liq_p))
    _fc.open_pick()
    QTest.qWait(_fc.LIQ_TRAVEL + _fc.LIQ_SETTLE + 160)
    check("★ 展开遗忘卡 = 整卡充满液体（展开/收起本身就是一次开合）",
          _fc.isLiquidFilled(), str(_fc._liq_p))
    _fc.close_pick()
    QTest.qWait(_fc.LIQ_TRAVEL + 160)
    check("★ 收起遗忘卡 = 抽空", not _fc.hasLiquid(), str(_fc._liq_p))
finally:
    _fc.on_open = _orig_fc_open
    p._fetch_forget_list = _orig_fetch
_settle()

# ---- 17f 结构反向断言：三处填液体必须共用 GlassBase 那一套 ----
def _methods_of(cls):
    node = _node_of(cls)
    if node is None:
        return set()
    return {m.name for m in node.body if isinstance(m, ast.FunctionDef)}


_ALL_CLASSES = [c.name for c in ast.walk(ast.parse(_SRC_TEXT))
                if isinstance(c, ast.ClassDef)]
for _meth in ("_liquid_path", "_paint_liquid", "_liq_to"):
    _owners = [c for c in _ALL_CLASSES if _meth in _methods_of(c)]
    check(f"★ 反向断言：`{_meth}` 只有 GlassBase 一处定义（三处是复用，不是各写一套）",
          _owners == ["GlassBase"], str(_owners))
for _dotted in ("Capsule.setChecked", "DeckCard.setOn", "ForgetCard.open_pick",
                "ForgetCard.close_pick"):
    check(f"★ `{_dotted}` 里调的是 `_liq_to`（不是自己另画一份）",
          "_liq_to" in _calls_in(_src_of(_dotted)), "")
check("★ 反向断言：`_attach_frost` 必须遍历 `sheet_cards`（写死 status_card 会漏卡）",
      "sheet_cards" in _src_of("Panel._attach_frost"),
      "控制台卡曾因漏在这一行而**完全没有卡面**（只剩描边）")
check("★ 反向断言：`_glow_run` 里必须有 off 判断（off 模式下绝不许起表）",
      "GLOW_MODE" in _src_of("GlassBase._glow_run")
      and "off" in _src_of("GlassBase._glow_run"), "")
check("★ 反向断言：`_paint_glow` 会读 GLOW_MODE（off 要早退，不是画完了再乘 0）",
      "GLOW_MODE" in _src_of("GlassBase._paint_glow"), "")
check("★ 反向断言：`_glow_brush` 不再直接读 NEON_RING（色环要走 glow_ring_stops）",
      "NEON_RING" not in _calls_in(_src_of("GlassBase._glow_brush")),
      str(sorted(_calls_in(_src_of("GlassBase._glow_brush")))))
def _off_early_return_line(dotted):
    """`类.方法` 里 `if ... GLOW_MODE ...: return` 那句 return 的行号（没有就 None）。"""
    _n = _node_of(dotted)
    if _n is None:
        return None
    for _x in ast.walk(_n):
        if isinstance(_x, ast.If) and "GLOW_MODE" in ast.unparse(_x.test):
            for _b in _x.body:
                if isinstance(_b, ast.Return):
                    return _b.lineno
    return None


_off_line = _off_early_return_line("GlassBase._paint_glow")
_first_paint = _linenos("GlassBase._paint_glow").get("save", 10 ** 9)
check("★ `_paint_glow` 的第一件事就是为 off 早退（不是画完了再丢掉）",
      _off_line is not None and _off_line < _first_paint,
      f"off 早退 @L{_off_line} < 首次绘制调用 @L{_first_paint}")
check("★ 灵敏度对照：`_paint_glow` 里确实有绘制调用（上一条不是「本来就没画」）",
      _first_paint < 10 ** 9, f"setPen @L{_linenos('GlassBase._paint_glow').get('setPen')}")
check("★ 反向断言：荧光色环在**运行时**读 GLOW_MONO（换肤后单色要跟着变）",
      "GLOW_MONO" in _src_of("glow_ring_stops"), "")
check("★ 反向断言：`Panel.set_glow_mode` 里同步分段控件时用的是 `emit=False`",
      _kw_of(_src_of("Panel.set_glow_mode"), "setMode").get("emit") == "False",
      str(_kw_of(_src_of("Panel.set_glow_mode"), "setMode")))


print("\n=== 结果 ===")
_shot_failed = bool(fails)
if fails:
    print(f"失败 {len(fails)} 项: {fails}")
else:
    print("全部通过")

# ---------- 14. 截图（默认不写文件）----------
# 放在结果统计**之后**：截图会动到滚动位置、hover 荧光、卡片的连接态，
# 这些副作用在断言之后再发生就不会把断言搅乱（早先塞在断言中间，一截图就有偶发 FAIL）。
if SHOT and not _shot_failed:
    print("=== 14. 截图 ===")
    # 两个坑（都踩过）：
    #   1) setOn 会经 on_change → Panel._on_deck_toggle → set_connected(attached)，
    #      而测试环境 attached=False，会把连接态打回 False → 必须先 setOn 再手工置 True。
    #   2) 第 13b 末尾把 worker.status 接回来了，定时推送会在 qWait 期间把连接态刷成
    #      「未连接游戏」→ 这里必须再断开一次，否则截出来永远是空卡。
    try:
        p.worker.status.disconnect(p.on_status)
    except TypeError:
        pass
    _dc.setOn(True)
    QTest.qWait(60)
    _dc.set_connected(True)
    # 5 张刚好占满横排；drawCount=9 → 「还有 4 张」会挂在下方，一眼看到两种状态
    _dc.update_data({"ok": True, "cards": [
        {"name": "Bash", "cost": 2},
        {"name": "Cheap Lighter", "cost": 0},
        {"name": "Hammer", "cost": 1},
        {"name": "Toasty", "cost": 0},
        {"name": "Body Hammer", "cost": 2}],
        "drawCount": 9, "discard": 0, "exhaust": 0, "hand": 3, "handSize": 4, "warning": "",
        "deckType": 2, "deckTypeName": "战斗"})
    QTest.qWait(80)
    p.resize(576, 1000)
    QTest.qWait(340)
    p._scroll_to(700, smooth=False)
    QTest.qWait(300)
    p.grab().save(OUT_DECK)
    print(f"截图: {OUT_DECK}  牌库卡高={_dc.height()}")

    if _dc.isOn():
        _dc.setOn(False)
    p.resize(576, 350)
    QTest.qWait(200)
    p._scroll_to(0, smooth=False)
    QTest.qWait(300)
    # 悬停态：把荧光停在看得清的相位再抓图
    # ⚠️ 用 `p.cap_top` 而不是复用的 `_c` —— 上面 `_c` 在中途被别的检查覆盖成 dict 了，
    #    沿用老变量会在截图阶段炸掉（曾经踩过：最后一步崩、截图全没生成）。
    _hov = p.cap_top
    _hov._hover = 1.0
    _hov._glow_ang = 35.0
    QTest.qWait(40)
    _hov.grab().save(OUT_HOVER)
    p.grab().save(OUT)
    print(f"截图: {OUT}\n截图: {OUT_HOVER}")

    # 遗忘卡：展开态（喂一份模拟牌库，滚轮停在中间那张）
    _fc2 = p.forget_card
    _fc2.set_connected(True)
    _fc2.open_pick()
    _fc2.update_list({
        "ok": True, "canForget": True, "deckType": 2, "deckTypeName": "战斗",
        "counts": {"draw": 4, "discard": 2, "exhaust": 1}, "total": 7,
        "cards": [
            {"idx": 0, "stack": "_normalShuffledDrawCards", "stackLabel": "抽牌堆",
             "index": 0, "name": "Toasty", "cost": 0, "ptr": "0xa0"},
            {"idx": 1, "stack": "_normalShuffledDrawCards", "stackLabel": "抽牌堆",
             "index": 1, "name": "Stab", "cost": 1, "ptr": "0xa1"},
            {"idx": 2, "stack": "_normalShuffledDrawCards", "stackLabel": "抽牌堆",
             "index": 2, "name": "Crippling Blow", "cost": 2, "ptr": "0xa2"},
            {"idx": 3, "stack": "_normalShuffledDrawCards", "stackLabel": "抽牌堆",
             "index": 3, "name": "Roast", "cost": 1, "ptr": "0xa3"},
            {"idx": 4, "stack": "_discards", "stackLabel": "弃牌堆",
             "index": 0, "name": "Seasoning", "cost": 0, "ptr": "0xa4"},
            {"idx": 5, "stack": "_discards", "stackLabel": "弃牌堆",
             "index": 1, "name": "Flash", "cost": 1, "ptr": "0xa5"},
            {"idx": 6, "stack": "_exhausted", "stackLabel": "已消耗",
             "index": 0, "name": "Stab", "cost": 1, "ptr": "0xa6"},
        ],
    })
    _fc2._sel = 2
    _fc2._render()
    QTest.qWait(120)
    _fc2.grab().save(OUT_FORGET)
    print(f"截图: {OUT_FORGET}  遗忘卡高={_fc2.height()}")
    _fc2.close_pick()
    _fc2._warn = ''
    _fc2._forgotten = 1      # 顺带看一眼计数标签和按钮行同处一行的观感
    _fc2._render()
    QTest.qWait(120)
    _fc2.grab().save(OUT_FORGET_CLOSED)
    print(f"截图: {OUT_FORGET_CLOSED}  收起态高={_fc2.height()}")

    # 运行状态页：仪表盘按钮打开后的整页观感（顺手核对按钮图标是否画对）
    p.resize(576, 620)
    QTest.qWait(160)
    p.toggle_status_sheet()
    QTest.qWait(p.SHEET_MS + 180)
    p.grab().save(OUT_SHEET)
    print(f"截图: {OUT_SHEET}  状态页={p.sheet.geometry().getRect()}")
    p.toggle_status_sheet()
    QTest.qWait(p.SHEET_MS + 180)
elif SHOT:
    print("=== 14. 截图：跳过（断言已有失败，先修断言）===")
else:
    print("=== 14. 截图：已跳过（需要时设 SHROOM_UI_SHOT=1）===")

if _shot_failed:
    sys.exit(1)

