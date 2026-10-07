/*
 * intent_probe.js —— 「卡牌效果」标定探针（**只读**，不挂钩任何函数、不改游戏状态）
 *
 * 目标：验证面板能否读懂手牌的效果 —— 即读出每张牌的
 *       `List<CardIntentPair>` → 每条 (CardIntent 名称, Number, 目标数, 高级修饰)。
 *
 * 字段偏移来源：Il2CppDumper v6.7.46 对 Il2Cpp v31 的 dump（本机 0.6.41 版本，`dump.cs`）。
 *   GameplayCardData.<Intents>k__BackingField : List<CardIntentPair>   @ 0x148
 *     ⚠️ 泛型基类 AbstractCardData<TChild> 的字段 dump 里全印成 0x0，
 *        但 <Intents> 定义在 **GameplayCardData 自身**上（非泛型基类）⇒ 0x148 可用。
 *   CardIntentPair._intent           : CardIntent (ScriptableEnum)  @ 0x20
 *   CardIntentPair.Number            : int32                        @ 0x28
 *   CardIntentPair.AdvancedIntentBehaviour : bool                   @ 0x38
 *   CardIntentPair.TargetMultiplicity      : Multiplicity(enum i32) @ 0x7C
 *   CardIntentPair.TargetOverride    : IntentTargetType(enum i32)   @ 0x80
 *   CardIntentPair.NegateArmour      : bool                         @ 0xC0
 *   CardIntentPair.SkipArmour        : bool                         @ 0xC1
 *   ScriptableEnum._assetIndex       : int32                        @ 0x18
 *   ScriptableEnum._guid             : string                       @ 0x20
 *   CardIntent.AllowedTargets        : IntentTargetType             @ 0x28
 *   CardIntent.PrimaryTarget         : IntentTargetType             @ 0x2C
 *   CardIntent.HiddenFromPlayer      : bool                         @ 0x50
 *   CardIntent.ProxyImplementation   : CardIntent                   @ 0x80
 *
 * ★ 意图的**名字**（本轮结论）：
 *   `CardIntent` 是 ScriptableEnum，实例上只有 `_guid`/`_assetIndex`，**没有名字**。
 *   但类上有 176 个 `protected static CardIntent __xxx` 静态字段（dump.cs 第 9133 行起），
 *   每个字段名 = 意图名（去掉 `__`、首字母大写）。
 *   ⇒ **零硬编码方案**：用 `il2cpp_class_get_fields` 迭代静态字段，读它的值得到
 *      `CardIntent*` ⇒ 建 `指针 → 意图名` 反向表。游戏更新/换版本都不用改代码。
 *      （顺序方案不可靠：字段顺序在 metadata 里被裁剪过，dump 里有 176 个而
 *       `get_XXX` 只有 171 个 —— 缺的 5 个正好是 Explode/GroupUp/DealDamage/Enrage/Nothing。）
 *
 * 运行：游戏运行中 → `python tools/intent_probe.py`
 *       （跑前请先断开面板：Frida 单 session + 绝不重复注入出货 agent.js）
 */

'use strict';

const MODULE_NAME = 'GameAssembly.dll';

const OFF = {
    listItems: 0x10,        /* List<T>._items */
    listSize: 0x18,         /* List<T>._size  */
    arrayData: 0x20,        /* Il2CppArray 元素起始 */
    strLen: 0x10,
    strChars: 0x14,

    cardIntents: 0x148,     /* GameplayCardData.<Intents>k__BackingField */

    pairIntent: 0x20,
    pairNumber: 0x28,
    pairAdvanced: 0x38,
    pairMultiplicity: 0x7C,
    pairTargetOverride: 0x80,
    pairNegateArmour: 0xC0,
    pairSkipArmour: 0xC1,

    seAssetIndex: 0x18,
    seGuid: 0x20,

    intentAllowedTargets: 0x28,
    intentPrimaryTarget: 0x2C,
    intentHidden: 0x50,
    intentProxy: 0x80,
};

