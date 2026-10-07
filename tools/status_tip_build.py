# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""从游戏 Unity Localization 抽「状态 → 官方说明文案」（TOOLTIP_STATUS_*）。

这是**游戏自己的权威口径**：每个状态干什么，官方 tooltip 写得很清楚。
推演引擎的伤害模型必须建立在这份文案上，而不是照搬别的游戏的"易伤 +50%"。

关键几条（原文见 tools/_tooltips.txt）：
  VULNERABLE  会额外受到 [P2NUM]% 伤害      ← 确实是易伤，只是不在 StatusEffect 枚举里
  THORNS      受伤时除非伤害致命，否则反击 [PNUM] 点伤害
  AIRBORNE    难以触及，承受伤害降低 50%。命中 [PNUM] 次令其落地
  CONFUSED    攻击伤害降低 [PNUM]
  ENRAGED     造成 [PNUM] 点额外伤害
  MUTANT      造成 [PNUM] 点额外伤害（如果受到伤害则翻倍）
  SPOREY / …
  STUNNED     跳过其行动

用法：
    python tools/status_tip_build.py            # → src/status_tips.json

输出：src/status_tips.json
    { source, count, tips: { "Charged": {"key":…, "en":…, "zh":…, "raw_zh":…}, … } }
    ⚠️ zh 是剥掉 [HEXCODE]/</color> 等标记后的可读文本；raw_zh 保留原文。
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
OUT = os.path.join(ROOT, "src", "status_tips.json")
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
    raise SystemExit("找不到游戏目录")


def _table(path, mono_name, attr, sub):
    env = UnityPy.load(path)
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read()
        if getattr(d, "m_Name", "") != mono_name:
            continue
        return {str(getattr(e, "m_Id", "")): str(getattr(e, sub, ""))
                for e in (getattr(d, attr, None) or [])}
    return {}


def norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


# 文案里的标记：只留纯文本，便于显示 / 后续判断
_TAG = re.compile(r"\[HEXCODE\]|</color>|<[^>]+>|\[/?LINK\]|\[/link\]", re.I)


def clean(s):
    s = _TAG.sub("", s or "")
    s = s.replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def main():
    aa = find_aa_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print("游戏资源目录：%s" % aa)
    en_p = glob.glob(os.path.join(aa, "localization-string-tables-english*"))[0]
    zh_p = glob.glob(os.path.join(aa, "localization-string-tables-chinese*"))[0]
    sh_p = glob.glob(os.path.join(aa, "localization-assets-shared*"))[0]

    en = _table(en_p, "Enemy_en", "m_TableData", "m_Localized")
    zh = _table(zh_p, "Enemy_zh-Hans", "m_TableData", "m_Localized")
    kk = _table(sh_p, "Enemy Shared Data", "m_Entries", "m_Key")

    rows = [(kk[i], en.get(i, ""), zh.get(i, "")) for i in kk
            if kk[i].startswith("TOOLTIP_STATUS_")]
    print("TOOLTIP_STATUS_* 共 %d 条" % len(rows))

    # key(去掉 TOOLTIP_STATUS_) 归一化 → 文案
    by_nkey = {}
    for k, e, z in rows:
        by_nkey.setdefault(norm(k[len("TOOLTIP_STATUS_"):]), (k, e, z))

    enum = json.load(open(ENUM, encoding="utf-8"))
    members = enum["members"]
    # ⚠️ 以前这里手工补过 `Vulnerable`（当时它不在枚举里）。现在 status_meta.py 已按
    #    真机 dump 把它与 `Poisoned` 一起补进 members ⇒ **不再需要本地的 extra**。
    #    留着会算出 69 的分母，看上去"少配了一条"（假告警）。
    extra = []

    tips, missing = {}, []
    for n in members + extra:
        hit = by_nkey.get(norm(n))
        if hit is None:
            missing.append(n)
            continue
        k, e, z = hit
        tips[n] = {"key": k, "en": clean(e), "zh": clean(z), "raw_zh": z}

    print("配到说明：%d / %d" % (len(tips), len(members) + len(extra)))
    if missing:
        print("⚠️ 没有 tooltip 的（不硬编，留空）：%s" % missing)

    out = {
        "_comment": ("状态 → 游戏官方 tooltip 文案（Enemy 串表 TOOLTIP_STATUS_*）。"
                     "这是状态效果的权威口径，推演引擎的伤害模型以此为准。"),
        "source": aa,
        "count": len(tips),
        "missing": missing,
        "enemy_status_members": len(members),
        "tips": dict(sorted(tips.items())),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("写出 %s (%d 字节)" % (os.path.relpath(OUT, ROOT), os.path.getsize(OUT)))


if __name__ == "__main__":
    main()
