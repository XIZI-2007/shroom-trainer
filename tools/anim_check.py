"""真机**屏幕**逐帧核对四个动画：① 状态页开页「卡片自己展开」② 主题切换「圆形扩散」
③ 让位「卡片变高时下位卡片自动让开」④ 仪表盘「指针转一整圈回到原位」。

为什么不能只靠 `ui_check.py`：那边用 `p.grab()` 把控件树同步渲染进一张离屏图，
走的是 `render()` 那条路 —— **屏幕合成分辨不出来**。这条工具量的是"用户眼睛看到的"。

它做什么：
  · 起一个真实窗口（576×350，与用户报问题时一致：那是默认的最小高度）
  · 用 PostMessage 往顶层 HWND 投真实的 WM_LBUTTONDOWN/UP 点按钮
    —— **不动用户的光标**（这点和 `focus_check.py` 不一样，那个会挪真光标）
  · 阶段一（仪表盘按钮 → 状态页）：断言展开全程卡片都是「面板 x = pad、宽度
    0 → 满宽、右缘离页面前沿 pad」
  · 阶段二（右上角开关 → 换主题）：断言**从屏幕**抓到的中途帧里新旧主题同时存在
    （圆内已变暗 / 圆外还是浅的）、半径单调增长、收尾整窗变暗且遮罩已撤
  · 阶段三（喂牌库数据 → 牌库卡长高）：把检查挂在**真实 Paint 事件**上数
    「被画到屏幕上的、卡片互相重叠」的帧数 —— 必须是 0。
    这一条是踩出来的：`sync_height()` 里先 `setFixedHeight` 再回调，于是从那一句到
    让位补间跑起来之间，存在一帧「上面那张已经变高、下面那张还停在旧位置」。
    数出来的 6 帧**都是真的画上了屏的**（不是理论推演），所以必须在真窗口这层守。
  · 阶段四（点仪表盘 → 指针转一圈）：从屏幕帧里**直接读出指针指向**（圆环内最暗
    像素的方位角，那个环里除了指针没有别的墨），断言：起止都在 55°（原位）、中途
    确实扫到过背面。为什么整窗/按钮区的像素差异在这里不管用：点一下会同时改变
    按钮的 `_on` 底色，像素差没法归因给指针 —— 只有读出**角度**才是干净证据。
  · `SHROOM_UI_SHOT=1` 时输出胶片 `tools/anim_shot.png`（开页）、
    `tools/anim_theme_shot.png`（换肤）供肉眼核对

⚠️ 开头装了 `sys.excepthook`：Qt 槽里的未捕获 Python 异常会被 PyQt 直接 `abort()`
   （退出码 127，日志里看不到任何 traceback），只能靠猜。装个会冲刷的钩子把真错打出来。

退出码 0 = 全绿。窗口只在运行期间存在（约 6 秒）。
"""
import ctypes
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.pop("SHROOM_AUTO_ATTACH", None)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QFont, QImage, QPainter, QColor
from PyQt6.QtCore import QTimer, QPoint, QRect, QRectF, QObject, QEvent

import trainer_gui as G


def _excepthook(t, v, tb):
    """⚠️ Qt 槽里的未捕获 Python 异常会被 PyQt 直接 `abort()`（退出码 127、日志里
    **看不到任何 traceback**），排查时只能靠猜。装一个会冲刷的钩子，把真错打出来。
    （2026-09-27 踩过：`ThemeButton` 没有 `setOn`，一个 AttributeError 表现成"原生崩溃"，
    白跑了一轮二分定位。）"""
    import traceback
    traceback.print_exception(t, v, tb)
    sys.stderr.flush()


sys.excepthook = _excepthook

WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0201, 0x0202, 0x0001
u32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.environ.get("SHROOM_UI_SHOT") == "1"
PAD = 8
# 主题断言的两个取样点（**面板逻辑坐标**）：都挑纯页面底，避开卡片和底部按钮
PT_IN = (400, 31)        # 头行的伸缩空档 —— 圆心在 (541,31)，这点距圆心 141px
PT_OUT = (10, 340)       # 左下角留白 —— 距圆心 ≈614px

fails = []


def check(name, ok, info=""):
    print(("  [OK]  " if ok else "  [FAIL]") + f" {name} {info}")
    if not ok:
        fails.append(name)


