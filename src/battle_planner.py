# -*- coding: utf-8 -*-
"""战斗打法推演 —— 拿手牌的效果（意图）算「怎么打最划算」。

这是「让面板理解卡牌效果」的**下游用途**：intent_reader 告诉你每张牌干什么，
本模块在手牌 + 精力 + 敌人状态的约束下，搜出一个较好的出牌顺序。

⚠️ 定位是**启发式顾问**，不是求解器。原因写明白，免得后人当成"游戏真值"：
  · 游戏里大量效果是**联动**的（触发链、状态层数、牌与牌的引用），本模块只建模
    直接数值 + 少数已知状态倍率（易伤/虚弱/中毒），联动一概不猜。
  · 敌人行为、抽牌随机、洗牌都不建模 —— 只算"这一手牌此刻能打出多少"。
  · 所以输出必须带 `assumptions` 与 `confidence`，让用户知道哪部分是算出来的、
    哪部分没算。宁可说"不知道"，也不给一个看着精确的错答案。

搜索策略：
  · 手牌 ≤ `MAX_EXACT` 张且精力够时，**穷举**所有可打出序列（含"少打一张"），
    取目标函数最高者 —— 小规模下这是精确最优，不是近似。
  · 超过就退化成**贪心 + 剪枝**：每步选边际收益最高的，重复到打不动为止。
  · 两种路径都真实模拟了精力消耗与状态倍率，不是简单按分数排序。
"""
import itertools
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLE = getattr(__import__("sys"), "_MEIPASS", None)
if _BUNDLE:
    _HERE = _BUNDLE


def _load(name, default):
    for d in (_HERE, os.path.join(_HERE, "src")):
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            continue
    return default


# ---- 状态模型（人工判读，见 tools/status_model_build.py 的判读红线）----
# 只装"能算的"；文案没给数值的一律不在表里 ⇒ 推演不计入（不猜）。
_SM = _load("status_model.json", {})
STATUS_MODEL = _SM.get("items") or {}
STATUS_KIND_LABEL = _SM.get("kindLabel") or {}

# 意图 op → 状态规范名（tools/status_intent_map_build.py 生成）
_SIM = _load("status_intent_map.json", {})
STATUS_APPLY = _SIM.get("apply") or {}      # 施加到敌人
STATUS_SELF = _SIM.get("self") or {}        # 加到自己
STATUS_NOTE = _SIM.get("note") or {}        # 生成带状态的牌（不当挂状态）

# 超过这个张数就不再穷举（n! 增长；6 张 = 720×子集，已在毫秒级边缘）
MAX_EXACT = 6
# 穷举时的节点上限，兜底防某个手牌把 CPU 占住（命中就转贪心）
MAX_NODES = 200000


def status_kind(name):
    """状态名 → kind（模型里没有 = 'flavor'，推演不计入）。"""
    return (STATUS_MODEL.get(name) or {}).get("kind") or "flavor"


def status_is_computable(name):
    return bool((STATUS_MODEL.get(name) or {}).get("computable"))


def status_label(name, stacks=0):
    """状态 → 一句人话（给 UI/日志），带层数。模型里没有就只给名字。"""
    rec = STATUS_MODEL.get(name)
    if not rec:
        return name
    lab = rec.get("label") or name
    p = rec.get("param")
    try:
        n = int(stacks or 0)
    except (TypeError, ValueError):
        n = 0
    txt = lab
    if "{n}" in txt:
        txt = txt.replace("{n}", str(n))
    if "{p}" in txt:
        txt = txt.replace("{p}", ("%g" % float(p)) if p is not None else "?")
    return "%s %s" % (rec.get("zh") or name, txt)


def status_short(name, stacks=0):
    """状态 → **紧凑**中文名（如「易伤×2」）。给界面一行里塞好几个状态用。

    与 `status_label()` 的分工：那个给的是完整口径（"易伤 受击伤害 +100%"），
    适合单独一行；这个只给名字+层数，适合 `③ 咒语虚弱×2 · 再生×2` 这种并列。
    """
    rec = STATUS_MODEL.get(name)
    zh = (rec or {}).get("zh") or name
    try:
        n = int(stacks or 0)
    except (TypeError, ValueError):
        n = 0
    return ("%s×%d" % (zh, n)) if n > 0 else zh



