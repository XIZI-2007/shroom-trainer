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

datas = [('src/agent.js', '.'), ('src/i18n_cards.json', '.')]
binaries = []
hiddenimports = []

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
