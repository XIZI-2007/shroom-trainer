# -*- mode: python ; coding: utf-8 -*-
#
# 体积优化原则（用户 2026-10-01 要求：**不影响功能与性能** 前提下压体积）
#   1. 只减"代码零使用的资源"，绝不改功能代码，也不动算法依赖（numpy 必须留 ——
#      模糊算法靠 np.cumsum；换纯 Python 会明显拖慢，违反"不影响性能"）。
#   2. excludes 只列环境里装了但本程序绝不 import 的重量级包（opencv/IPython/UnityPy/
#      androguard 等），防间接 import 链把它们拖进来。
#   3. 资源级裁剪见下方 DROP_BASENAMES / DROP_PATH_PARTS —— 每条都写明依据（代码审查结论）。
#   4. frida/_frida.pyd 126MB 是官方 wheel 的 frida-core 单体，**裁不了**（自己编不现实），
#      collect_all 只顺手剥掉它的类型存根(_frida.pyi / py.typed)与 asyncio 封装(aio.py)。
#
# ⚠️ 裁剪后必须跑 tools/exe_smoke.py（真机黑盒）确认窗口、置顶、点击、截图断言全绿。
import os
from PyInstaller.utils.hooks import collect_all

datas = [
    ('src/agent.js', '.'),
    ('src/i18n_cards.json', '.'),
    # ---- 卡牌效果理解层（第 61 轮）----
    # ⚠️ 这四个是**离线生成的静态数据**，intent_reader.py 直接读，不改功能代码。
    #    游戏更新后要重跑 tools/{intent_meta,intent_build,intent_dict_build,
    #    term_glossary_build,intent_semantics_build}.py **按此顺序**并重新打包。
    #    ⚠️ intent_meta 必须第一个跑（enum 是 dict/semantics 的输入）。
    ('src/card_intent_dict.json', '.'),    # 意图 → 中英文案模板
    ('src/card_intent_enum.json', '.'),    # 173 个权威成员（含 2 个运行时独有，校验用）
    ('src/intent_semantics.json', '.'),    # 意图 → 类别/口径/目标/得失/场景
    ('src/term_glossary.json', '.'),       # 术语词表 + 意图一句话释义
    # ---- 敌人状态（第 63 轮）----
    # ⚠️ battle_planner.py 直接读这五张表算状态倍率；**漏打任何一张都会静默降级**
    #    （STATUS_MODEL 为空 ⇒ 所有状态都判 flavor ⇒ 易伤倍率恒 1.0，不报错）。
    #    游戏更新后按此顺序重跑：
    #      status_meta → status_loc_build → status_tip_build
    #      → status_model_build → status_intent_map_build
    #    ⚠️ status_meta 必须第一个（成员表是后四张的输入）。
    ('src/status_effect_enum.json', '.'),  # 68 个权威成员（含 Vulnerable/Poisoned）
    ('src/status_i18n.json', '.'),         # 状态 → 中英名
    ('src/status_tips.json', '.'),         # 状态 → 官方 tooltip（权威效果口径）
    ('src/status_model.json', '.'),        # 状态 → 可计算模型（**人工判读**，见生成器）
    ('src/status_intent_map.json', '.'),   # 意图 op → 状态名（apply/self/note 三分类）
]
binaries = []
hiddenimports = [
    # 数据驱动的纯计算模块，显式声明防被静态分析漏掉
    'intent_reader',
    'battle_planner',
]

_ret = collect_all('frida')


# 剥掉 frida 的类型存根与 asyncio 封装（本项目只用同步 API，运行期零用途）
def _keep_frida(entry):
    name = entry[0] if isinstance(entry, (tuple, list)) else entry
    base = os.path.basename(str(name).replace('\\', '/'))
    return not (base.endswith('.pyi') or base == 'py.typed' or base == 'aio.py')


datas += [d for d in _ret[0] if _keep_frida(d)]
binaries += [b for b in _ret[1] if _keep_frida(b)]
hiddenimports += [h for h in _ret[2] if h != 'frida.aio']

# ---------------------------------------------------------------- 资源级裁剪
# 依据：对 src/trainer_gui.py + src/trainer_core.py 全文检索
#   grep QIcon/QPixmap/QImage/QSvg/QtSvg/QtNetwork/QOpenGL/QNetwork/QUrl/QFileDialog
# 结论：无 QIcon、无 SVG、无网络、无 OpenGL、无 Qt 标准对话框；
#       QImage 全部由内存字节构造（QImage(bytes,...)），不加载任何图片文件。
DROP_BASENAMES = {
    # 软件 OpenGL 回退实现（19.7MB，最大可裁项）—— 全程 QPainter 光栅绘制，不建 OpenGL 上下文
    'opengl32sw.dll',
    # 无 PDF / 网络 / SVG 使用
    'qt6pdf.dll', 'qt6network.dll', 'qt6svg.dll',
    'qsvgicon.dll', 'qsvg.dll', 'qpdf.dll',
    # 不加载外部图片文件（QImage 由内存构造），只留 qico.dll 兜底
    'qjpeg.dll', 'qwebp.dll', 'qtiff.dll', 'qgif.dll', 'qicns.dll',
    'qtga.dll', 'qwbmp.dll',
    # 无触摸屏输入处理
    'qtuiotouchplugin.dll',
}
# 96 个 .qm 共约 6.4MB：界面文案全部硬编码中文，且不使用 Qt 标准对话框 ⇒ 永远不加载翻译
DROP_PATH_PARTS = ('pyqt6/qt6/translations/',)
KEEP_BASENAMES = {'qico.dll'}  # 保留：ico 支持，45KB 的兜底

EXCLUDES = [
    # ---- 环境里存在、本项目绝不使用的重量级包（防间接 import 拖入）----
    'cv2', 'PIL', 'Pillow', 'IPython', 'ipykernel', 'jupyter', 'nbformat', 'nbconvert',
    'matplotlib', 'scipy', 'pandas', 'sklearn', 'skimage', 'sympy', 'torch',
    'UnityPy', 'UnityPyHelper', 'androguard', 'apkInspector', 'apkInspectorCLI',
    'sqlalchemy', 'alembic', 'sounddevice', 'astc_encoder', 'absl', 'google',
    'zmq', 'lxml', 'bs4', 'requests', 'urllib3', 'httpx', 'aiohttp',
    'jsonschema', 'rich', 'pygments', 'jedi', 'parso', 'tornado',
    'yaml', 'ruamel', 'capstone', 'keystone', 'unicorn', 'pyelftools',
    # ---- 标准库中本项目确定用不到的（自绘 GUI，无 CLI/Tk/测试入口）----
    'tkinter', 'unittest', 'doctest', 'pydoc', 'lib2to3', 'idlelib',
    'ensurepip', 'curses', 'turtledemo', 'pdb', 'pydoc_data',
]

a = Analysis(
    ['src/trainer_gui.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=2,          # -OO：剥 docstring/assert，只影响 .pyc 体积
)
pyz = PYZ(a.pure)


def _drop(name):
    n = str(name).replace('\\', '/')
    base = n.rsplit('/', 1)[-1].lower()
    if base in KEEP_BASENAMES:
        return False
    if base in DROP_BASENAMES:
        return True
    low = n.lower()
    return any(part in low for part in DROP_PATH_PARTS)


_before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if not _drop(e[0])]
a.datas = [e for e in a.datas if not _drop(e[0])]
print('[spec] 资源裁剪：%d -> %d 项' % (_before, len(a.binaries) + len(a.datas)))

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ShroomTrainer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
