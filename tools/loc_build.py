# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""从游戏的 Unity Localization 串表里抽出「英文牌名 → 中文」对照表。

用法：
    python tools/loc_build.py              # 自动找游戏目录，写到 src/i18n_cards.json
    python tools/loc_build.py <游戏目录>

依赖：`pip install UnityPy`（只读游戏文件，不修改任何东西）

原理（实测确认）：
  StreamingAssets/aa/StandaloneWindows64/ 下每个语言一个 bundle，里面是
  `StringTable` 的 MonoBehaviour，字段 m_TableData = [{m_Id, m_Localized}]；
  同目录的 localization-assets-shared bundle 里是 `SharedTableData`，字段
  m_Entries = [{m_Id, m_Key}]（key 形如 CARD_NAMES_BASH）。

  两张表按 **m_Id** 对齐：英文值当 key、中文值当 value，就得到「英文名 → 中文」。
  这样面板侧不用去解析运行时的 LocKey，直接拿 <Name>（英文）查表即可。

游戏一更新串表就要重跑一次（牌名会变），跑完重新打包。
"""
import glob
import json
import os
import sys

try:
    import UnityPy
except ImportError:
    raise SystemExit("缺 UnityPy：pip install UnityPy")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "src", "i18n_cards.json")
PATCH = os.path.join(HERE, "i18n_cards_patch.json")

PATTERNS = [
    r"{d}\SteamLibrary\steamapps\common\Shroom and Gloom",
    r"C:\Program Files (x86)\Steam\steamapps\common\Shroom and Gloom",
    r"{d}\Program Files (x86)\Steam\steamapps\common\Shroom and Gloom",
]


def find_aa_dir(explicit=None):
    cands = [explicit] if explicit else []
    cands += [p.format(d=d) for p in PATTERNS for d in "CDEFGH"]
    for vdf in glob.glob(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"):
        try:
            import re
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8",
                                                                errors="ignore").read()):
                cands.append(os.path.join(m.group(1).replace("\\\\", "\\"),
                                          r"steamapps\common\Shroom and Gloom"))
        except Exception:
            pass
    for c in cands:
        if not c:
            continue
        aa = os.path.join(c, "Shroom and Gloom_Data", "StreamingAssets", "aa",
                          "StandaloneWindows64")
        if os.path.isdir(aa):
            return aa
    raise SystemExit("找不到游戏目录，请手动传：python tools/loc_build.py <游戏目录>")


def table_values(path, table_name):
    """StringTable: m_Id -> m_Localized"""
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read()
        if getattr(d, "m_Name", "") != table_name:
            continue
        return {str(getattr(e, "m_Id", "")): str(getattr(e, "m_Localized", ""))
                for e in (getattr(d, "m_TableData", None) or [])}
    return {}


def table_keys(path, table_name):
    """SharedTableData: m_Id -> m_Key"""
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read()
        if getattr(d, "m_Name", "") != table_name:
            continue
        return {str(getattr(e, "m_Id", "")): str(getattr(e, "m_Key", ""))
                for e in (getattr(d, "m_Entries", None) or [])}
    return {}


def main():
    aa = find_aa_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"游戏资源目录：{aa}")
    zh_bundle = glob.glob(os.path.join(aa, "localization-string-tables-chinese*"))[0]
    en_bundle = glob.glob(os.path.join(aa, "localization-string-tables-english*"))[0]
    shared = glob.glob(os.path.join(aa, "localization-assets-shared*"))[0]

    en = table_values(en_bundle, "Card Names_en")
    zh = table_values(zh_bundle, "Card Names_zh-Hans")
    kk = table_keys(shared, "Card Names Shared Data")
    print(f"英文 {len(en)} 条 / 中文 {len(zh)} 条 / key {len(kk)} 条")
    if not en or not zh:
        raise SystemExit("串表读出来是空的，游戏版本可能变了（表名/结构）")

    by_name, by_key = {}, {}
    for i, e in en.items():
        z = zh.get(i, "")
        if e and z:
            by_name[e] = z
        if kk.get(i):
            by_key[kk[i]] = z or e
    missing = [e for i, e in en.items() if e and not zh.get(i)]
    if missing:
        print(f"中文缺失 {len(missing)} 条（从补丁表补）：{missing}")

    # 手工补丁表（官方 zh-Hans 里留空的条目，按 CARD_NAMES_* key 补）
    patch = {}
    if os.path.isfile(PATCH):
        with open(PATCH, encoding="utf-8") as f:
            patch = json.load(f)
    for k, z in (patch.get("by_key") or {}).items():
        if not z:
            continue
        by_key[k] = z
        for id_, key in kk.items():
            if key == k and en.get(id_):
                by_name[en[id_]] = z
    for n, z in (patch.get("by_name") or {}).items():
        if z:
            by_name[n] = z
    if patch:
        print(f"补丁表生效 {len(patch.get('by_key') or {}) + len(patch.get('by_name') or {})} 条")

    left = [e for i, e in en.items()
            if e and not zh.get(i) and e not in by_name]
    if left:
        print(f"仍缺中文 {len(left)} 条：{left}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"by_name": dict(sorted(by_name.items())),
                   "by_key": dict(sorted(by_key.items()))},
                  f, ensure_ascii=False, indent=0)
    print(f"写出 {OUT}：by_name {len(by_name)} / by_key {len(by_key)}，"
          f"{os.path.getsize(OUT)} 字节")


if __name__ == "__main__":
    main()
