# -*- coding: utf-8 -*-
"""从游戏 Unity Localization 的 `Enemy` 串表抽「状态名 → 中英名 + 本地化键」。

状态名在 `Enemy` 表里，key 形如 `STATUS_CHARGED` / `STATUS_MARKED` / `STATUS_SPELL_WEAKNESS`
（值形如 已充能 / 标记 / 咒语虚弱）。key 的拼写**不规整**（有的带下划线 HOT_SAUCE、
NANO_LACED，有的不带 GLOOMCREATURECOSIMICHORROR），所以按**归一化**（去非字母数字 + 大写）匹配。

⚠️ 状态名以 `tools/status_meta.py` 从元数据抽出的**权威成员表**为准，本脚本只负责配名。
   配不到的会显式列出来（宁缺毋滥，不做模糊猜测）。

用法：
    python tools/status_loc_build.py            # 自动找游戏目录 → src/status_i18n.json

游戏一更新串表就要重跑（和 loc_build.py 同理）。
"""
import glob
import json
import os
import re
import sys

try:
    import UnityPy
except ImportError:
    raise SystemExit("缺 UnityPy：pip install UnityPy")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "src", "status_i18n.json")
ENUM = os.path.join(ROOT, "src", "status_effect_enum.json")

PATTERNS = [
    r"{d}\SteamLibrary\steamapps\common\Shroom and Gloom",
    r"C:\Program Files (x86)\Steam\steamapps\common\Shroom and Gloom",
]


def find_aa_dir(explicit=None):
    cands = [explicit] if explicit else []
    cands += [p.format(d=d) for p in PATTERNS for d in "CDEFGH"]
    for vdf in glob.glob(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"):
        for m in re.finditer(r'"path"\s+"([^"]+)"',
                             open(vdf, encoding="utf-8", errors="ignore").read()):
            cands.append(os.path.join(m.group(1).replace("\\\\", "\\"),
                                      r"steamapps\common\Shroom and Gloom"))
    for c in cands:
        if not c:
            continue
        aa = os.path.join(c, "Shroom and Gloom_Data", "StreamingAssets", "aa",
                          "StandaloneWindows64")
        if os.path.isdir(aa):
            return aa
    raise SystemExit("找不到游戏目录，请手动传：python tools/status_loc_build.py <游戏目录>")


def _table(path, mono_name, attr, sub):
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read()
        if getattr(d, "m_Name", "") != mono_name:
            continue
        arr = getattr(d, attr, None) or []
        return {str(getattr(e, "m_Id", "")): str(getattr(e, sub, "")) for e in arr}
    return {}


def norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


# ⚠️ 游戏本地化表 key 的**拼写错误**，只能定向修（不做模糊匹配，免得错配到别的状态）。
#    STATUS_GLOOMCREATURECOSIMICHORROR ← 应为 ...COSMICHORROR（多了个 I）。
KEY_FIXUPS = {
    "GLOOMCREATURECOSIMICHORROR": "GLOOMCREATURECOSMICHORROR",
}


def main():
    aa = find_aa_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print("游戏资源目录：%s" % aa)
    en_p = glob.glob(os.path.join(aa, "localization-string-tables-english*"))[0]
    zh_p = glob.glob(os.path.join(aa, "localization-string-tables-chinese*"))[0]
    sh_p = glob.glob(os.path.join(aa, "localization-assets-shared*"))[0]

    en = _table(en_p, "Enemy_en", "m_TableData", "m_Localized")
    zh = _table(zh_p, "Enemy_zh-Hans", "m_TableData", "m_Localized")
    kk = _table(sh_p, "Enemy Shared Data", "m_Entries", "m_Key")
    print("Enemy 表：en %d / zh %d / key %d" % (len(en), len(zh), len(kk)))
    if not en or not kk:
        raise SystemExit("Enemy 串表读出来是空的，游戏版本可能变了")

    # 收集 STATUS_* 条目 → (normkey, key, en, zh)
    entries = []
    for i, k in kk.items():
        if not k.startswith("STATUS_"):
            continue
        entries.append({
            "key": k,
            "nkey": norm(k[len("STATUS_"):]),
            "en": en.get(i, ""),
            "zh": zh.get(i, ""),
        })
    print("STATUS_* 条目 %d 条" % len(entries))

    enum = json.load(open(ENUM, encoding="utf-8"))
    members = enum["members"]

    by_name, missing = {}, []
    for n in members:
        nn = norm(n)
        hit = None
        # 1) key 归一化精确匹配（首选：key 就是为这个状态建的）
        for e in entries:
            if KEY_FIXUPS.get(e["nkey"], e["nkey"]) == nn:
                hit = e
                break
        # 2) en 值归一化精确匹配（兜底：key 拼写和状态名不一致时）
        if hit is None:
            for e in entries:
                if norm(e["en"]) == nn:
                    hit = e
                    break
        if hit is None:
            missing.append(n)
            continue
        by_name[n] = {
            "zh": hit["zh"] or hit["en"],
            "en": hit["en"],
            "key": hit["key"],
            "hasZh": bool(hit["zh"]),
        }

    print()
    print("配到名字：%d / %d" % (len(by_name), len(members)))
    if missing:
        print("⚠️ 没配到的（保留英文名，不硬猜）：%s" % missing)
    no_zh = [n for n, v in by_name.items() if not v["hasZh"]]
    if no_zh:
        print("⚠️ 配到英文但中文为空：%s" % no_zh)

    # 反向：表里有、但状态枚举里没有的 STATUS_ 键（供人工核对，不写进结果）
    extra = [e["key"] for e in entries if e["nkey"] not in {norm(n) for n in members}]
    if extra:
        print()
        print("表里有、枚举成员里没有的 STATUS_ 键（可能是卡牌属性/文案，不是敌人状态）：")
        for k in sorted(set(extra)):
            e = next(x for x in entries if x["key"] == k)
            print("    %-38s en=%-22s zh=%s" % (k, e["en"], e["zh"]))

    out = {
        "_comment": ("状态名 → 中英名 + 本地化键。成员表来自 status_meta.py（元数据），"
                     "名字来自游戏 Enemy 串表的 STATUS_* 键。"),
        "source": aa,
        "count": len(by_name),
        "missing": missing,
        "by_name": dict(sorted(by_name.items())),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print()
    print("写出 %s (%d 字节)" % (os.path.relpath(OUT, ROOT), os.path.getsize(OUT)))


if __name__ == "__main__":
    main()
