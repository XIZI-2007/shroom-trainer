# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""从 global-metadata.dat 抽 CardIntent 枚举的**权威成员表**。

为什么不用串表：串表（Card Shared Data 的 `// Intents` 段）只有 235 条文本，
且是"描述模板"，不保证覆盖全部成员。元数据里的 `get_XXX` / `__xxx` 是
ScriptableEnum 的属性访问器与后备字段，**顺序即枚举顺序**，是最权威的来源。

用法：
    python tools/intent_meta.py            # 打印统计 + 写 src/card_intents.json 的 enum 段
    python tools/intent_meta.py --dump     # 额外打印完整成员表

输出：src/card_intent_enum.json
    { "source": "...global-metadata.dat", "count": N, "members": ["DamageSelf", ...],
      "lower_map": {"damageself": "DamageSelf", ...} }
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

META = (r"D:\SteamLibrary\steamapps\common\Shroom and Gloom"
        r"\Shroom and Gloom_Data\il2cpp_data\Metadata\global-metadata.dat")

# `CardIntent` 那一大段里，除了意图本身还有这几个属性/字段，要剔除
NOT_INTENTS = {
    "AllCardIntents", "All", "ApplyBrainless2", "Target",
}

# ⚠️⚠️ **离线抓不到、但运行时真实存在的成员** —— 必须手工补，否则后果极隐蔽。
#
# 判据（第 62 轮真机实证，不是猜的）：
#   1. 真机 `agent.js buildIntentMap()` 枚举 CardIntent 的**静态字段**得到 152 个名字；
#      离线 `get_XXX` 序列得到 171 个；**差集 = {DealDamage, Nothing}**（这两个只在真机有）。
#   2. 真机手牌上真的读到 `DealDamage×16Single` / `DealDamage×6Single`
#      ⇒ 它确实是被大量卡牌使用的**核心伤害意图**。
#   3. 在 metadata 里 `DealDamage` 的上下文（@1042214）是
#      `get_BlockDamage.get_Danger.get_DealDamage.get_Explode...` ——
#      这些是**属性访问器**，但**不在** CardIntent 的 anchor(`get_HasMultipleAllowedTargets`)
#      之后的那段序列里（相对 anchor 是 -13651，即在 anchor **之前**）
#      ⇒ 现有的"锚点 + 截断到 PrimaryTarget"策略天然抓不到它们。
#
# ⚠️ 漏掉的后果：`agent.js` 能读出意图**名字**（名表走静态字段，全），
#    但 `intent_reader.summary()` 在语义表里查不到它 ⇒ 判成 unknown
#    ⇒ **伤害一分都不计入收益** ⇒ 推演恒输出"没有值得打出的牌"（真机实测如此）。
RUNTIME_ONLY_MEMBERS = ["DealDamage", "Nothing"]


def read_meta(path=META):
    if not os.path.isfile(path):
        raise SystemExit("找不到元数据：%s" % path)
    with open(path, "rb") as fh:
        return fh.read()


def _string_blobs(data):
    """列出所有「C 字符串池」的 (偏移, 文本)，按偏移升序。"""
    out = []
    for m in re.finditer(rb"[ -~]{2,}", data):
        out.append((m.start(), m.group().decode("ascii", "replace")))
    return out


def extract_card_intents(data):
    """定位 `CardIntent` 类型名之后的 get_XXX 序列 → 权威成员表。

    布局（IL2CPP 元数据字符串池，按类型顺序排布）：
        ... CardIntent ,
        get_HasMultipleAllowedTargets, get_SelfOrProxy, IsAddCardEffect,
        get_AllCardIntents, get_All,
        get_ApplyBrainless, get_ApplyInfested, ...      <- 意图从这里开始
        get_Unlock,
        PrimaryTarget, CastEffect, ...                  <- 属性/字段，到此为止
        ...
        __allCardIntents, __all, __applyBrainless, ...  <- 同名后备字段

    ⚠️ `CardIntent` 这 10 个字符在元数据里出现 30+ 次（类名、注释、文件名…），
    **不能直接 find 第一次**。用唯一的近邻锚点 `get_HasMultipleAllowedTargets`
    （它紧跟在类型名之后、且全库唯一）来定位。
    """
    anchor = data.find(b"get_HasMultipleAllowedTargets")
    if anchor < 0:
        raise SystemExit("元数据里找不到 CardIntent 的锚点 get_HasMultipleAllowedTargets")
    anchor -= 32  # 回退到锚点前的类型名区域，保证下面正则有完整上下文

    # ⚠️ 必须显式截断：`get_XXX` 正则会一直往后匹配到别的类型的属性，
    # 整段 200KB 能匹配出 1030 个。真正的意图序列在 `PrimaryTarget`（首个非
    # 属性名的成员）处结束 —— 它是 CardIntent 这个 ScriptableEnum 的最后一个块。
    end = data.find(b"PrimaryTarget", anchor)
    if end < 0:
        raise SystemExit("找不到 CardIntent 序列的终止标记 PrimaryTarget")
    seg = data[anchor:end]

    names = [m.group(1).decode() for m in re.finditer(rb"get_([A-Z][A-Za-z0-9_]*)", seg)]
    if not names:
        raise SystemExit("CardIntent 段里没找到任何 get_XXX")

    # 去掉前导的非意图属性
    skip = {"HasMultipleAllowedTargets", "SelfOrProxy", "AllCardIntents", "All"}
    while names and names[0] in skip:
        names.pop(0)

    # 到第一个非 get_ 命名段（PrimaryTarget/CastEffect…）为止已经在正则里天然截断了，
    # 但 IsAddCardEffect 之类的漏网要手工剔除。
    names = [n for n in names if n not in NOT_INTENTS]

    # 去重保序
    seen, ordered = set(), []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)

    # 与 `__xxx` 后备字段交叉验证（应为一对一）
    backs = {m.group(1).decode().lower() for m in re.finditer(rb"__([A-Za-z][A-Za-z0-9_]*)", seg)}
    matched = [n for n in ordered if n.lower() in backs]

    # ⚠️ 补运行时独有的成员（离线序列抓不到，见 RUNTIME_ONLY_MEMBERS 注释）。
    #    放在末尾：它们不在 get_XXX 序列里，无语义顺序，缀在后面最不容易误伤。
    for n in RUNTIME_ONLY_MEMBERS:
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    return ordered, matched


def main():
    dump = "--dump" in sys.argv
    data = read_meta()
    members, matched = extract_card_intents(data)

    print("CardIntent 权威成员：%d 个" % len(members))
    print("其中能与 __字段 对上：%d 个" % len(matched))
    print()

    # 分类统计（按前缀，方便人读）
    groups = {}
    for n in members:
        key = n[:6] if n.startswith("Apply") else n[:4]
        groups[key] = groups.get(key, 0) + 1
    top = sorted(groups.items(), key=lambda kv: -kv[1])[:12]
    print("高频前缀：", ", ".join("%s×%d" % (k, v) for k, v in top))
    print()

    if dump:
        print("== 完整成员表 ==")
        for i, n in enumerate(members):
            print("  [%3d] %s" % (i, n))
        print()

    out = {
        "_comment": "CardIntent 枚举权威成员表，从 global-metadata.dat 的 get_XXX 序列抽取",
        "source": META,
        "count": len(members),
        "members": members,
        "lower_map": {n.lower(): n for n in members},
    }
    dst = os.path.join(ROOT, "src", "card_intent_enum.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("写出", os.path.relpath(dst, ROOT), "(%d 字节)" % os.path.getsize(dst))


if __name__ == "__main__":
    main()
