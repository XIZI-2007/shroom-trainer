"""Shroom & Gloom 辅助 - Frida 控制器"""
import os
import time
import json
import glob
import ctypes
import ctypes.wintypes as _wt
import frida

GAME_EXE = "Shroom and Gloom.exe"
GAME_DIR_DEFAULT = r"D:\SteamLibrary\steamapps\common\Shroom and Gloom"
AGENT_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.js")
CARD_I18N_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n_cards.json")

_BUNDLE_DIR = getattr(__import__("sys"), "_MEIPASS", None)
if _BUNDLE_DIR:
    AGENT_JS = os.path.join(_BUNDLE_DIR, "agent.js")
    CARD_I18N_JSON = os.path.join(_BUNDLE_DIR, "i18n_cards.json")


# ---- 找游戏进程：**不用** Frida 的 enumerate_processes ----
# ⚠️⚠️ 本机实测（frida 17.18.0 / Win11）：`device.enumerate_processes()` 只返回 **119** 个
# 进程，而同刻 Win32 `CreateToolhelp32Snapshot` 能看到 **293** 个 —— 少了 174 个，
# `steam.exe` 和 `Shroom and Gloom.exe` **都在缺的那批里**。
# 后果：`find_pid()` 永远返回 None → 面板报「连接异常：未找到游戏进程」，
# 但同一时刻 `device.attach(163856)` 按 PID **是能成功的** —— 说明注入链路没坏，
# **坏的只是枚举**。
# 所以这里改成走 Win32 快照拿 PID，只把 PID 交给 Frida。**别再改回 Frida 枚举。**
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", _wt.DWORD),
        ("cntUsage", _wt.DWORD),
        ("th32ProcessID", _wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", _wt.DWORD),
        ("cntThreads", _wt.DWORD),
        ("th32ParentProcessID", _wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", _wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _win32_process_name(pid):
    """按 PID 反查可执行文件名（不含路径）。取不到返回空串。

    `szExeFile` 只给文件名不给完整路径，所以这里再补一次
    `QueryFullProcessImageNameW` 拿全路径用于**精确比对**（有些游戏进程名会被
    Steam/DRM 包一层，比如 `xxx_launcher.exe`）。
    """
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = _wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def list_processes():
    """返回 [(pid, 完整镜像路径)]，走 Win32 快照（见上面为什么不用 Frida 枚举）。"""
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID_HANDLE or snap == 0:
        return []
    out = []
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            pid = int(e.th32ProcessID)
            # szExeFile 只有文件名；要全路径用于精确比对，拿不到就退回文件名
            full = _win32_process_name(pid) or e.szExeFile
            out.append((pid, full))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def load_card_i18n():
    """牌名中英对照（从游戏的 Unity Localization 串表里抽出来的，见 tools/loc_build.py）。

    返回 {by_name: {英文名: 中文}, by_key: {CARD_NAMES_XXX: 中文}}；读不到就空字典，
    界面退化成显示英文原名，不会崩。
    """
    try:
        with open(CARD_I18N_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"by_name": d.get("by_name") or {}, "by_key": d.get("by_key") or {}}
    except Exception:
        return {"by_name": {}, "by_key": {}}


def find_game_dir():
    """按顺序查找游戏目录"""
    cands = []
    env = os.environ.get("SHROOM_GAME_DIR")
    if env:
        cands.append(env)
    cands.append(GAME_DIR_DEFAULT)
    # 常见 Steam 库位置
    for drive in "CDEFGH":
        cands.append(rf"{drive}:\SteamLibrary\steamapps\common\Shroom and Gloom")
        cands.append(rf"{drive}:\Program Files (x86)\Steam\steamapps\common\Shroom and Gloom")
    cands.append(os.path.expanduser(r"~\SteamLibrary\steamapps\common\Shroom and Gloom"))
    # libraryfolders.vdf 里的库
    for vdf in glob.glob(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"):
        try:
            txt = open(vdf, encoding="utf-8", errors="ignore").read()
            import re
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                cands.append(os.path.join(m.group(1).replace("\\\\", "\\"),
                                         r"steamapps\common\Shroom and Gloom"))
        except Exception:
            pass
    for c in cands:
        if c and os.path.isfile(os.path.join(c, GAME_EXE)):
            return c
    return None


class GameTrainer:
    def __init__(self):
        self.session = None
        self.script = None
        self.pid = None
        self.device = frida.get_local_device()

    # ---------- 进程 ----------
    def find_pid(self):
        """找游戏进程 PID。走 Win32 快照，**不用** Frida 的枚举（原因见文件头注释）。

        匹配规则（从严到宽）：
          1. 镜像全路径 == `find_game_dir()/GAME_EXE`（最准，防止撞上同名的别的程序）
          2. 文件名 == GAME_EXE（游戏目录被挪走 / 换盘时仍能找到）
          3. 兜底：Frida 自己的枚举（万一 Win32 快照被拦，这里还能捡回来）
        """
        want = GAME_EXE.lower()
        game_dir = find_game_dir()
        target = os.path.join(game_dir, GAME_EXE).lower() if game_dir else None

        rows = list_processes()
        # ① 全路径精确匹配
        if target:
            for pid, full in rows:
                if full.lower() == target:
                    return pid
        # ② 退一步只看文件名（Win32 拿不到全路径时 full 就是文件名）
        for pid, full in rows:
            if os.path.basename(full).lower() == want:
                return pid
        # ③ 兜底：Frida 枚举（本机实测会缺进程，但聊胜于无）
        try:
            for p in self.device.enumerate_processes():
                if p.name.lower() == want:
                    return p.pid
        except Exception:
            pass
        return None

    def attach(self):
        pid = self.find_pid()
        if pid is None:
            raise RuntimeError("未找到游戏进程，请先启动游戏")
        self.session = self.device.attach(pid)
        self.pid = pid
        with open(AGENT_JS, "r", encoding="utf-8") as f:
            src = f.read()
        self.script = self.session.create_script(src)
        self.script.load()
        res = self.script.exports_sync.init()
        if not res or not res.get("ok"):
            raise RuntimeError(f"注入失败: {res}")
        return pid

    def detach(self):
        try:
            if self.script:
                self.script.unload()
        except Exception:
            pass
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass
        self.session = None
        self.script = None
        self.pid = None

    # ---------- 功能 ----------
    def set(self, feature, on, value=None):
        if value is not None:
            self.script.exports_sync.set(feature, bool(on), int(value))
        else:
            self.script.exports_sync.set(feature, bool(on))

    def status(self):
        return self.script.exports_sync.status()

    def deck(self):
        """牌库快照（只读）：抽牌堆按抽牌顺序返回牌名。"""
        return self.script.exports_sync.deck()

    # ---------- 遗忘手牌 ----------
    def forget_list(self):
        """列出可遗忘的牌：抽牌堆 + 弃牌堆 + 已消耗，按堆分组、逐张列出。

        返回：
          {
            ok, cards:[{idx, stack, stackLabel, index, name, cost, ptr}],
            deckType, deckTypeName,          # 1=探索(营地) / 2=战斗
            counts:{draw, discard, exhaust}, total,
            warning, canForget, err
          }
        注意：name 是**英文原名**，汉化在 GUI 侧查 i18n_cards.json。
        """
        return self.script.exports_sync.forget_list()

    def forget(self, ptr_hex, stack_key):
        """遗忘一张牌（真删，不写额度、不花资源、不备份）。

        ptr_hex / stack_key 来自 forget_list() 里对应项的 ptr / stack。
        返回 { ok, before, after, name, hitIndex, orderOk, err }。
        """
        return self.script.exports_sync.forget(str(ptr_hex), str(stack_key))
