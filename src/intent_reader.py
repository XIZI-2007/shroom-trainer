# -*- coding: utf-8 -*-
# Copyright (C) 2026 XIZI-2007. All rights reserved.
# 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
"""卡牌效果理解层 —— 把 agent.js 读出的 (意图, 数值, 目标数) 变成"人话 + 可算的数"。

数据来源（全部离线，随 exe 打包）：
  card_intent_dict.json   意图 → 中英文案模板（[PNUM] 等占位符）
  intent_semantics.json   意图 → 类别/数值口径/作用对象/得失/场景/能否计入收益
  term_glossary.json      模板里 [LINKKEY=…] 等引用键 → 中英词；+ 意图一句话释义
  i18n_cards.json         牌名中英对照

本模块只做**纯计算**，不碰 Frida / Qt，方便单测。

典型用法：
    r = IntentReader()
    r.summary(card_intents_from_agent_js, cost=2)
    # -> {'text': '造成 6 点伤害；抽 1 张牌', 'deal': 6, 'heal': 0, ...}
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLE = getattr(__import__("sys"), "_MEIPASS", None)
if _BUNDLE:
    _HERE = _BUNDLE


def _load(name, default):
    for d in (_HERE, os.path.join(_HERE, "src")):
        p = os.path.join(d, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return default


# ---- 占位符 ----
# [PNUM] / [JNUM] / [XXX_NUM] = 数值槽，渲染时替换成本条意图的 number
# ⚠️ **[EXTRA_DAMAGE] / [EXTRA_HEAL] 不是 number**：它们是**另一个**数值槽
#    （模板形如 `[PNUM][EXTRA_DAMAGE]`，两槽各填一次 ⇒ 把两个都替换成 number
#    会出现"造成 6 6 点伤害"/"治疗 66 点"这种重复，踩过）。
#    面板读不到"额外伤害"的独立数值 ⇒ 直接吃掉这个槽，只保留 PNUM。
_P_NUM = re.compile(r"\[(?:P|J)NUM\]|\[[A-Z_]*_NUM\]")
_P_EXTRA = re.compile(r"\[EXTRA_(?:DAMAGE|HEAL)\]")
# [LINKKEY=WORD_X] / [JOINER=WORD_X] / [JNUM=WORD_X] = 术语引用
_P_REF = re.compile(r"\[(LINKKEY|JOINER|JNUM)=([A-Za-z0-9_]+)\]")
# [MULTIPLICITY_ENEMY|CARD] = 目标数
_P_MULT = re.compile(r"\[MULTIPLICITY_([A-Z]+)\]")
# 其它装饰性占位符，直接吃掉
_P_JUNK = re.compile(r"\[(?:HEXCODE|CARD_NAME|PROPERTY_NAME|MULTIPLY[A-Z_0-9]*|TRIGGER[A-Z_]*)\]|</?link>|</?color>|</?b>")
# 兜底：任何**没被上面吃掉**的裸 [XXX] / [KEY=] 一律删（不残留方括号；踩过 WHEN_USED/DECK_TYPE/LINKKEY=）
_P_ANY = re.compile(r"\[[A-Za-z_0-9=]*\]")

# 目标词：agent.js 的 mult 值是 MULTIPLICITY_NAME 的输出（Single/All/Self/…），
# ⚠️ 这些键在串表里**不存在**（游戏自己也没写），必须自己给词，别再走 term()。
_TGT_WORD = {
    "Single": ("敌人", "the enemy"), "Enemy": ("敌人", "the enemy"),
    "All": ("所有敌人", "all enemies"), "AllButSelf": ("其他所有敌人", "all other enemies"),
    "Random": ("随机敌人", "a random enemy"),
    "ToTheLeft": ("左侧敌人", "the enemy to the left"),
    "ToTheRight": ("右侧敌人", "the enemy to the right"),
    "Self": ("自己", "yourself"),
    "Card": ("卡牌", "the card"),
    "None": ("", ""),
}

# 少数意图的串表模板是**开发占位**或**缺数值槽**，中文/英文都不可用 ⇒ 手工覆盖。
# 只列必须的（护甲/治疗这类直接进战斗计算的），其余宁可显示原文也不臆造。
#   {n} = 数值   {tgt} = 目标词（多目标时才有）   {tgt1} = 单目标词
_OVERRIDE = {
    "ShieldDONTUSE": ("获得 {n} 点护甲", "Gain {n} shield"),
    "HealSelf":      ("治疗自己 {n} 点生命", "Heal {n} health"),
    "IncreaseDamage": ("使下一张攻击牌伤害 +{n}", "Increase next damage by {n}"),
    # ⚠️ 游戏里最核心的伤害意图，串表却只有 `INTENT_DEAL_DAMAGE_POS`（造成伤害）
    #    与 `_NEG`（治疗等量）**两条变体**，运行时枚举成员只有一个 `DealDamage`。
    #    模板里还有 [EXTRA_DAMAGE] 这种面板拿不到的槽 ⇒ 直接手工给一句干净的。
    "DealDamage":    ("对{tgt}造成 {n} 点伤害", "Deal {n} damage to {tgt}"),
}


class IntentReader:
    """意图 → 人话 / 数值。所有表都在构造时加载一次。"""

    # 目标倍率：'Single' → 1；'All' → 用场上敌人数（未知时给 1，不虚报）
    _MULT_SINGLE = {"Single", "Random", "ToTheLeft", "ToTheRight", "Self", "None"}
    _MULT_ALL = {"All", "AllButSelf"}

    def __init__(self):
        d = _load("card_intent_dict.json", {"intents": []})
        self.dict = {e["op"]: e for e in d.get("intents", []) if e.get("op")}
        self.sem = _load("intent_semantics.json", {}).get("intents", {})
        self.cats = _load("intent_semantics.json", {}).get("cats", {})
        g = _load("term_glossary.json", {})
        self.terms = g.get("terms", {})
        self.tips = g.get("tips", {})
        self.tips_norm = g.get("tipsByNorm") or {}
        i18n = _load("i18n_cards.json", {})
        self.card_zh = i18n.get("by_name", {})

        if not self.dict:
            self.warn = "卡牌意图词典缺失（card_intent_dict.json 没打进包？）"
        else:
            self.warn = ""

        # 预编译别名（模板引用名 ≠ 实际表键名，见 term_glossary_build.py）
        self._alias = [
            (re.compile(r"^STATUS_(.+)$"), ["PROPERTY_{0}", "WORD_{0}"]),
            (re.compile(r"^MULTIPLICITY_ENEMY$"), ["MULT_ENEMY_SINGLE"]),
            (re.compile(r"^MULTIPLICITY_CARD$"), ["MULT_CARD_SINGLE"]),
            (re.compile(r"^MULTIPLICITY_(.+)$"), ["MULT_{0}_SINGLE", "MULT_{0}"]),
            (re.compile(r"^JNUM_(.+)$"), ["WORD_QUANTIFIER{0}", "JNUM_{0}", "WORD_{0}"]),
        ]

    # ---------- 词表 ----------
    def term(self, key, lang="zh"):
        """引用键 → 词。查不到就按前缀猜个人能读的名字（不留空）。"""
        t = self.terms.get(key)
        if t is None:
            for rx, cands in self._alias:
                m = rx.match(key)
                if not m:
                    continue
                for c in cands:
                    k2 = c.format(*m.groups()) if "{0}" in c else c
                    if k2 in self.terms:
                        t = self.terms[k2]
                        break
                if t:
                    break
        if t is None:
            return self._friendly(key)
        v = (t.get(lang) or t.get("en") or "").strip()
        return v or self._friendly(key)

    @staticmethod
    def _friendly(key):
        for p in ("STATUS_", "MULTIPLICITY_", "PROPERTY_", "WORD_", "MULT_"):
            if key.startswith(p):
                key = key[len(p):]
                break
        return " ".join(w.capitalize() for w in key.split("_") if w) or key

    def card_name(self, en_name):
        """英文牌名 → 中文（查不到就原样返回英文）。"""
        return self.card_zh.get(en_name) or en_name

    def tip(self, op):
        """意图的一句话释义（游戏只给了 84 条，没有就返回空串）。"""
        t = self.tips.get(op)
        if t:
            return (t.get("zh") or t.get("en") or "").strip()
        t = self.tips_norm.get(re.sub(r"[^a-z0-9]", "", op.lower()))
        if t:
            return (t.get("zh") or t.get("en") or "").strip()
        return ""

    # ---------- 渲染 ----------
    def render(self, op, number, mult, lang="zh"):
        """一条意图 → 人话。模板缺了/是开发占位就退回「意图名 x 数值」。"""
        # 手工覆盖优先（护甲/治疗这类模板在串表里不可用）
        ov = _OVERRIDE.get(op)
        if ov:
            # ⚠️ 目标词**必须走 _TGT_WORD**（agent.js 的 mult 值域），不能走 term()：
            #    term 查不到 `MULTIPLICITY_SINGLE` 这类键时会按前缀猜，
            #    猜出来是 "Single"/"Allbutself" 这种英文枚举名（踩过）。
            w = _TGT_WORD.get(mult or "Single")
            tw = (w[0] if lang == "zh" else w[1]) if w else ""
            return (ov[0] if lang == "zh" else ov[1]).format(n=number, tgt=tw, tgt1=tw)

        e = self.dict.get(op)
        if not e:
            return ("%s %s" % (op, number)).strip() if number else op
        tpl = (e.get(lang) or "").strip() or (e.get("en") or "").strip()
        if not tpl:
            return op
        # 目标词：**模板里有目标占位符就填**（否则会留下孤立的连接词，如"对施加 3 层中毒"）。
        # 单目标是"敌人"，全体是"所有敌人"。模板没有该槽的地方不要硬加。
        tgt_word = ""
        if _P_MULT.search(tpl):
            w = _TGT_WORD.get(mult or "Single")
            tgt_word = (w[0] if lang == "zh" else w[1]) if w else ""
        txt = _P_MULT.sub(tgt_word, tpl)
        txt = _P_EXTRA.sub("", txt)      # ⚠️ 先吃掉，免得被 _P_NUM 当数值填（会造成 66）
        if number:
            txt = _P_NUM.sub(str(number), txt)
        else:
            txt = _P_NUM.sub("", txt)
        txt = _P_REF.sub(lambda m: self.term(m.group(2), lang), txt)
        txt = _P_JUNK.sub("", txt)
        txt = _P_ANY.sub("", txt)      # 兜底清残留占位符
        txt = re.sub(r"\s+", " ", txt).strip(" ,，。、")
        return txt

    @classmethod
    def target_count(cls, mult):
        """目标数 → 倍率。All/AllButSelf 未知场上敌人数时按 1 算（不虚报）。"""
        if not mult:
            return 1
        if mult in cls._MULT_SINGLE:
            return 1
        return 1   # All 系列需要场上敌人数，本函数拿不到 ⇒ 保守取 1

    # ---------- 汇总 ----------
    def summary(self, intents, cost=-1, lang="zh"):
        """一张牌的意图列表 → {text, 各类数值合计, items}。

        intents 直接吃 agent.js `cardIntents()` 的输出：
          [{number, mult, intent, ...}, …]
        """
        deal = heal = shield = 0
        draw = energy = 0
        texts, items, unknown = [], [], []
        for it in intents or []:
            op = (it.get("intent") or "").strip()
            n = int(it.get("number") or 0)
            mult = it.get("mult") or "Single"
            k = self.target_count(mult)
            s = self.sem.get(op)
            if not op:
                continue
            if s is None:
                unknown.append(op)
            else:
                m = s.get("metric")
                if m == "damage":
                    deal += n * k
                elif m == "heal":
                    heal += n
                elif m == "shield":
                    shield += n
                elif m == "draw":
                    draw += n
                elif m == "energy":
                    energy += n
            t = self.render(op, n, mult, lang)
            if t:
                texts.append(t)
            items.append({
                "op": op, "n": n, "mult": mult,
                "cat": (s or {}).get("cat", ""),
                "catLabel": self.cats.get((s or {}).get("cat", ""), ""),
                "metric": (s or {}).get("metric", ""),
                "text": t, "tip": self.tip(op),
            })
        return {
            "text": "；".join(texts),
            "cost": cost,
            "deal": deal, "heal": heal, "shield": shield,
            "draw": draw, "energy": energy,
            "count": len(items), "items": items,
            "unknown": unknown,
        }

    def score(self, summ, enemies=1, hp_ratio=1.0):
        """战斗收益粗评（阶段 3 的输入项之一，不是最终决策）。

        权重是**可调的启发式**，刻意保守：只算确定性收益，不猜 combo。
        对敌人多的局面伤害类按 enemies 放大。
        """
        s = 0.0
        s += summ["deal"] * 1.0
        s += summ["shield"] * 0.8
        s += summ["heal"] * 0.7 * (1.6 if hp_ratio < 0.6 else 1.0)   # 血少时治疗更值钱
        s += summ["draw"] * 1.2
        s += summ["energy"] * 1.5
        # debuff 层数按目标数折算（多目标更值）
        for it in summ.get("items", []):
            if it.get("cat") == "debuff" and it.get("n"):
                s += min(it["n"], 12) * 0.45 * (1 + 0.5 * (enemies - 1))
        c = summ.get("cost") or 0
        summ["score"] = round(s, 2)
        summ["perCost"] = round(s / c, 2) if c > 0 else None
        return summ["score"]


# 便捷：单例（GUI 里反复用）
_reader = None


def reader():
    global _reader
    if _reader is None:
        _reader = IntentReader()
    return _reader
