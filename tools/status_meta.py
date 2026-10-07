# -*- coding: utf-8 -*-
"""从 global-metadata.dat 抽 `StatusEffect` 枚举的**权威成员表**。

`StatusEffect` 和 `CardIntent` 是同一套东西：IL2CPP 里的 ScriptableEnum，
类上有一批 `get_XXX` 属性访问器 + 一批 `__xxx` 静态后备字段，**字段名就是状态名**。
所以抽取策略照抄 intent_meta.py，锚点换成状态自己的：

    get_AllStatusEffects        <- 全库唯一，属性访问器序列的起点
      get_Charged … get_Unnerving   <- 状态成员（顺序即声明顺序）
    StatusColor                 <- 之后是类属性（颜色/显示规则/最小值…），序列到此为止
      …
    __allStatusEffects          <- 后备字段序列起点
      __charged … __unnerving
    AllStatusEffects            <- 之后是枚举实例引用列表

⚠️⚠️ **必须同时用后备字段交叉验证**（第 66 轮实证）：有三个状态**只有后备字段、
   没有属性访问器**：`Thorns` / `Spellshield` / `Regrowth`。只抽 `get_XXX` 会静默漏掉，
   而它们在本地化表里都有正式名字（荆棘 / 咒语护盾 / 再生），是真实状态。
   这与 CardIntent 那边 DealDamage 漏网的教训**完全同类**。

⚠️ 别把 `get_Vulnerable` / `get_Poisoned` 当状态：它们在元数据里属于 **`CardProperty`**
   （卡牌属性，看邻近的 `PropertyName` / `<ShouldDisplayNumber>` / `__allCardProperties`）。
   本地化表里的 `STATUS_VULNERABLE` / `STATUS_POISONED` 是**另一套键**，指卡牌属性文案。

用法：
    python tools/status_meta.py            # 统计 + 写 src/status_effect_enum.json
    python tools/status_meta.py --dump     # 额外打印完整成员表

输出：src/status_effect_enum.json
    { source, count, members: [...], accessor_members: [...], backfield_only: [...],
      lower_map: {...}, display_fields: [...] }
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

META = (r"D:\SteamLibrary\steamapps\common\Shroom and Gloom"
        r"\Shroom and Gloom_Data\il2cpp_data\Metadata\global-metadata.dat")

# ⚠️⚠️ **离线抓不到、但运行时真实存在的成员** —— 必须手工补，后果与 CardIntent 的
#    `DealDamage` 完全同型（那次踩过一回，这次是同一个坑的第二例）。
#
# 判据（2026-10-06 第 63 轮真机实证，`fieldsOf('StatusEffect')` dump）：
#   · 运行时 StatusEffect 类上有 **68 个 `__xxx` 静态字段**；
#   · 离线 `get_XXX` 序列只有 **66 个** ⇒ 差集 = **{Vulnerable, Poisoned}**；
#   · 在 metadata 里 `get_Vulnerable`@1066183 / `get_Poisoned`@1066073，
#     而锚点 `get_AllStatusEffects`@1087743 —— **它们落在锚点之前**，
#     现有的"锚点 + 截断到 StatusColor"策略天然抓不到。
#   · 真机 `statusMap()` 也确实含这两只（只要资产已加载）。
#
# ⚠️ 漏掉的后果：`ApplyVulnerable` 是本游戏**最核心的增伤状态**（tooltip：
#     会额外受到 [P2NUM]% 伤害）。漏了它 ⇒ 易伤算不出倍率 ⇒ 推演低估伤害。
#
# ⚠️ 注意大小写：静态字段是 `__vulnerable`（首字母小写），规范名是 `Vulnerable`。
RUNTIME_ONLY_MEMBERS = ["Vulnerable", "Poisoned"]

# 属性访问器序列
ANCHOR_GET = "get_AllStatusEffects"      # 全库唯一
END_GET = "StatusColor"                  # StatusEffect 的类属性从这里开始
# 后备字段序列
ANCHOR_BACK = "__allStatusEffects"
END_BACK = "AllStatusEffects"            # 之后是枚举实例引用列表

# 序列首尾的"集合属性"本身不是状态
NOT_STATUS = {"AllStatusEffects"}


def read_meta(path=META):
    if not os.path.isfile(path):
        raise SystemExit("找不到元数据：%s" % path)
    with open(path, "rb") as fh:
        return fh.read()


def _blobs(data):
    return [(m.start(), m.group().decode("ascii", "replace"))
            for m in re.finditer(rb"[ -~]{2,}", data)]


def _segment(blobs, start_name, end_name, nth_start=0):
    """从 start_name（第 nth_start 次出现）到其后第一次 end_name 之间的字符串列表。"""
    idxs = [j for j, (o, t) in enumerate(blobs) if t == start_name]
    if len(idxs) <= nth_start:
        raise SystemExit("元数据里找不到锚点 %s（第 %d 次）" % (start_name, nth_start + 1))
    i = idxs[nth_start]
    for j in range(i + 1, len(blobs)):
        if blobs[j][1] == end_name:
            return blobs[i:j]
    raise SystemExit("锚点 %s 之后找不到终止标记 %s" % (start_name, end_name))


def extract(data):
    blobs = _blobs(data)

    # ---- 属性访问器序列 → 权威成员 ----
    seg_get = _segment(blobs, ANCHOR_GET, END_GET)
    accessors = [t[4:] for _, t in seg_get if t.startswith("get_") and len(t) > 4]

    # ---- 后备字段序列（camelCase → PascalCase）----
    seg_back = _segment(blobs, ANCHOR_BACK, END_BACK)
    backs_raw = [t[2:] for _, t in seg_back if t.startswith("__") and len(t) > 2]
    backs = []
    for b in backs_raw:
        if b and b[0].islower():
            b = b[0].upper() + b[1:]
        if b not in backs:
            backs.append(b)

    # ---- 交叉验证 ----
    a_set, b_set = set(accessors), set(backs)
    only_back = [b for b in backs if b not in a_set]      # ⚠️ 只有后备字段的状态

    # ---- 合并：访问器序列为主，补上只有后备字段的 ----
    members, seen = [], set()
    for n in accessors + only_back:
        if n in NOT_STATUS or n in seen:
            continue
        seen.add(n)
        members.append(n)

    # ⚠️ 补运行时独有成员（离线序列落在锚点之前，抓不到；见 RUNTIME_ONLY_MEMBERS）
    for n in RUNTIME_ONLY_MEMBERS:
        if n not in seen:
            seen.add(n)
            members.append(n)

    # ---- 类属性（显示规则/颜色/取值范围），供理解层判断"要不要显示数字" ----
    display_fields = [t for _, t in blobs[
        next(j for j, (o, t) in enumerate(blobs) if t == "StatusColor"):
        next(j for j, (o, t) in enumerate(blobs) if t == ANCHOR_BACK)
    ] if re.match(r"^[A-Z][A-Za-z]+$", t)]

    return {
        "accessor_members": accessors,
        "backfield_only": only_back,
        "members": members,
        "display_fields": display_fields,
        "only_accessor": [a for a in accessors if a not in b_set],
    }


def main():
    dump = "--dump" in sys.argv
    data = read_meta()
    r = extract(data)

    print("StatusEffect 权威成员：%d 个" % len(r["members"]))
    print("  其中属性访问器序列：%d 个" % len(r["accessor_members"]))
    print("  只有后备字段、无访问器（易漏）：%d 个 → %s"
          % (len(r["backfield_only"]), r["backfield_only"]))
    print()

    if dump:
        print("== 完整成员表 ==")
        for i, n in enumerate(r["members"]):
            tag = "  (仅后备字段)" if n in r["backfield_only"] else ""
            print("  [%3d] %s%s" % (i, n, tag))
        print()
        print("== StatusEffect 类属性 ==")
        print("  " + ", ".join(r["display_fields"]))
        print()

    out = {
        "_comment": ("StatusEffect 枚举权威成员表，从 global-metadata.dat 抽取。"
                     "get_XXX 属性访问器序列 + __xxx 静态后备字段交叉验证；"
                     "backfield_only 是只有后备字段、按访问器抽会漏掉的状态。"),
        "source": META,
        "count": len(r["members"]),
        "members": r["members"],
        "accessor_members": r["accessor_members"],
        "backfield_only": r["backfield_only"],
        "display_fields": r["display_fields"],
        "lower_map": {n.lower(): n for n in r["members"]},
    }
    dst = os.path.join(ROOT, "src", "status_effect_enum.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("写出 %s (%d 字节)" % (os.path.relpath(dst, ROOT), os.path.getsize(dst)))


if __name__ == "__main__":
    main()
