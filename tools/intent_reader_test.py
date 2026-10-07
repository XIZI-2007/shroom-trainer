# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""intent_reader 单测：把结构化意图渲染成人话，用真实词典数据过一遍。

跑法：python tools/intent_reader_test.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from intent_reader import IntentReader   # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
        print("  FAIL  %s" % msg)
    else:
        print("  ok    %s" % msg)


def main():
    r = IntentReader()
    print("词典加载：%d 条意图，%d 条术语，%d 条释义" % (len(r.dict), len(r.terms), len(r.tips)))
    check(len(r.dict) == 173, "意图词典 173 条（实际 %d）" % len(r.dict))
    check(not r.warn, "无加载告警：%s" % r.warn)

    print("\n--- 渲染 ---")
    cases = [
        # (意图, 数值, 目标数, 期望包含)
        ("ApplyVulnerable", 2, "Single", None),
        ("ApplyPoisoned", 3, "All", None),
        ("DrawCardsEffect", 2, "Single", None),
        ("HealSelf", 5, "Self", None),
        ("GainEnergy", 2, "Self", None),
        ("ShieldDONTUSE", 6, "Self", None),
        ("IncreaseDamage", 3, "Single", None),
        ("DealHalfHealthInDamage", 0, "Single", None),
    ]
    for op, n, mult, _ in cases:
        t = r.render(op, n, mult, "zh")
        print("    %-24s n=%-2d %-8s -> %s" % (op, n, mult, t))
        check(bool(t.strip()), "%s 渲染非空" % op)
        check("[" not in t and "]" not in t, "%s 占位符已清干净" % op)

    # ★ DealDamage：**离线表抓不到、只有真机才有**的核心伤害意图（第 62 轮踩过）。
    #   它一旦缺失，summary 判未知 ⇒ 伤害不计入收益 ⇒ 推演恒输出"没有值得打出的牌"。
    print("\n--- DealDamage（运行时独有，防回归）---")
    check("DealDamage" in r.dict, "★ DealDamage 在词典里（离线序列抓不到，必须手工补）")
    check("DealDamage" in r.sem, "★ DealDamage 在语义表里（缺它 = 推演恒无收益）")
    check(r.sem.get("DealDamage", {}).get("metric") == "damage",
          "★ DealDamage 口径 = damage")
    for n, mult, want in ((6, "Single", 6), (16, "Single", 16), (3, "AllButSelf", 3)):
        s1 = r.summary([{"intent": "DealDamage", "number": n, "mult": mult}], cost=1)
        check(s1["deal"] == want and not s1["unknown"],
              "★ DealDamage %d/%s → deal=%d，无未识别（实际 %d / %s）"
              % (n, mult, want, s1["deal"], s1["unknown"]))
    # ⚠️ [PNUM][EXTRA_DAMAGE] 是**两个独立槽** ⇒ 曾经重复填充出 "造成 6 6 点伤害"
    t6 = r.render("DealDamage", 6, "Single")
    check(t6.count("6") == 1, "★ 数值只出现一次（EXTRA_DAMAGE 不是 number，别重复填）：%s" % t6)
    tgt = r.render("DealDamage", 4, "All", "zh")
    check("所有敌人" in tgt, "★ 多目标词走 _TGT_WORD 而非 term 猜名：%s" % tgt)
    check("All" not in tgt and "Single" not in tgt,
          "★ 不残留英文枚举名（term 猜名的坑）：%s" % tgt)

    print("\n--- 汇总 ---")
    intents = [
        {"intent": "ApplyVulnerable", "number": 2, "mult": "Single"},
        {"intent": "ShieldDONTUSE", "number": 6, "mult": "Self"},
        {"intent": "HealSelf", "number": 4, "mult": "Self"},
        {"intent": "DrawCardsEffect", "number": 1, "mult": "Single"},
    ]
    s = r.summary(intents, cost=2)
    print("    文本：%s" % s["text"])
    print("    伤害/护甲/治疗/抽牌 = %d/%d/%d/%d" % (s["deal"], s["shield"], s["heal"], s["draw"]))
    print("    条目 %d，未识别 %s" % (s["count"], s["unknown"]))
    check(s["deal"] == 0, "本例无直接伤害")
    check(s["shield"] == 6, "护甲合计 = 6")
    check(s["heal"] == 4, "治疗合计 = 4")
    check(s["draw"] == 1, "抽牌合计 = 1")
    check(s["count"] == 4, "条目 4 条")
    check(not s["unknown"], "无未识别意图")
    check("；" in s["text"], "多条用分号连")

    sc = r.score(s, enemies=1, hp_ratio=0.5)
    print("    score=%s perCost=%s" % (sc, s["perCost"]))
    check(sc > 0, "评分 > 0")
    check(s["perCost"] is not None, "有每费收益")

    print("\n--- 健壮性 ---")
    e = r.summary([], cost=-1)
    check(e["text"] == "" and e["count"] == 0, "空意图列表不崩")
    r.score(e)
    u = r.summary([{"intent": "NoSuchIntent", "number": 1, "mult": "Single"}], cost=1)
    check(u["unknown"] == ["NoSuchIntent"], "未知意图被登记而不是崩")
    check(u["text"].strip() == "NoSuchIntent 1", "未知意图退化成名字+数值：%r" % u["text"])
    # 牌名汉化
    nm = r.card_name("Acacia Mace")
    check(nm == "相思木钉头锤", "牌名汉化：%s" % nm)
    check(r.card_name("Not A Card") == "Not A Card", "未知牌名原样返回")

    print("\n--- 全量渲染冒烟（173 条都不能带占位符残留）---")
    bad = []
    for op in r.dict:
        for lng in ("zh", "en"):
            t = r.render(op, 3, "All", lng)
            if "[" in t or "]" in t:
                bad.append((op, lng, t))
    check(not bad, "173×2 条渲染无占位符残留（异常 %d）" % len(bad))
    for b in bad[:6]:
        print("      残留：", b)

    print()
    if FAIL:
        print("FAILED %d 项" % len(FAIL))
        for f in FAIL:
            print("  -", f)
        return 1
    print("INTENT READER OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