/* 世界管理器 / 模型列表偏移（与 agent.js 保持一致） */
const OFF_WM = {
    listItems: 0x10, listSize: 0x18, arrayData: 0x20,
    mainWorld: 0x48, futureWorld: 0x50,
    pureModels: 0x10, runtimeModels: 0x20,
};

const MULTIPLICITY = ['None', 'Single', 'AllButSelf', 'All', 'Random', 'ToTheLeft', 'ToTheRight', 'Self'];
const INTENT_TARGET = { 0: 'None', 1: 'Player', 2: 'Card', 4: 'EnemyWorld' };
/* IL2CPP FieldInfo 的字段类型标志位（il2cpp-api.h） */
const FIELD_STATIC = 0x10;
/* `CardIntent` 上以 `__` 开头但**不是**意图的静态字段（dump.cs 9131/9132 行） */
const EXCLUDE_FIELDS = { '__allCardIntents': 1, '__all': 1 };

let ga = null, gaSize = 0;
const fn = {};
const NULL = ptr(0);

function bind() {
    const mod = Process.getModuleByName(MODULE_NAME);
    ga = mod.base;
    gaSize = mod.size;
    const ex = (n) => {
        const a = mod.getExportByName(n);
        if (a === null) throw new Error('缺少导出函数：' + n);
        return a;
    };
    fn.domain_get = new NativeFunction(ex('il2cpp_domain_get'), 'pointer', []);
    fn.domain_get_assemblies = new NativeFunction(ex('il2cpp_domain_get_assemblies'), 'pointer', ['pointer', 'pointer']);
    fn.assembly_get_image = new NativeFunction(ex('il2cpp_assembly_get_image'), 'pointer', ['pointer']);
    fn.class_from_name = new NativeFunction(ex('il2cpp_class_from_name'), 'pointer', ['pointer', 'pointer', 'pointer']);
    fn.class_get_name = new NativeFunction(ex('il2cpp_class_get_name'), 'pointer', ['pointer']);
    fn.class_get_field_from_name = new NativeFunction(ex('il2cpp_class_get_field_from_name'), 'pointer', ['pointer', 'pointer']);
    fn.class_get_fields = new NativeFunction(ex('il2cpp_class_get_fields'), 'pointer', ['pointer', 'pointer']);
    fn.field_get_name = new NativeFunction(ex('il2cpp_field_get_name'), 'pointer', ['pointer']);
    fn.field_get_offset = new NativeFunction(ex('il2cpp_field_get_offset'), 'int', ['pointer']);
    fn.field_get_flags = new NativeFunction(ex('il2cpp_field_get_flags'), 'uint', ['pointer']);
    fn.field_static_get_value = new NativeFunction(ex('il2cpp_field_static_get_value'), 'void', ['pointer', 'pointer']);
    fn.thread_attach = new NativeFunction(ex('il2cpp_thread_attach'), 'pointer', []);
}

function cstr(s) { return Memory.allocUtf8String(s); }

function inModule(p) {
    try {
        return !p.isNull() && p.compare(ga) >= 0 && p.compare(ga.add(gaSize)) < 0;
    } catch (e) { return false; }
}

/* 托管字符串：+0x10 length(int32) / +0x14 utf16 chars */
function readCsString(p) {
    if (p === null || p.isNull() || !inModule(p)) return '';
    try {
        const len = p.add(OFF.strLen).readS32();
        if (len <= 0 || len > 4096) return '';
        return p.add(OFF.strChars).readUtf16String(len) || '';
    } catch (e) { return ''; }
}

function readUtf8(p) {
    if (p === null || p.isNull()) return '';
    try { return p.readUtf8String() || ''; } catch (e) { return ''; }
}