# ---- 增伤类意图：不产生即期伤害，但会抬高**后面**的伤害 ----
# ⚠️ 以前这两个完全没建模 ⇒ 带它们的牌收益恒为 0 ⇒ **永远不会被推荐**
#    （用户报：「当牌组内有其他能够增加额外 buff 的牌时，打法建议内不会提出使用这些牌」）。
#    文案（src/card_intent_dict.json）：
#      IncreaseDamage  「令[某张]卡伤害 [PNUM]」   → 加到**下一张**造成伤害的牌上
#      MultiplyDamage  「[某张]卡伤害 x[PNUM]」    → 乘到**下一张**造成伤害的牌上
#    两张的目标都是「卡牌」(MULTIPLICITY_CARD) —— 即"给某一张牌加 buff"。
#    面板不知道玩家会挑哪张 ⇒ 取最自然的读法：**算在下一张造成伤害的牌上**；
#    若是全体（All/AllButSelf）则本回合一直有效（不消耗）。
_DMG_BONUS = {"IncreaseDamage"}
_DMG_MULT = {"MultiplyDamage"}
_ALL_MULT = ("All", "AllButSelf")


class Enemy(object):
    """一个敌人。

    状态用**通用层数表** `statuses` 表示：{状态名: 层数}，名字取自 agent.js
    的 `readEnemyStatuses()`（StatusEffect 枚举，66 个）。

    ⚠️ 不要再按别的游戏的「易伤 +50% / 虚弱 -25%」想当然建模 —— 那是《杀戮尖塔》
       的模型，**本游戏不是**。本游戏的倍率一律照**游戏自带 tooltip**（src/status_tips.json）：

         VULNERABLE  会额外受到 [P2NUM]% 伤害       ← 层数即百分比，不是"固定 +50%"
         AIRBORNE    难以触及，承受伤害降低 50%
         THORNS      受伤时（除非致命）反击 [PNUM] 点伤害
         CONFUSED    攻击伤害降低 [PNUM]
         ENRAGED/MUTANT  造成 [PNUM] 点额外伤害

    为兼容旧调用，`vulnerable` / `weak` / `poison` 三个参数仍收，但会被翻译成
    statuses 里的对应键（Vulnerable 的"层数"按工具文案当百分比用）。
    """

    def __init__(self, hp, block=0.0, vulnerable=0, weak=0, poison=0, name="",
                 statuses=None, max_hp=None):
        self.hp = float(hp)
        self.block = float(block)
        self.name = name or "敌人"
        # 血量上限：只用于**显示**（"6/10" 那种），推演计算一概不用它。
        # 快照没给（老调用/假数据）就留 None，界面退化成只显示当前血。
        try:
            self.max_hp = float(max_hp) if max_hp not in (None, "") else None
        except (TypeError, ValueError):
            self.max_hp = None
        st = {}
        if statuses:
            for k, v in statuses.items():
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    n = 0
                if n > 0:
                    st[k] = n
        # 旧签名的三个参数 → 合并进 statuses（弱化别名，保持向后兼容）
        if vulnerable and "Vulnerable" not in st:
            st["Vulnerable"] = int(vulnerable)
        if weak and "Confused" not in st:
            st["Confused"] = int(weak)
        if poison and "Poisoned" not in st:
            st["Poisoned"] = int(poison)
        self.statuses = st

    def alive(self):
        return self.hp > 0

    def clone(self):
        return Enemy(self.hp, self.block, name=self.name,
                     statuses=dict(self.statuses), max_hp=self.max_hp)

    def has(self, name):
        return int(self.statuses.get(name) or 0) > 0

    def stacks(self, name):
        try:
            return int(self.statuses.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    # ---- 兼容旧属性（老测试/老调用读它们）----
    @property
    def vulnerable(self):
        return self.stacks("Vulnerable")

    @property
    def weak(self):
        return self.stacks("Confused")

    @property
    def poison(self):
        return float(self.stacks("Poisoned"))

    @property
    def effective_hp(self):
        """真实血量 + 护甲 —— 判断"还要打多少才死"用这个。"""
        return max(0.0, self.hp) + max(0.0, self.block)

    def damage_multiplier(self):
        """打到这个敌人身上的**伤害倍率**（按游戏 tooltip 口径，读 status_model.json）。

        · Vulnerable(kind=taken_pct, param=每层百分比)：**每层 +param% 受伤**
          —— 用户实测口径：**一层易伤 = +50% 伤害** ⇒ 3 层 = +150%（×2.5）。
          ⚠️ 以前按"层数即百分比"（1 层 = +1%）算，易伤几乎不影响排序 —— 那是**错的**
             （见 2026-10-06 用户报的"先易伤再接攻击更划算，算法却把易伤排第三"）。
        · Airborne(kind=taken_mult)：承受伤害降低 50% ⇒ ×0.5（系数来自模型 param）。
        两者可叠加（乘法）。
        ⚠️ Armoured / Decay 的文案**没给数值** ⇒ 模型里标成不可算，这里**不计入**，
           宁可低估也不编一个数。
        """
        m = 1.0
        for name, st in self.statuses.items():
            k = status_kind(name)
            if k == "taken_mult":
                p = (STATUS_MODEL.get(name) or {}).get("param")
                try:
                    m *= float(p)
                except (TypeError, ValueError):
                    pass
            elif k == "taken_pct":
                p = (STATUS_MODEL.get(name) or {}).get("param")
                try:
                    per = float(p)
                except (TypeError, ValueError):
                    per = 100.0        # 模型没给 param 时退回"层数即百分比"
                m *= (1.0 + int(st) * per / 100.0)
        return m

    def counter_damage(self):
        """被近战打中时的反伤（kind=counter；Thorns：「除非伤害致命，否则反击 N 点」）。"""
        total = 0
        for name, st in self.statuses.items():
            if status_kind(name) == "counter":
                total += int(st)
        return total

    def alive_states(self):
        """这个敌人身上**会对本回合计算产生影响**的状态（给 UI/描述用）。"""
        out = []
        for name, st in sorted(self.statuses.items()):
            if status_is_computable(name):
                out.append({"name": name, "stacks": int(st),
                            "kind": status_kind(name),
                            "text": status_label(name, st)})
        return out


# ⚠️ 游戏**没给敌人赋名字**（见 agent.js readEnemy 顶部的双证结论）⇒ 面板按
#    「从左到右」编号称呼它们。①②③ 只是给 UI 认位置用的，不代表任何游戏语义。
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def enemy_label(seq):
    """1 起的序号 → 显示用标签（①…⑩，超出退 (11)）。seq<=0 时给空串。"""
    try:
        i = int(seq)
    except (TypeError, ValueError):
        return ""
    if i <= 0:
        return ""
    if i <= len(_CIRCLED):
        return _CIRCLED[i - 1]
    return "(%d)" % i


class State(object):
    """推演用的局面。player_hp/energy 是当前值；enemies 会被就地模拟。

    `player_statuses` 记玩家侧的增益/惩罚（Charged/Blessed/SpellWeakness…），
    形如 {状态名: 层数}。⚠️ 只有 SpellWeakness 会对本回合计算产生影响
    （每打一张牌自己受 N 伤）；其余自身状态只做显示，不装作会算。

    增伤链（`IncreaseDamage` / `MultiplyDamage`）用四个量表示：
      `next_bonus`/`next_mult` —— **下一张**造成伤害的牌用掉（用完清零/复位）
      `perm_bonus`/`perm_mult` —— 本回合一直有效（全体增伤时用）
    """

    def __init__(self, player_hp=60.0, energy=3, enemies=None, player_statuses=None):
        self.player_hp = float(player_hp)
        self.energy = int(energy)
        self.enemies = [e.clone() for e in (enemies or [])]
        self.player_statuses = dict(player_statuses or {})
        self.next_bonus = 0.0
        self.next_mult = 1.0
        self.perm_bonus = 0.0
        self.perm_mult = 1.0
        # 本张牌打在谁身上（给界面显示"牌 → 目标血量"用；纯记录，不参与评分）
        self.last_target = None

    def clone(self):
        s = State(self.player_hp, self.energy)
        s.enemies = [e.clone() for e in self.enemies]
        s.player_statuses = dict(self.player_statuses)
        s.next_bonus, s.next_mult = self.next_bonus, self.next_mult
        s.perm_bonus, s.perm_mult = self.perm_bonus, self.perm_mult
        s.last_target = self.last_target
        return s

    def take_damage_bonus(self):
        """取这次伤害要用的 (加值, 乘数)，并把"下一张"那两个量消耗掉。"""
        b = self.perm_bonus + self.next_bonus
        m = self.perm_mult * self.next_mult
        self.next_bonus = 0.0
        self.next_mult = 1.0
        return b, m

    def player_stacks(self, name):
        try:
            return int(self.player_statuses.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    def alive_enemies(self):
        return [e for e in self.enemies if e.alive()]

    @property
    def enemy_hp_total(self):
        return sum(e.effective_hp for e in self.alive_enemies())

    @property
    def enemy_raw_total(self):
        return sum(max(0.0, e.hp) for e in self.alive_enemies())


# ---- 把 intent_reader 的汇总折算成"这一张牌打出去会发生什么" ----
def effects_of(summ):
    """intent_reader.summary() 的结果 → 推演用的 effects。

    `aoe` = 这条效果是否是"打所有敌人"。判据是**意图的目标数**（mult=All/AllButSelf），
    不是猜的 —— 直接看 summary 里 items 的 mult。

    `bonus` / `mult` / `*_all` 是**增伤类**（IncreaseDamage / MultiplyDamage）：
    它们本身不掉血，但会抬高后面那张伤害牌的数值（见 `_DMG_BONUS` 的说明）。
    """
    summ = summ or {}
    aoe = any((it.get("mult") in _ALL_MULT) for it in (summ.get("items") or []))
    bonus = bonus_all = 0.0
    mult = mult_all = 1.0
    for it in (summ.get("items") or []):
        op = it.get("op") or ""
        try:
            n = float(it.get("n") or 0)
        except (TypeError, ValueError):
            n = 0.0
        if n <= 0:
            continue
        all_t = it.get("mult") in _ALL_MULT
        if op in _DMG_BONUS:
            if all_t:
                bonus_all += n
            else:
                bonus += n
        elif op in _DMG_MULT:
            if all_t:
                mult_all *= n
            else:
                mult *= n
    return {
        "deal": float(summ.get("deal") or 0),
        "shield": float(summ.get("shield") or 0),
        "heal": float(summ.get("heal") or 0),
        "draw": float(summ.get("draw") or 0),
        "energy": float(summ.get("energy") or 0),
        "aoe": aoe,
        "bonus": bonus, "bonusAll": bonus_all,
        "mult": mult, "multAll": mult_all,
    }


def card(name, cost, summary):
    """组一张推演用的牌。summary 来自 intent_reader.summary()。"""
    return {"name": name, "cost": int(cost or 0),
            "summary": summary, "effects": effects_of(summary)}


def _target_info(e, aoe=False):
    """把"这一步打在谁身上"记成给界面看的紧凑结构。

    ⚠️ 取的是**打之前**的血 —— 用户要的就是"这个对象当下多少血"，
       用它来判断该优先打谁。纯记录，不参与任何评分。
    """
    if e is None:
        return None
    return {"label": e.name, "hp": max(0.0, e.hp),
            "maxHp": e.max_hp, "block": max(0.0, e.block), "aoe": bool(aoe)}


def _apply_damage(state, amount, targets="one", counter=True):
    """按目标数把伤害分配到敌人身上；护甲先吃，状态倍率加成，超杀不浪费到下一个。

    ⚠️ 倍率来自 `Enemy.damage_multiplier()`（按游戏 tooltip：Vulnerable 层数当百分比、
       Airborne 减半），**不是**写死的 1.5。
    ⚠️ 增伤链：先把 `state.take_damage_bonus()` 拿到的 (加值, 乘数) 施加到这张牌的伤害上，
       再把结果按敌人各自的受伤倍率分配。「下一张」的加值/乘数**在这里被消耗掉**。
    ⚠️ 反伤（Thorns）：本游戏的 Thorns 是"受伤时除非伤害致命，否则反击 N 点"。
       这里按"没打死就吃 N 点反伤"记进 state.player_hp（推演是启发式，只做保守估计）。
    """
    out = 0.0
    alive = state.alive_enemies()
    if not alive:
        return 0.0
    bonus, mult = state.take_damage_bonus()
    amount = (amount + bonus) * mult
    tgts = alive if targets == "all" else alive[:1]
    if tgts:
        state.last_target = _target_info(tgts[0], targets == "all")
    for e in tgts:
        dmg = amount * e.damage_multiplier()
        absorbed = min(e.block, dmg)
        e.block -= absorbed
        dmg -= absorbed
        killed = min(e.hp, dmg)
        e.hp -= killed
        out += killed
        # 反伤：只有"这一击没打死它"才触发（tooltip: 除非伤害致命）
        if counter and e.hp > 0:
            cd = e.counter_damage()
            if cd > 0:
                state.player_hp -= cd
    return out


def play_card(state, card):
    """把一张牌真正打到局面上（就地改 state）。返回这一步的实际收益描述。

    ⚠️ 只处理**确定性**的部分：直接伤害/护甲/治疗/抽牌/精力/状态层数。
       "生成一张牌""改造牌"这类不产生即期数值的，仅记入 `utility`，不装作会算。
    """
    eff = card.get("effects") or {}
    summ = card.get("summary") or {}
    gained = {"damage": 0.0, "block": 0.0, "heal": 0.0, "energy": 0.0, "draw": 0.0,
              "status": []}

    mult_all = bool(eff.get("aoe"))
    n = float(eff.get("deal") or 0)
    state.last_target = None                 # 每张牌重新记一次目标
    if n > 0:
        gained["damage"] = _apply_damage(state, n, "all" if mult_all else "one")
        gained["target"] = state.last_target

    b = float(eff.get("shield") or 0)
    if b > 0:
        gained["block"] = b

    h = float(eff.get("heal") or 0)
    if h > 0:
        state.player_hp += h
        gained["heal"] = h

    dr = float(eff.get("draw") or 0)
    gained["draw"] = dr

    en = float(eff.get("energy") or 0)
    if en > 0:
        state.energy += int(en)
        gained["energy"] = en

    # ---- 增伤类（IncreaseDamage / MultiplyDamage）----
    # ⚠️ 放在直接伤害**之后**：这类意图的目标是"另一张卡"，不该给本张牌自己加成。
    b, ba = float(eff.get("bonus") or 0), float(eff.get("bonusAll") or 0)
    mu, ma = float(eff.get("mult") or 1), float(eff.get("multAll") or 1)
    if ba:
        state.perm_bonus += ba
    if b:
        state.next_bonus += b
    if ma != 1:
        state.perm_mult *= ma
    if mu != 1:
        state.next_mult *= mu
    if b or ba or mu != 1 or ma != 1:
        gained["buff"] = {"bonus": b + ba, "mult": (mu * ma) if (mu != 1 or ma != 1) else 1.0}

    # ---- 状态层数：意图 op → 状态名（表来自 status_intent_map.json）----
    # ⚠️ 这里**不再硬编码** ApplyVulnerable/ApplyPoisoned 那一小撮 —— 22 个 Apply
    #    意图全部走映射表，漏了谁一眼能看出来。映射不到的（生成牌类）忽略。
    for it in summ.get("items") or []:
        op = it.get("op") or ""
        amt = int(it.get("n") or 0)
        if amt <= 0 or not op:
            continue
        tgt_all = it.get("mult") in ("All", "AllButSelf")
        tgts = state.alive_enemies() if tgt_all else state.alive_enemies()[:1]
        if op in STATUS_APPLY:
            name = STATUS_APPLY[op]
            for e in tgts:
                e.statuses[name] = e.stacks(name) + amt
                gained["status"].append({"who": "enemy", "name": name, "n": amt})
            # 挂状态也算"打在谁身上"（界面同样要显示对象血量）
            if tgts and not gained.get("target"):
                gained["target"] = _target_info(tgts[0], tgt_all)
            # 中毒：本游戏 Poisoned 的官方口径没有 tooltip（不在 enum 里），
            # 语义层按"伤害"归类 ⇒ 保守地只记层数、**不立即扣血**（不猜结算时机）。
        elif op in STATUS_SELF:
            name = STATUS_SELF[op]
            state.player_statuses[name] = state.player_statuses.get(name, 0) + amt
            gained["status"].append({"who": "self", "name": name, "n": amt})

    # ---- 敌人身上的"打牌惩罚" ----
    # SpellWeakness（kind=player_punish）挂在**敌人**身上，文案是
    # 「当你打出一张牌时，将会受到 N 点伤害」⇒ 每打一张牌结算一次。
    # ⚠️ 本张牌若自己挂上了它，这里也会立刻算一次（时序未实测，取保守侧）。
    punish = 0
    for e in state.alive_enemies():
        for name, st in e.statuses.items():
            if status_kind(name) == "player_punish":
                punish += int(st)
    if punish > 0:
        state.player_hp -= punish

    state.energy -= int(card.get("cost") or 0)
    return gained


def objective(state, start):
    """局面评分：扣完的敌人血最多、自己掉血最少。

    权重刻意简单可解释（不搞黑箱）：主要看"净削减的敌人有效血量"，
    其次看治疗/未用掉的精力（未用 = 浪费）。
    """
    killed = start.enemy_hp_total - state.enemy_hp_total
    score = killed * 2.0
    score -= max(0.0, start.player_hp - state.player_hp) * 0.5
    # ⚠️ 精力结余给的是**微量**权重，但它是正数 —— 一张 0 费、什么都没干的牌
    #    会因此拿到 +0.15 而被判成"值得打"（踩过：`plan` 会推荐空牌）。
    #    ⇒ 只有**真削减了血**才允许拿这个奖励。
    if killed > 0:
        score += state.energy * 0.05
    return score


def _playable(state, hand, used):
    out = []
    for i, c in enumerate(hand):
        if i in used:
            continue
        if int(c.get("cost") or 0) <= state.energy:
            out.append(i)
    return out


def plan(hand, energy=3, enemies=None, player_hp=60.0, max_cards=None,
         player_statuses=None):
    """算这一手怎么打。返回 {sequence, damage, ...} 或 {ok: False, reason}。

    hand 里每张牌形如：
      {"name": "Hammer", "cost": 1, "summary": intent_reader.summary(...),
       "effects": {"deal":…, "shield":…, "heal":…, "draw":…, "energy":…, "aoe":bool}}

    `player_statuses` 是玩家侧状态层数（从快照的 `playerStatuses` 来）；
    只有 SpellWeakness 会影响本回合计算。
    """
    hand = [c for c in (hand or []) if c]
    if not hand:
        return {"ok": False, "reason": "手牌为空"}
    if not (enemies and any(e.alive() for e in enemies)):
        return {"ok": False, "reason": "场上没有敌人（推演只在战斗中有意义）"}

    start = State(player_hp, energy, enemies, player_statuses=player_statuses)
    limit = len(hand) if max_cards is None else min(len(hand), max_cards)
    cands = hand[:limit]

    best = {"score": None, "seq": [], "state": start.clone(), "gains": []}

    def consider(seq, state, gains):
        sc = objective(state, start)
        if best["score"] is None or sc > best["score"]:
            best.update({"score": sc, "seq": list(seq), "state": state.clone(),
                         "gains": list(gains)})

    exact = len(cands) <= MAX_EXACT
    nodes = [0]
    if exact:
        # 穷举所有「有序子序列」：每一步从还没打过的牌里挑一张能打的
        def dfs(state, seq, used, gains):
            nodes[0] += 1
            consider(seq, state, gains)
            if nodes[0] > MAX_NODES:
                return
            for i in _playable(state, cands, used):
                st = state.clone()
                g = play_card(st, cands[i])
                dfs(st, seq + [i], used | {i}, gains + [g])
        dfs(start.clone(), [], set(), [])
    else:
        # 贪心：每步取"边际收益最大"的那张，直到打不动
        st = start.clone()
        seq = []
        gains = []
        used = set()
        while True:
            best_i, best_gain, best_st, best_g = None, -1e9, None, None
            for i in _playable(st, cands, used):
                s2 = st.clone()
                g = play_card(s2, cands[i])
                sc = objective(s2, start) - objective(st, start)
                if sc > best_gain:
                    best_i, best_gain, best_st, best_g = i, sc, s2, g
            if best_i is None or best_gain <= 0:
                break
            st = best_st
            seq.append(best_i)
            gains.append(best_g)
            used.add(best_i)
        consider(seq, st, gains)

    if best["score"] is None:
        return {"ok": False, "reason": "没有任何可打出的牌（精力不够？）"}

    end = best["state"]
    seq_cards = [cands[i] for i in best["seq"]]
    spent = sum(int(c.get("cost") or 0) for c in seq_cards)
    # 出牌过程中挂上的状态汇总（给 UI 显示"这一手会留下什么"）
    # ⚠️⚠️ 显示给用户的血量用**开局的实时值**（`start`），**不是**推演模拟到那一步的预测值。
    #    原因（用户实测报的"出牌后血量没法实时刷新"）：预测值是"假设前面几步都打完了"的
    #    中间态；玩家打完一张牌再看面板，看到的还是旧的预测，跟屏幕上对不上。
    #    用户要的是"这一步打谁 + 它现在多少血" ⇒ 一律取开局快照的血（每轮推演都会重算 ⇒ 实时）。
    _live = {}
    for _e in start.enemies:
        _live.setdefault(_e.name, _e)
    applied = {}
    per_card = []
    for g in best["gains"]:
        st_list = list(g.get("status") or [])
        tgt = g.get("target")
        if tgt:
            e0 = _live.get(tgt.get("label"))
            if e0 is not None:
                tgt = dict(tgt, hp=max(0.0, e0.hp), maxHp=e0.max_hp,
                           block=max(0.0, e0.block))
        per_card.append({"applied": st_list, "target": tgt})
        for s in st_list:
            key = ("self:" if s["who"] == "self" else "") + s["name"]
            applied[key] = applied.get(key, 0) + int(s["n"])
    return {
        "ok": True,
        "sequence": [{"index": i, "name": cands[i].get("name", ""),
                      "cost": int(cands[i].get("cost") or 0),
                      "applied": (per_card[k]["applied"] if k < len(per_card) else []),
                      "target": (per_card[k]["target"] if k < len(per_card) else None)}
                     for k, i in enumerate(best["seq"])],
        "order": best["seq"],
        "spentEnergy": spent,
        "leftEnergy": end.energy,
        "damageDealt": round(start.enemy_hp_total - end.enemy_hp_total, 1),
        "enemiesLeft": len(end.alive_enemies()),
        "enemyHpAfter": round(end.enemy_hp_total, 1),
        "lethal": len(end.alive_enemies()) == 0,
        "appliedStatuses": applied,
        "method": "exact" if exact else "greedy",
        "nodes": nodes[0],
        "assumptions": [
            "只算确定性收益（伤害/护甲/治疗/抽牌/精力/状态层数/增伤），不猜牌与牌的联动",
            "敌人本回合不还手、不建模抽牌与洗牌",
            "状态倍率按游戏官方 tooltip（src/status_tips.json）读取，"
            "文案没给数值的状态（如 Armoured/Decay）**不计入**，宁可低估",
            "本游戏易伤（Vulnerable）的**每层 = +50% 受伤**（实测口径）；"
            "增伤牌（令卡牌伤害 +N / ×N）算在**下一张造成伤害的牌**上",
            "护甲优先吸收伤害；超杀不转移到下一个目标",
        ],
        "confidence": "high" if exact else "medium",
    }


def describe(res):
    """把 plan() 的结果说成一句人话（给界面/日志用）。"""
    if not res.get("ok"):
        return "无法推演：%s" % res.get("reason", "")
    if not res["sequence"]:
        return "这一手没有值得打出的牌（当前精力下无正收益）。"
    names = " → ".join("%s[%d]" % (s["name"], s["cost"]) for s in res["sequence"])
    tail = "，可斩杀！" if res["lethal"] else "，剩敌 %d 只 / 共 %.0f 血。" % (
        res["enemiesLeft"], res["enemyHpAfter"])
    return "建议：%s；预计削减 %.0f 血，余力 %d%s（%s）" % (
        names, res["damageDealt"], res["leftEnergy"], tail,
        "精确穷举" if res["method"] == "exact" else "贪心近似")


# ---------------- 内存战况 → 推演输入（端到端桥接） ----------------
def _status_dict(raw):
    """agent.js 读出的状态数组 → {规范名: 层数}。

    输入形如 `[{"name": "Vulnerable", "number": 2, "raw": "0x…"}]`。
    ⚠️ `number` 可能是 0（元素是 StatusEffect 资产本身、层数在别处）——
       这时**保留该状态**（层数记 1？不 —— 记 0 但键存在）。
       本游戏的"存在性"状态（Armoured/Stunned）本来就没有层数，
       而"层数型"状态（Vulnerable）读不到数时记 0 会让倍率失效。
       ⇒ 折中：number<=0 但状态**可算**时记 1（最小存在性），否则记 0。
    """
    out = {}
    for s in (raw or []):
        if not isinstance(s, dict):
            continue
        nm = (s.get("name") or "").strip()
        if not nm:
            continue
        try:
            n = int(s.get("number") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            n = 1 if status_is_computable(nm) else 0
        out[nm] = out.get(nm, 0) + n
    return out


def from_snapshot(snapshot, reader=None):
    """agent.js `battle()` 的战况快照 → (hand, energy, enemies, player_hp)。

    snapshot 形如：
      { hp, energy, enemies:[{name, seq, hp, armour, alive, retreated,
                              statuses:[{name, number, raw}]}],
        playerStatuses:[{name, number}],
        hand:[{name, cost, intents:[{intent, number, mult, …}]}] }

    ⚠️ 手牌元素必须带 `intents`（即 `battle(withIntents=True)`）；否则每张牌
       效果为空，推演会得出"打不出任何收益"的假结论 —— 这里会显式提示。
    ⚠️ 敌人只取 **alive 且未撤退** 的（撤退的敌人不该计入斩杀目标）。
    ⚠️ 敌人的 `name` 不可信（游戏没赋名字）⇒ 优先用 `seq` 编号当显示名；
      seq 缺失时才退回 name，再退回"敌人"。
    ⚠️ 状态读不出来时（`statusDiag` 非空）**不静默**：在 note 里说明，
      因为状态会直接改伤害倍率，安静地当成"没状态"会让推演偏乐观。
    """
    if reader is None:
        from intent_reader import reader as _get
        reader = _get()

    snap = snapshot or {}
    hand_raw = snap.get("hand") or []
    missing = [c for c in hand_raw if not (c.get("intents"))]

    hand = []
    for c in hand_raw:
        summ = reader.summary(c.get("intents") or [], cost=c.get("cost", -1))
        hand.append(card(reader.card_name(c.get("name") or ""),
                         c.get("cost") or 0, summ))

    enemies = []
    status_unknown = 0
    for e in (snap.get("enemies") or []):
        if not e.get("alive", True) or e.get("retreated"):
            continue
        lbl = enemy_label(e.get("seq"))
        st = _status_dict(e.get("statuses"))
        if e.get("statusDiag"):
            status_unknown += 1
        enemies.append(Enemy(hp=e.get("hp") or 0,
                             block=e.get("armour") or 0,
                             name=lbl or (e.get("name") or ""),
                             statuses=st,
                             max_hp=e.get("maxHp")))

    player_statuses = _status_dict(snap.get("playerStatuses"))

    energy = snap.get("energy")
    if energy is None or int(energy) < 0:
        energy = 0
    player_hp = snap.get("hp")
    if player_hp is None or float(player_hp) < 0:
        player_hp = 60.0

    notes = []
    if missing:
        notes.append("有 %d 张手牌没带效果（要用 battle(withIntents=True) 读），"
                     "本次推演会低估它们" % len(missing))
    if status_unknown:
        notes.append("有 %d 只敌人的状态没读出来（状态会改伤害倍率），"
                     "本次推演可能偏乐观" % status_unknown)
    return {
        "hand": hand, "energy": int(energy), "enemies": enemies,
        "player_hp": float(player_hp),
        "player_statuses": player_statuses,
        "deckTypeName": snap.get("deckTypeName") or "",
        "warning": snap.get("warning") or "",
        "note": "；".join(notes),
    }


def plan_from_snapshot(snapshot, reader=None, **kw):
    """一步到位：内存战况 → 出牌建议。返回 (方案, 桥接信息)。

    `方案` 就是 plan() 的返回值（ok=False 时 reason 说明为什么算不了）。
    """
    br = from_snapshot(snapshot, reader=reader)
    res = plan(br["hand"], energy=br["energy"], enemies=br["enemies"],
               player_hp=br["player_hp"], player_statuses=br["player_statuses"], **kw)
    if br["note"]:
        res.setdefault("assumptions", []).insert(0, br["note"])
    res["bridge"] = {
        "handCount": len(br["hand"]), "energy": br["energy"],
        "enemyCount": len(br["enemies"]), "playerHp": br["player_hp"],
        "deckTypeName": br["deckTypeName"], "battleWarning": br["warning"],
        "enemyStatuses": [{"name": e.name, "statuses": dict(e.statuses)}
                          for e in br["enemies"] if e.statuses],
        "playerStatuses": dict(br["player_statuses"]),
    }
    return res, br