def film(frames, name, cols=4, sw=320, sh=195):
    """把逐帧图拼成一张胶片，供肉眼核对。"""
    rows = (len(frames) + cols - 1) // cols
    out = QImage(sw * cols, sh * rows, QImage.Format.Format_RGB32)
    out.fill(QColor("#101418"))
    pain = QPainter(out)
    for i, (t, im) in enumerate(frames):
        x, y = (i % cols) * sw, (i // cols) * sh
        pain.drawImage(QRectF(x, y, sw, sh), im, QRectF(0, 0, im.width(), im.height()))
        pain.setPen(QColor("#ffffff"))
        pain.drawText(x + 6, y + 15, f"{t:.0f}ms")
    pain.end()
    dst = os.path.join(HERE, name)
    out.save(dst)
    print(f"\n胶片: {dst}（{len(frames)} 帧）")


app = QApplication(sys.argv)
app.setStyleSheet(G.QSS)
app.setFont(QFont("Microsoft YaHei UI", 9))

p = G.Panel()
p._fetch_forget_list = lambda: None
p.move(120, 80)
p.show()
QTest.qWait(700)
p.resize(576, 350)
QTest.qWait(300)

hwnd = int(p.winId())
dpr = p.devicePixelRatioF()
bc = p.b_dash.mapTo(p, QPoint(p.b_dash.width() // 2, p.b_dash.height() // 2))
scr = app.primaryScreen()
full = p._sheet_rect().width() - PAD * 2
print(f"面板 {p.width()}x{p.height()}  DPR={dpr}  仪表盘按钮中心=({bc.x()},{bc.y()})  "
      f"卡片满宽={full}")

frames = []
samples = []
t0 = [0.0]


def px_of(im, lx, ly):
    """从**屏幕抓到的整窗图**里取某面板逻辑坐标处的颜色。

    按 `im.width()/p.width()` 折算，别写死 dpr —— 抓到的图可能带窗口边框
    （本面板是无边框窗，实测 1:1，但这个折算方式换成有边框主题也不会偏）。
    """
    k = im.width() / max(1, p.width())
    x = int(min(im.width() - 1, max(0, lx * k)))
    y = int(min(im.height() - 1, max(0, ly * k)))
    return im.pixelColor(x, y)


def sample():
    t = (time.perf_counter() - t0[0]) * 1000.0
    if t > 420:
        finish_sheet()
        return
    pm = scr.grabWindow(hwnd)
    if not pm.isNull():
        frames.append((t, pm.toImage()))
    if p.isSheetOpen():
        samples.append((t,
                        p.status_card.mapTo(p, QPoint(0, 0)).x(),
                        p.status_card.width(),
                        p.sheet.x() + p.sheet.width(),
                        # 控制台卡：页内第二张，必须和上面那张**同步**展开
                        p.console_card.width(),
                        p.console_card.mapTo(p, QPoint(0, 0)).x()))
    QTimer.singleShot(4, sample)


def finish_sheet():
    print(f"\n--- 阶段一：开页 ---\n抓到 {len(frames)} 帧屏幕图，{len(samples)} 个动画采样")
    if samples:
        print(f"{'t(ms)':>7} {'卡面板x':>8} {'卡宽':>7} {'页面前沿':>9} {'右缘余量':>9} {'控制台宽':>8}")
        for t, cx, cw, front, ccw, _ccx in samples[::2]:
            print(f"{t:7.0f} {cx:8d} {cw:7d} {front:9d} {front - (cx + cw):9d} {ccw:8d}")

    mid = [s for s in samples if 0 < s[2] < full]
    check("真的抓到了展开过程的中间帧（否则下面几条什么也没测到）",
          len(mid) >= 3, f"中间帧 {len(mid)} 个")
    if mid:
        check("★ 展开全程卡片左边都钉在 pad 上（面板 x 恒为 pad）",
              all(abs(s[1] - PAD) <= 1 for s in mid),
              f"面板 x 取值 {sorted({s[1] for s in mid})}")
        check("★ 展开全程卡片右缘离页面前沿留出 pad（卡片始终在页面内部，右角不会被裁）",
              all(abs((s[3] - (s[1] + s[2])) - PAD) <= 1 for s in mid),
              f"余量取值 {sorted({s[3] - (s[1] + s[2]) for s in mid})}")
        check("★ 宽度是单调展开的（没有回缩、没有跳变）",
              all(b[2] >= a[2] for a, b in zip(mid, mid[1:])),
              f"{[s[2] for s in mid]}")
        # 控制台卡是这一轮新加的页内第二张卡：**必须和上面那张同帧同宽** ——
        # 两张各跑各的展开动画（或者漏了它）会一眼看出「一张在长、另一张还是窄条」。
        check("★ 两张页内卡是**同步**展开的（逐帧宽度一致，没有谁慢一拍）",
              all(abs(s[2] - s[4]) <= 1 for s in mid),
              f"最大逐帧差 {max(abs(s[2] - s[4]) for s in mid)}px")
        check("★ 控制台卡的左缘也钉在 pad 上（和状态卡同一套展开算法）",
              all(abs(s[5] - PAD) <= 1 for s in mid),
              f"面板 x 取值 {sorted({s[5] for s in mid})}")
    check("★ 收尾后两张卡都是满宽（展开只是临时的，不能留窄卡）",
          p.status_card.width() == full and p.console_card.width() == full,
          f"状态卡 {p.status_card.width()} / 控制台 {p.console_card.width()} vs {full}")
    if SHOT and frames:
        film(frames, "anim_shot.png")
    # 状态页直接收起（不走动画）：主题那一段的取样点要落在**页面底**上，
    # 状态页盖着的话取样点全在它上面，"圆外还是浅色"就量不出来了。
    p.sheet.hide()
    p._sheet_open = False
    p.b_dash.setOn(False)
    QTest.qWait(120)
    QTimer.singleShot(120, start_theme)


# ---------------- 阶段二：圆形扩散换主题 ----------------
tframes = []
tsamples = []
tt0 = [0.0]


def tsample():
    t = (time.perf_counter() - tt0[0]) * 1000.0
    if t > 560:
        finish_theme()
        return
    pm = scr.grabWindow(hwnd)
    if not pm.isNull():
        im = pm.toImage()
        tframes.append((t, im))
        tsamples.append((t, p.reveal._r, p.reveal.isVisible(), p._theme,
                         px_of(im, *PT_IN).lightness(), px_of(im, *PT_OUT).lightness()))
    QTimer.singleShot(4, tsample)


def start_theme():
    global hwnd, dpr
    tc = p.b_theme.mapTo(p, QPoint(p.b_theme.width() // 2, p.b_theme.height() // 2))
    print(f"\n--- 阶段二：换主题 ---\n开关中心(面板逻辑)=({tc.x()},{tc.y()})  开始主题={p._theme}")
    lp = (int(tc.y() * dpr) << 16) | (int(tc.x() * dpr) & 0xFFFF)
    u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    QTimer.singleShot(30, lambda: u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp))
    tt0[0] = time.perf_counter()
    QTimer.singleShot(0, tsample)


def finish_theme():
    print(f"抓到 {len(tframes)} 帧屏幕图，{len(tsamples)} 个采样")
    print(f"{'t(ms)':>7} {'半径':>7} {'遮罩':>6} {'主题':>6} {'圆内L':>6} {'圆外L':>6}")
    for t, r, vis, th, lin, lout in tsamples[::3]:
        print(f"{t:7.0f} {r:7.0f} {str(vis):>6} {th:>6} {lin:6d} {lout:6d}")

    mid = [s for s in tsamples if s[2] and 0 < s[1] < 0.98 * p.reveal._rmax]
    check("真的抓到了扩散过程的中间帧（否则下面几条什么也没测到）",
          len(mid) >= 3, f"中间帧 {len(mid)} 个")
    if mid:
        check("★ 半径单调增长（没有回缩、没有跳变）",
              all(b[1] >= a[1] for a, b in zip(mid, mid[1:])),
              f"{[round(s[1]) for s in mid[::2]]}")
        # 这是整条动画的核心：屏幕合成出来的那一帧里，**圆内已经变暗、圆外还是浅的**。
        # 要是遮罩根本没生效（整窗一起变色 / 一直是旧色），这两个数就会同向，这里必红。
        both = [s for s in mid if s[4] < 110 and s[5] > 200]
        check("★ 屏幕合成出来的中途帧里新旧主题**同时存在**（圆内已暗、圆外还浅）",
              len(both) >= 3,
              f"合格帧 {len(both)}/{len(mid)}；样例 "
              f"{[(round(s[1]), s[4], s[5]) for s in both[:3]]}")
        check("★ 灵敏度对照：扩散刚开始时圆外必须还是浅色（否则等于整窗一起变色）",
              mid[0][5] > 200, f"第一帧圆外 L={mid[0][5]}")
    last = tsamples[-1] if tsamples else None
    check("★ 收尾（屏幕）：遮罩已撤、整窗变暗、开关进入黑夜态",
          (not p.reveal.isVisible()) and p._theme == "dark"
          and (last is None or last[5] < 110),
          f"可见={p.reveal.isVisible()} theme={p._theme} "
          f"左下角L={last[5] if last else '?'}")

    if SHOT and tframes:
        film(tframes, "anim_theme_shot.png")

    start_reflow()


# ---------------- 阶段三：让位（卡片变高 → 下位卡片让开） ----------------
PAINTED = []          # 被真实画上屏、且当时卡片互相重叠的帧
rframes = []
rrows = []


class _ReflowWatch(QObject):
    """挂在**真实 Paint 事件**上数重叠帧。

    为什么不用定时采样：定时器可能正好落在两次绘制之间，采不到那一帧；
    挂 Paint 则是"只要这帧会被画出来，就一定被检查到"。
    """

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            rows = [(type(c).__name__, c.y(), c.height()) for c in p.scroll_items]
            for a, b in zip(rows, rows[1:]):
                if b[1] < a[1] + a[2]:
                    PAINTED.append((obj.__class__.__name__, a, b,
                                    a[1] + a[2] - b[1]))
        return False


def _settle(limit=80):
    for _ in range(limit):
        if p._reflow_anim is None and not p._reflow_timer.isActive():
            return
        QTest.qWait(16)


def _feed(n):
    d = {"ok": True, "discard": 3, "exhaust": 2, "hand": 5, "handSize": 4,
         "warning": "", "deckType": 2, "deckTypeName": "战斗",
         "drawCount": n, "cards": [{"name": f"Card {i}", "cost": 1} for i in range(n)]}
    p.deck_card.update_data(d)


def start_reflow():
    global hwnd
    print("\n--- 阶段三：让位（牌库卡吃数据后长高，下面的遗忘卡让开）---")
    # ⚠️ 真 worker 每 450ms 推一次状态，`on_status` 会按 attached(False) 把牌库卡
    #    打回「未连接」→ 卡片缩回 70，这一节就永远等不到变高（ui_check 里踩过同一坑）。
    try:
        p.worker.status.disconnect(p.on_status)
    except TypeError:
        pass
    _watch = _ReflowWatch()
    p.installEventFilter(_watch)
    for c in p.scroll_items:
        c.installEventFilter(_watch)

    # 窗口拉高一点，让牌库卡和遗忘卡同屏可见
    p.resize(576, 560)
    QTest.qWait(260)
    hwnd = int(p.winId())
    p.deck_card.setOn(True)
    p.deck_card.set_connected(True)
    _feed(3)
    _settle()
    y0 = p.forget_card.y()
    h0 = p.deck_card.height()
    _feed(10)                      # 10 张 → 多出「还有 5 张」那一行 → 变高
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.32:
        pm = scr.grabWindow(hwnd)
        if not pm.isNull():
            im = pm.toImage()
            rframes.append(im)
            rrows.append((p.deck_card.height(), p.deck_card.y(), p.forget_card.y()))
        QTest.qWait(22)
    _settle()

    dy = p.forget_card.y() - y0
    dh = p.deck_card.height() - h0
    print(f"牌库卡 {h0} → {p.deck_card.height()}（Δ{dh}）  遗忘卡 y {y0} → "
          f"{p.forget_card.y()}（Δ{dy}）")
    print(f"屏幕帧 {len(rframes)} 张；被画出来的重叠帧 {len(PAINTED)} 个")
    for h, y, fy in rrows:
        print(f"  牌库卡 h={h:4d} y={y:4d}   遗忘卡 y={fy:4d}  间距={fy - (y + h):3d}")

    check("★ 前提：这一节真的把牌库卡喂高了（否则下面全是空转）", dh > 0,
          f"Δh={dh}")
    check("★ **被画到屏幕上的每一帧**里卡片都不重叠（挂在真实 Paint 上数的）",
          not PAINTED, f"{len(PAINTED)} 个重叠帧，前 3 个={PAINTED[:3]}")
    check("★ 从屏幕抓到了补间的中间帧", len(rframes) >= 4, f"{len(rframes)} 帧")
    if rrows:
        check("★ 途中下卡一直被推开、从不回弹（屏幕采样同步取几何）",
              all(b[2] >= a[2] - 1 for a, b in zip(rrows, rrows[1:])),
              str([r[2] for r in rrows]))
    check("★ 让开量正好等于上面那张的增高量（Δy == Δh，多让一点就是浪费空间）",
          dy == dh and dh > 0, f"Δy={dy} Δh={dh}")
    rows = [(type(c).__name__, c.y(), c.height()) for c in p.scroll_items]
    check("★ 收尾几何零重叠、间距仍是 CARD_GAP",
          all(b[1] >= a[1] + a[2] for a, b in zip(rows, rows[1:]))
          and all(b[1] - (a[1] + a[2]) == p.CARD_GAP for a, b in zip(rows, rows[1:])),
          str(rows))

    if SHOT and rframes:
        film([(i * 22.0, im) for i, im in enumerate(rframes)], "anim_reflow_shot.png")

    QTimer.singleShot(80, start_needle)


# ---------------- 阶段四：指针转一圈（从屏幕帧里读角度） ----------------
# 关键点：**不能靠像素差异**。点一下同时会改按钮的 `_on` 底色（那个圈会亮起来），
# 于是"两张图不一样"既可能是指针转了、也可能只是底色变了，归因不了。
# 改成一个干净的测量：指针是圆环内的**唯一**墨（表盘弧在更外面 7.5 逻辑像素处、
# 轴点在里面 1.7 处），所以在半径 [2.2, 5.3] 的环里找最暗的像素，它的方位角
# **就是指针指向**。逐帧读这个角度，就能在真机屏幕上直接验"转了一圈回到原位"。
ND_R0, ND_R1 = 2.2, 5.3          # 环的内外半径（逻辑像素），避开轴点和表盘弧
nfr = []
nt0 = [0.0]


def _needle_angle(im):
    """屏幕帧里指针的指向（度，0 = 3 点钟方向、逆时针为正）。返回 (角度, 偏离量)。

    ⚠️ **不能找「最暗的像素」**：黑夜主题下指针比页面底还亮（底色 #17191C 亮度 76、
    指针 #9BA1A8 亮度 484），"最暗点"会落到空气上、方位角就是噪声。
    改成「先取环内众数当底色，再取**偏离底色最远**的像素」—— 环里除了底色就只有
    指针（轴点在内 1.7、表盘弧在外 7.5，都被环挡在外面），所以它在两个主题下都成立。
    """
    k = im.width() / max(1, p.width())
    cx, cy = bc.x() * k, bc.y() * k
    r0, r1 = ND_R0 * k, ND_R1 * k
    pix, cnt = [], {}
    for yy in range(max(0, int(cy - r1)), min(im.height(), int(cy + r1) + 2)):
        for xx in range(max(0, int(cx - r1)), min(im.width(), int(cx + r1) + 2)):
            if not (r0 <= math.hypot(xx - cx, yy - cy) <= r1):
                continue
            v = im.pixel(xx, yy)
            cnt[v] = cnt.get(v, 0) + 1
            pix.append((xx, yy, v))
    if not pix:
        return None, -1
    bg = QColor(max(cnt, key=cnt.get))

    def _dev(v):
        c = QColor(v)
        return (abs(c.red() - bg.red()) + abs(c.green() - bg.green())
                + abs(c.blue() - bg.blue()))

    xx, yy, v = max(pix, key=lambda t: _dev(t[2]))
    # ⚠️ 方位角用 `cy - y`：Qt 的 y 轴朝下，翻过来才是数学上的"逆时针为正"。
    return math.degrees(math.atan2(cy - yy, xx - cx)) % 360.0, _dev(v)


def start_needle():
    global hwnd, dpr, bc
    p.resize(576, 350)
    QTest.qWait(320)
    hwnd = int(p.winId())
    dpr = p.devicePixelRatioF()
    bc = p.b_dash.mapTo(p, QPoint(p.b_dash.width() // 2, p.b_dash.height() // 2))
    print(f"\n--- 阶段四：指针转一圈 ---\n按钮中心=({bc.x()},{bc.y()})  DPR={dpr}  "
          f"状态页开={p.isSheetOpen()}  _spin={p.b_dash._spin}")
    check("前置：这一步之前状态页是关的、指针停在原位",
          (not p.isSheetOpen()) and p.b_dash._spin == 0.0,
          f"open={p.isSheetOpen()} _spin={p.b_dash._spin}")
    pm = scr.grabWindow(hwnd)
    if not pm.isNull():
        nfr.append((-1, pm.toImage(), 0.0))       # 点击**前**的基准帧
    lp = (int(bc.y() * dpr) << 16) | (int(bc.x() * dpr) & 0xFFFF)
    u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    QTimer.singleShot(30, lambda: u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp))
    nt0[0] = time.perf_counter()
    QTimer.singleShot(0, nsample)


def nsample():
    t = (time.perf_counter() - nt0[0]) * 1000.0
    if t > 460:
        finish_needle()
        return
    pm = scr.grabWindow(hwnd)
    if not pm.isNull():
        nfr.append((t, pm.toImage(), p.b_dash._spin))
    QTimer.singleShot(4, nsample)


def finish_needle():
    print(f"抓到 {len(nfr)} 帧屏幕图（含点击前的基准帧）")
    rows = []
    for t, im, sp in nfr:
        ang, dev = _needle_angle(im)
        rows.append((t, sp, ang, dev))
    print(f"{'t(ms)':>7} {'_spin':>7} {'指针角度':>9} {'偏离底色':>9}")
    for t, sp, ang, dev in rows:
        print(f"{t:7.0f} {sp:7.3f} "
              f"{(f'{ang:8.0f}°' if ang is not None else '     ?'):>9} {dev:9d}")

    mid = [(t, sp, ang, dev) for t, sp, ang, dev in rows if ang is not None]
    check("★ 每帧都从环里读到了指针（读不到就说明屏幕合成里根本没有指针）",
          len(mid) == len(rows) and len(rows) >= 5,
          f"{len(mid)}/{len(rows)} 帧读到")
    check("★ 读到的确实是墨而不是一片均匀底色（偏离量够大，两个主题下都成立）",
          all(dev > 80 for _, _, _, dev in mid),
          f"偏离量取值 {sorted({dev for _, _, _, dev in mid})[:6]}")
    base = mid[0][2] if mid else -1
    check("★ 起始帧指针就在原位方向（≈55°）", abs(base - 55.0) <= 14.0,
          f"起始角 {base:.0f}°")
    far = [a for _, _, a, _ in mid
           if abs((a - base + 180) % 360 - 180) >= 60.0]
    check("★ 屏幕中途帧里指针真的扫到了别的方向（不是原地不动 / 只是底色在变）",
          len(far) >= 2, f"偏离起始 ≥60° 的帧 {len(far)} 个，"
                         f"样例 {[round(a) for a in far[:4]]}")
    check("★ 收尾帧指针回到原位方向（≈55°，±14°）—— 屏幕上看到的就是「转一圈回到原位」",
          abs(mid[-1][2] - 55.0) <= 14.0, f"收尾角 {mid[-1][2]:.0f}°")
    check("★ 收尾时 `_spin` 已归零（过渡量不残留，指针不会停在半圈上）",
          p.b_dash._spin == 0.0, str(p.b_dash._spin))

    # 再点一次（收回页面）：指针倒着转一圈 → 按钮整体应当回到**点击前那一帧**的样子
    # （同样的 `_on` 底色、同样的指针角度）。这条把"回到原位"钉在真机屏幕上。
    lp = (int(bc.y() * dpr) << 16) | (int(bc.x() * dpr) & 0xFFFF)
    u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    QTimer.singleShot(30, lambda: u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp))
    QTest.qWait(520)
    pm = scr.grabWindow(hwnd)
    diff = -1
    if (not pm.isNull()) and nfr and nfr[0][0] == -1:
        a, b = nfr[0][1], pm.toImage()
        diff = sum(1 for y in range(min(a.height(), b.height()))
                   for x in range(min(a.width(), b.width()))
                   if a.pixel(x, y) != b.pixel(x, y))
    check("★ 一开一合之后，按钮真的回到了**点击前那一帧**的样子（指针 + 底色一起复位）",
          diff == 0, f"差异 {diff} 像素（基准帧 vs 收尾帧）")
    check("收尾：状态页已关、指针在原位",
          (not p.isSheetOpen()) and p.b_dash._spin == 0.0 and not p.sheet.isVisible(),
          f"open={p.isSheetOpen()} _spin={p.b_dash._spin}")

    print("\n=== 结果 ===")
    print(f"失败 {len(fails)} 项: {fails}" if fails else "全部通过")
    app.exit(1 if fails else 0)



QTimer.singleShot(250, lambda: (
    u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON,
                     (int(bc.y() * dpr) << 16) | (int(bc.x() * dpr) & 0xFFFF)),
    QTimer.singleShot(30, lambda: u32.PostMessageW(
        hwnd, WM_LBUTTONUP, 0,
        (int(bc.y() * dpr) << 16) | (int(bc.x() * dpr) & 0xFFFF))),
    t0.__setitem__(0, time.perf_counter()),
    QTimer.singleShot(0, sample)))
sys.exit(app.exec())