/* 在全部 assembly 里按 (name, namespace) 找类；namespace 空串表示全局 */
function findClass(name, namespace) {
    try {
        const cnt = Memory.alloc(8);
        const asms = fn.domain_get_assemblies(fn.domain_get(), cnt);
        const n = cnt.readU32();
        const ns = cstr(namespace || '');
        const nm = cstr(name);
        for (let i = 0; i < n; i++) {
            const asm = asms.add(i * Process.pointerSize).readPointer();
            if (asm.isNull()) continue;
            const img = fn.assembly_get_image(asm);
            if (img.isNull()) continue;
            const k = fn.class_from_name(img, ns, nm);
            if (!k.isNull()) return k;
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return NULL;
}

const fieldOffCache = {};

function fieldOff(klass, fieldName) {
    if (klass === null || klass.isNull()) return -1;
    const key = klass.toString() + '|' + fieldName;
    if (key in fieldOffCache) return fieldOffCache[key];
    let off = -1;
    try {
        const f = fn.class_get_field_from_name(klass, cstr(fieldName));
        if (!f.isNull()) off = fn.field_get_offset(f);
    } catch (e) { /* 探测失败：跳过这一项 */ }
    fieldOffCache[key] = off;
    return off;
}

function at(obj, off) {
    if (!obj || obj.isNull() || off < 0) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function atI32(obj, off) {
    if (!obj || obj.isNull() || off < 0) return 0;
    try { return obj.add(off).readS32(); } catch (e) { return 0; }
}

function readList(listPtr) {
    const out = [];
    if (!listPtr || listPtr.isNull() || !inModule(listPtr)) return out;
    try {
        const items = listPtr.add(OFF.listItems).readPointer();
        const n = listPtr.add(OFF.listSize).readS32();
        if (n <= 0 || n > 512 || items.isNull() || !inModule(items)) return out;
        for (let i = 0; i < n; i++) {
            out.push(items.add(OFF.arrayData + i * Process.pointerSize).readPointer());
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return out;
}

/* ------------------------------------------------------------------
 * 意图名表：遍历 CardIntent 的静态字段（__xxx）→ { ptr字符串: 名字 }
 * 零硬编码：不依赖字段顺序、不依赖 guid、不依赖外部 JSON。
 * ------------------------------------------------------------------ */
let intentMap = null;       /* Map<string, string> */
let intentMapInfo = null;   /* 诊断信息 */

function buildIntentMap() {
    if (intentMap !== null) return intentMap;
    intentMap = new Map();
    intentMapInfo = { fields: 0, statics: 0, named: 0, sample: [], err: '' };
    try {
        const k = findClass('CardIntent', '');
        if (k.isNull()) { intentMapInfo.err = 'CardIntent 类未找到'; return intentMap; }
        /* il2cpp_field_static_get_value 要求线程已 attach */
        try { fn.thread_attach(fn.domain_get()); } catch (e) { /* 忽略 */ }

        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(NULL);
        const buf = Memory.alloc(Process.pointerSize);
        let guard = 0;
        while (guard++ < 4096) {
            const f = fn.class_get_fields(k, iter);
            if (f.isNull()) break;
            intentMapInfo.fields++;
            const fl = fn.field_get_flags(f);
            if ((fl & FIELD_STATIC) !== 0) {
                intentMapInfo.statics++;
                const nm = readUtf8(fn.field_get_name(f));
                if (nm.indexOf('__') === 0 && !EXCLUDE_FIELDS[nm]) {
                    buf.writePointer(NULL);
                    try { fn.field_static_get_value(f, buf); } catch (e) { continue; }
                    const v = buf.readPointer();
                    if (v.isNull() || !inModule(v)) continue;
                    /* __xxx → Xxx（去掉前导 __，首字母大写）；`__shieldDONTUSE` 去掉内部 DON'T USE 标记 */
                    let pretty = nm.slice(2);
                    const dont = pretty.indexOf('DONTUSE');
                    if (dont > 0) pretty = pretty.slice(0, dont);
                    pretty = pretty.charAt(0).toUpperCase() + pretty.slice(1);
                    const key = v.toString();
                    if (!intentMap.has(key)) {
                        intentMap.set(key, pretty);
                        intentMapInfo.named++;
                        if (intentMapInfo.sample.length < 6) intentMapInfo.sample.push(pretty);
                    }
                }
            }
        }
    } catch (e) {
        intentMapInfo.err = String(e);
    }
    return intentMap;
}

function intentNameOf(p) {
    if (!p || p.isNull()) return '';
    return buildIntentMap().get(p.toString()) || '';
}

/* 一条 CardIntentPair → 可读对象 */
function readPair(p) {
    if (!p || p.isNull() || !inModule(p)) return null;
    const mult = atI32(p, OFF.pairMultiplicity);
    const tgt = atI32(p, OFF.pairTargetOverride);
    const it = {
        number: atI32(p, OFF.pairNumber),
        adv: p.add(OFF.pairAdvanced).readU8() ? 1 : 0,
        negateArmour: p.add(OFF.pairNegateArmour).readU8() ? 1 : 0,
        skipArmour: p.add(OFF.pairSkipArmour).readU8() ? 1 : 0,
        mult: MULTIPLICITY[mult] || ('?' + mult),
        targetOverride: INTENT_TARGET[tgt] || ('?' + tgt),
    };
    const intentPtr = at(p, OFF.pairIntent);
    if (intentPtr && !intentPtr.isNull() && inModule(intentPtr)) {
        const at_ = atI32(intentPtr, OFF.intentAllowedTargets);
        const pt = atI32(intentPtr, OFF.intentPrimaryTarget);
        it.intent = intentNameOf(intentPtr);          /* ★ 名字（静态字段表） */
        it.guid = readCsString(at(intentPtr, OFF.seGuid));
        it.assetIndex = atI32(intentPtr, OFF.seAssetIndex);
        it.allowedTargets = INTENT_TARGET[at_] || ('?' + at_);
        it.primaryTarget = INTENT_TARGET[pt] || ('?' + pt);
        it.hidden = intentPtr.add(OFF.intentHidden).readU8() ? 1 : 0;
        const proxy = at(intentPtr, OFF.intentProxy);
        it.hasProxy = proxy && !proxy.isNull() ? 1 : 0;
        it.intentPtr = intentPtr.toString();
    } else {
        it.intent = '';
        it.guid = '';
        it.assetIndex = -1;
        it.intentPtr = '0x0';
    }
    return it;
}

/* 一张 GameplayCardData → {name, cost, intents:[...]} */
function readCardData(dataPtr) {
    if (!dataPtr || dataPtr.isNull() || !inModule(dataPtr)) return null;
    const out = { ptr: dataPtr.toString(), name: '', cost: -1, intents: [] };

    /* 名字：<Name>k__BackingField 在泛型基类 AbstractCardData<T> 上，偏移动态取 */
    try {
        const k = dataPtr.readPointer();
        for (const f of ['<Name>k__BackingField', '_overrideDisplayName', 'LastLocKey']) {
            const off = fieldOff(k, f);
            if (off < 0) continue;
            const v = readCsString(at(dataPtr, off));
            if (v) { out.name = v; break; }
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }

    /* 费用：同样在基类上，用来算「每点精力收益」（agent.js 走 cardProperties/cardCost，
       探针里没有那套，直接沿父类链找属性列表里的花费字段） */
    try {
        const k = dataPtr.readPointer();
        for (const f of ['<Cost>k__BackingField', 'Cost', '_cost']) {
            const off = fieldOff(k, f);
            if (off < 0) continue;
            out.cost = atI32(dataPtr, off);
            break;
        }
    } catch (e) { /* 读不到就留 -1 */ }

    for (const pair of readList(at(dataPtr, OFF.cardIntents))) {
        const r = readPair(pair);
        if (r) out.intents.push(r);
    }
    return out;
}

/* --------- 世界模型枚举（与 agent.js 同路；不做 GC 堆遍历） --------- */
function listAt(obj, off) {
    if (!obj || obj.isNull()) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function getWorldManagers() {
    const out = [];
    try {
        const k = findClass('S_SharedDBPollManager', '');
        if (k.isNull()) return out;
        /* 静态实例列表 / 单例字段：候选名逐个试，拿到非空且在模块内的就算 */
        for (const f of ['_instance', 'Instance', 's_Instances', '_managers', 'Instances']) {
            const fp = fn.class_get_field_from_name(k, cstr(f));
            if (fp.isNull()) continue;
            const buf = Memory.alloc(Process.pointerSize);
            buf.writePointer(NULL);
            try { fn.field_static_get_value(fp, buf); } catch (e) { continue; }
            const v = buf.readPointer();
            if (v.isNull() || !inModule(v)) continue;
            /* 可能是 List<S_SharedDBPollManager> 也可能是单个 manager */
            const asList = readList(v);
            if (asList.length) { for (const x of asList) if (!x.isNull()) out.push(x); }
            else out.push(v);
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return out;
}

function instancesOf(wantName) {
    const res = [];
    const seen = {};
    const klass = findClass(wantName, '');
    if (klass.isNull()) return res;
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF_WM.mainWorld, OFF_WM.futureWorld]) {
            const world = listAt(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF_WM.pureModels, OFF_WM.runtimeModels]) {
                for (const obj of readList(listAt(world, off))) {
                    if (obj.isNull()) continue;
                    const k = obj.readPointer();
                    if (k.isNull()) continue;
                    /* 精确匹配 or 子类（GameplayCardData 是 AbstractCardData<T> 的子类） */
                    if (readUtf8(fn.class_get_name(k)) !== wantName) continue;
                    const key = obj.toString();
                    if (seen[key]) continue;
                    seen[key] = 1;
                    res.push(obj);
                }
            }
        }
    }
    return res;
}

rpc.exports = {
    /* 环境自检 */
    selfcheck() {
        try {
            bind();
            const k = gameplayCardDataClass();
            buildIntentMap();
            return {
                ok: true,
                ga: ga.toString(),
                size: gaSize,
                klass: k.isNull() ? '0x0' : k.toString(),
                intentsOff: fieldOff(k, '<Intents>k__BackingField'),
                nameOff: fieldOff(k, '<Name>k__BackingField'),
                intentMap: intentMapInfo,
            };
        } catch (e) {
            return { ok: false, err: String(e) };
        }
    },

    /* 意图名表全量（Python 侧可与 card_intent_enum.json 比对） */
    intentNames() {
        bind();
        buildIntentMap();
        const out = [];
        for (const kv of intentMap) out.push(kv[1]);
        return { info: intentMapInfo, names: out };
    },

    /* 给定 GameplayCardData 指针，读它的意图列表 */
    intentsOf(ptrHex) {
        bind();
        return readCardData(ptr(ptrHex));
    },

    /* 采样若干张 GameplayCardData（走世界模型枚举，与 agent.js 同路） */
    sample(limit) {
        bind();
        const n = (typeof limit === 'number' && limit > 0) ? limit : 12;
        const cards = [];
        let all = [];
        try { all = instancesOf('GameplayCardData'); } catch (e) { all = []; }
        let found = 0;
        for (const p of all) {
            if (found >= n) break;
            const c = readCardData(p);
            if (!c) continue;
            /* 只报真的有意图的牌，空意图（占位/地形资产）跳过 */
            if (!c.intents.length) continue;
            cards.push(c);
            found++;
        }
        return { scanned: all.length, cards: cards };
    },
};

let cachedClass = null;
function gameplayCardDataClass() {
    if (cachedClass === null) cachedClass = findClass('GameplayCardData', '');
    return cachedClass;
}
