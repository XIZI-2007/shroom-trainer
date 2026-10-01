/* forget_probe.js —— 「遗忘手牌」功能真机标定探针（只读优先）
 *
 * 目的：在施工前验证以下事实，全部通过后才敢写出货代码。
 *
 *  P1 牌库实例的真实 class 名（M_GameplayDeck / M_ForesightDeck）与
 *     M_Deck<T> 全部字段的**运行时偏移**（dump 里印 // 0x0，必须 field_get_offset 解析）
 *  P2 M_Deck<T>.RemoveCard / TryRemoveSpecificCard / AddToExileCardPile 的
 *     MethodInfo 能否通过 il2cpp_class_get_method_from_name 拿到非空 methodPointer
 *  P3 营地和战斗两套牌库（DeckType 1 / 2）是否同时存在、能否分别定位
 *  P4 「抽牌堆 / 弃牌堆 / 已消耗 / 流放」四堆在营地与战斗下的真实内容
 *
 * ⚠️ 本探针**绝不调用任何托管方法**（不 RemoveCard、不 Exile）。只有 rpc `probeCanary`
 *    会做一次最小侵入验证，且必须显式传参才执行 —— 默认不跑。
 *
 * 用法：python tools/forget_probe.py
 */
'use strict';

const MODULE_NAME = 'GameAssembly.dll';

const ga = Process.findModuleByName(MODULE_NAME);
const gaBase = ga.base;
const gaSize = ga.size;

const OFF = {
    objKlass: 0x00,
    listItems: 0x10,
    listSize: 0x18,
    arrayData: 0x20,
    mainWorld: 0x48,
    futureWorld: 0x50,
    pureModels: 0x10,
    runtimeModels: 0x20,
};

const fn = {};

function bind() {
    const missing = [];
    /* ⚠️ Frida 17 移除了 Module.findExportByName —— 必须用 mod.getExportByName
       （出货 agent.js 里就是这么写的；上一版探针在这里踩了 "not a function"） */
    const mod = Process.getModuleByName(MODULE_NAME);
    const exp = (n) => {
        let a = null;
        try { a = mod.getExportByName(n); } catch (e) { a = null; }
        if (a === null) { missing.push(n); return null; }
        return a;
    };
    const def = (key, name, ret, args) => {
        const a = exp(name);
        if (a !== null) fn[key] = new NativeFunction(a, ret, args);
    };

    def('domain_get', 'il2cpp_domain_get', 'pointer', []);
    def('domain_get_assemblies', 'il2cpp_domain_get_assemblies', 'pointer', ['pointer', 'pointer']);
    def('assembly_get_image', 'il2cpp_assembly_get_image', 'pointer', ['pointer']);
    def('class_from_name', 'il2cpp_class_from_name', 'pointer', ['pointer', 'pointer', 'pointer']);
    def('class_get_name', 'il2cpp_class_get_name', 'pointer', ['pointer']);
    def('class_get_namespace', 'il2cpp_class_get_namespace', 'pointer', ['pointer']);
    def('class_get_field_from_name', 'il2cpp_class_get_field_from_name', 'pointer', ['pointer', 'pointer']);
    def('field_get_offset', 'il2cpp_field_get_offset', 'int', ['pointer']);
    def('class_get_method_from_name', 'il2cpp_class_get_method_from_name', 'pointer', ['pointer', 'pointer', 'int']);
    def('class_get_methods', 'il2cpp_class_get_methods', 'pointer', ['pointer', 'pointer']);
    def('method_get_name', 'il2cpp_method_get_name', 'pointer', ['pointer']);
    def('class_get_parent', 'il2cpp_class_get_parent', 'pointer', ['pointer']);
    def('thread_attach', 'il2cpp_thread_attach', 'pointer', ['pointer']);
    def('field_static_get_value', 'il2cpp_field_static_get_value', 'void', ['pointer', 'pointer']);
    def('class_get_fields', 'il2cpp_class_get_fields', 'pointer', ['pointer', 'pointer']);

    return { missing: missing, bound: Object.keys(fn) };
}

/* ---------- 基础工具 ---------- */
function cstr(s) {
    const b = Memory.allocUtf8String(s);
    return b;
}

function readStr(p) {
    if (p === null || p.isNull()) return '';
    try { return p.readUtf8String() || ''; } catch (e) { return ''; }
}

function readCsString(p) {
    if (p === null || p.isNull()) return '';
    try {
        const len = p.add(0x10).readS32();
        if (len <= 0 || len > 8192) return '';
        return p.add(0x14).readUtf16String(len) || '';
    } catch (e) { return ''; }
}

function readListPtrs(list) {
    const out = [];
    if (list === null || list.isNull()) return out;
    try {
        const items = list.add(OFF.listItems).readPointer();
        const size = list.add(OFF.listSize).readS32();
        if (items.isNull() || size <= 0 || size > 20000) return out;
        for (let i = 0; i < size; i++) {
            out.push(items.add(OFF.arrayData + i * Process.pointerSize).readPointer());
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return out;
}

function listAt(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function atOff(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function i32Off(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return 0;
    try { return obj.add(off).readS32(); } catch (e) { return 0; }
}

function boolOff(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return false;
    try { return obj.add(off).readU8() !== 0; } catch (e) { return false; }
}

function scanListForClass(list, wantName) {
    for (const o of readListPtrs(list)) {
        if (o.isNull()) continue;
        const k = o.readPointer();
        if (k.isNull()) continue;
        if (readStr(fn.class_get_name(k)) === wantName) return o;
    }
    return NULL;
}

/* ---------- 类查找 ---------- */
let gImage = null;
let assemblyImages = [];

function findClass(name, namespace) {
    const ns = namespace || '';
    for (const img of assemblyImages) {
        const k = fn.class_from_name(img, cstr(ns), cstr(name));
        if (k !== null && !k.isNull()) return k;
    }
    return NULL;
}

/* 遍历所有程序集，找所有**名字精确等于** want 的类（含泛型特化） */
function findAllClasses(wantName) {
    const out = [];
    for (const img of assemblyImages) {
        /* il2cpp_image_get_class_count / get_class */
        let cnt = 0;
        try {
            cnt = imgCnt(img);
        } catch (e) { continue; }
        for (let i = 0; i < cnt; i++) {
            let k;
            try { k = imgCls(img, i); } catch (e) { continue; }
            if (k === null || k.isNull()) continue;
            const n = readStr(fn.class_get_name(k));
            if (n !== wantName) continue;
            out.push(k);
        }
    }
    return out;
}

/* ---------- 世界 / 模型扫描 ---------- */
function getWorldManagers() {
    const res = [];
    try {
        const sc = findClass('ServiceLocator', '');
        if (!sc.isNull()) {
            const f = fn.class_get_field_from_name(sc, cstr('_worldStateManager'));
            if (!f.isNull()) {
                const buf = Memory.alloc(Process.pointerSize);
                fn.field_static_get_value(f, buf);
                const v = buf.readPointer();
                if (!v.isNull()) res.push(v);
            }
            const f2 = fn.class_get_field_from_name(sc, cstr('_services'));
            if (!f2.isNull()) {
                const buf2 = Memory.alloc(Process.pointerSize);
                fn.field_static_get_value(f2, buf2);
                const svcs = buf2.readPointer();
                const m = scanListForClass(svcs, 'S_WorldStateManager');
                if (!m.isNull()) res.push(m);
            }
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return res;
}

function instancesOf(wantName) {
    const res = [];
    const seen = {};
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
            const world = listAt(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                for (const obj of readListPtrs(listAt(world, off))) {
                    if (obj.isNull()) continue;
                    const k = obj.readPointer();
                    if (k.isNull()) continue;
                    if (readStr(fn.class_get_name(k)) !== wantName) continue;
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

/* ---------- 字段偏移缓存 ---------- */
const _fieldOffCache = {};
function fieldOff(klass, fieldName) {
    if (klass === null || klass.isNull()) return -1;
    const key = klass.toString() + '|' + fieldName;
    if (key in _fieldOffCache) return _fieldOffCache[key];
    let off = -1;
    try {
        const f = fn.class_get_field_from_name(klass, cstr(fieldName));
        if (!f.isNull()) off = fn.field_get_offset(f);
    } catch (e) { /* 探测失败：跳过这一项 */ }
    _fieldOffCache[key] = off;
    return off;
}

function fieldOffOf(obj, fieldName) {
    if (obj === null || obj.isNull()) return -1;
    try { return fieldOff(obj.readPointer(), fieldName); } catch (e) { return -1; }
}

/* ================= P1：牌库字段偏移 ================= */
const DECK_FIELDS = [
    '<DeckType>k__BackingField',
    '_DeckType_k__BackingField',
    '_normalShuffledDrawCards',
    '_discards',
    '_exhausted',
    '_lodged',
    '_setAside',
    '_previousSetAside',
    '_exiles',
    '_handSaveCards',
    '_topOfDrawPileCards',
    '_topOfDrawPileNextEncounterCards',
    '_drawExactlyTheseNextTime',
    '_protocols',
    '_reshuffleAutomatically',
    '<isInitialised>k__BackingField',
    '_isInitialised_k__BackingField',
];

/* 期望偏移（由 il2cpp.h 结构链手算：Il2CppObject 0x10 → m_CachedPtr 0x10
 * → VInvoke_OnDestroy 0x18 → _stateManager 0x20 → _HasBeenSetup 0x28
 * → _World 0x30 → M_Deck<T> 字段区从 0x38 起）
 * 探针会把 field_get_offset 的结果与这张表逐项对照，不一致就是推算错了。 */
const DECK_FIELD_EXPECT = {
    '<DeckType>k__BackingField': 0x38,
    '_DeckType_k__BackingField': 0x38,
    '_normalShuffledDrawCards': 0x40,
    '_discards': 0x48,
    '_exhausted': 0x50,
    '_lodged': 0x58,
    '_setAside': 0x60,
    '_previousSetAside': 0x68,
    '_exiles': 0x70,
    '_handSaveCards': 0x78,
    '_topOfDrawPileCards': 0x80,
    '_topOfDrawPileNextEncounterCards': 0x88,
    '_drawExactlyTheseNextTime': 0x90,
    '_protocols': 0x98,
    '_reshuffleAutomatically': 0xA0,
    '<isInitialised>k__BackingField': 0xA1,
    '_isInitialised_k__BackingField': 0xA1,
};

/* 列表型字段（要做 List 结构校验的） */
const DECK_LIST_FIELDS = {
    '_normalShuffledDrawCards': 1, '_discards': 1, '_exhausted': 1, '_lodged': 1,
    '_setAside': 1, '_exiles': 1, '_handSaveCards': 1, '_protocols': 1,
    '_topOfDrawPileCards': 1, '_topOfDrawPileNextEncounterCards': 1,
    '_drawExactlyTheseNextTime': 1,
};

function probeDeckFields() {
    const out = { ok: false, deckClass: '', deckKlass: '', fields: {}, baseChain: [], err: '' };
    try {
        const deck = findAnyDeck();
        if (deck === null || deck.isNull()) {
            out.err = '未找到任何 M_Deck 实例（可能不在游戏内）';
            return out;
        }
        const k = deck.readPointer();
        out.deckKlass = k.toString();
        out.deckClass = readStr(fn.class_get_name(k));

        /* 继承链 */
        let cur = k, guard = 0;
        while (cur !== null && !cur.isNull() && guard++ < 12) {
            out.baseChain.push(readStr(fn.class_get_name(cur)));
            try { cur = fn.class_get_parent(cur); } catch (e) { break; }
        }

        for (const f of DECK_FIELDS) {
            const off = fieldOff(k, f);
            const exp = DECK_FIELD_EXPECT[f];
            const rec = { off: off, expect: exp === undefined ? null : exp, cls: '' };
            if (exp !== undefined) rec.match = (off === exp);
            if (off >= 0) {
                /* 记录这个字段实际定义在继承链的哪一层 */
                let c2 = k, g2 = 0;
                while (c2 !== null && !c2.isNull() && g2++ < 12) {
                    const fo = fn.class_get_field_from_name(c2, cstr(f));
                    if (!fo.isNull()) { rec.cls = readStr(fn.class_get_name(c2)); break; }
                    try { c2 = fn.class_get_parent(c2); } catch (e) { break; }
                }
                /* 指针字段：验证它指向的 List 结构是否合理 */
                if (DECK_LIST_FIELDS[f]) {
                    const lp = atOff(deck, off);
                    if (!lp.isNull()) {
                        const lpK = lp.readPointer();
                        rec.listCls = lpK.isNull() ? '' : readStr(fn.class_get_name(lpK));
                        const items = lp.add(OFF.listItems).readPointer();
                        const size = lp.add(OFF.listSize).readS32();
                        rec.size = size;
                        rec.itemsOk = !items.isNull() && size >= 0 && size < 20000;
                    } else {
                        rec.null = true;
                    }
                }
            }
            out.fields[f] = rec;
        }
        out.ok = true;
    } catch (e) {
        out.err = String(e);
    }
    return out;
}

/* 找任意一个 M_Deck<...> 实例：从 M_GameplayHand 的 <DeckImpl> 反查 */
function decksFromHands() {
    const out = [];
    for (const h of instancesOf('M_GameplayHand')) {
        const deck = atOff(h, fieldOffOf(h, '<DeckImpl>k__BackingField'));
        if (deck.isNull()) continue;
        out.push({ hand: h, deck: deck });
    }
    return out;
}

function findAnyDeck() {
    const ds = decksFromHands();
    if (ds.length) return ds[0].deck;
    /* 退路：世界模型里直接找 M_ForesightDeck 之类真身（M_GameplayDeck 是 embedded，一般找不到） */
    for (const n of ['M_ForesightDeck', 'M_GameplayDeck']) {
        const l = instancesOf(n);
        if (l.length) return l[0];
    }
    return NULL;
}

/* ================= P2：方法指针 ================= */
const METHOD_SPECS = [
    { cls: 'M_GameplayDeck', name: 'RemoveCard', argc: 3 },
    { cls: 'M_GameplayDeck', name: 'RemoveCard', argc: 1 },
    { cls: 'M_GameplayDeck', name: 'TryRemoveSpecificCard', argc: 2 },
    { cls: 'M_GameplayDeck', name: 'AddToExileCardPile', argc: 1 },
    { cls: 'M_GameplayDeck', name: 'GetAllCards', argc: 1 },
    { cls: 'M_GameplayDeck', name: 'GetAllCardsAndLocations', argc: 2 },
    { cls: 'M_GameplayDeck', name: 'GetCountOfCard', argc: 1 },
    { cls: 'M_GameplayDeck', name: 'AlreadyHasCard', argc: 1 },
    { cls: 'M_GameplayDeck', name: 'get_CardsInDeckCount', argc: 0 },
    { cls: 'M_GameplayHand', name: 'ExileCard', argc: 2 },
    { cls: 'M_GameplayHand', name: 'RemoveCardFromHand', argc: 2 },
    { cls: 'M_GameplayHand', name: 'DiscardCard', argc: 2 },
    { cls: 'M_Player', name: 'ExileCard', argc: 1 },
];

/* 期望 RVA（来自 dump.cs 的 GenericInstMethod 块），用于交叉验证 */
const EXPECT_RVA = {
    'M_GameplayDeck.RemoveCard/3': 0xC35720,
    'M_GameplayDeck.TryRemoveSpecificCard/2': 0xC37D30,
    'M_GameplayDeck.AddToExileCardPile/1': 0xC2DFC0,
    'M_GameplayDeck.GetAllCards/1': 0xC32820,
    'M_GameplayDeck.GetAllCardsAndLocations/2': 0xC31080,
    'M_GameplayDeck.GetCountOfCard/1': 0xC33070,
    'M_GameplayDeck.AlreadyHasCard/1': 0xC2E940,
    'M_GameplayDeck.get_CardsInDeckCount/0': 0xC38820,
    'M_GameplayHand.ExileCard/2': 0xC413B0,
    'M_GameplayHand.RemoveCardFromHand/2': 0xC47470,
    'M_GameplayHand.DiscardCard/2': 0xC3BFB0,
    'M_Player.ExileCard/1': 0xAC02E0,
};

function methodInfoOf(klass, name, argc) {
    try {
        const mi = fn.class_get_method_from_name(klass, cstr(name), argc);
        if (mi === null || mi.isNull()) return null;
        /* MethodInfo 头部：methodPointer @0x00 */
        const mp = mi.readPointer();
        return { mi: mi, ptr: mp };
    } catch (e) { return null; }
}

/* 拿「真实实例的 klass」对应的 MethodInfo —— 泛型共享方法必须用实例 klass 解析 */
function probeMethods() {
    const out = { ok: false, results: {}, classes: {}, err: '' };
    try {
        /* 记录几个类的可用性 */
        for (const n of ['M_GameplayDeck', 'M_ForesightDeck', 'M_GameplayHand', 'M_Player', 'M_Deck']) {
            const k = findClass(n, 'Backend');
            out.classes[n] = {
                inBackend: !(k === null || k.isNull()),
                all: findAllClasses(n).length,
            };
        }

        /* 用实例 klass 解析（这是唯一可靠的方式） */
        const ds = decksFromHands();
        out.deckInstanceCount = ds.length;
        if (ds.length) {
            out.instanceKlassName = readStr(fn.class_get_name(ds[0].deck.readPointer()));
            out.instanceKlass = ds[0].deck.readPointer().toString();
        }
        /* 手牌实例 */
        const hands = instancesOf('M_GameplayHand');
        out.handInstanceCount = hands.length;
        if (hands.length) {
            out.handKlassName = readStr(fn.class_get_name(hands[0].readPointer()));
        }
        /* 玩家 */
        const players = instancesOf('M_Player');
        out.playerInstanceCount = players.length;
        if (players.length) {
            out.playerKlassName = readStr(fn.class_get_name(players[0].readPointer()));
        }

        const lookup = (clsName) => {
            /* 优先用真实实例的 klass；没有实例就退回全局查找 */
            if (clsName === 'M_GameplayDeck' && ds.length) return ds[0].deck.readPointer();
            if (clsName === 'M_GameplayHand' && hands.length) return hands[0].readPointer();
            if (clsName === 'M_Player' && players.length) return players[0].readPointer();
            const k = findClass(clsName, 'Backend');
            if (k !== null && !k.isNull()) return k;
            const all = findAllClasses(clsName);
            return all.length ? all[0] : NULL;
        };

        for (const spec of METHOD_SPECS) {
            const key = spec.cls + '.' + spec.name + '/' + spec.argc;
            const k = lookup(spec.cls);
            const rec = { klassFound: !(k === null || k.isNull()), ptr: '', rva: '', expect: '', match: null, sig: '' };
            const exp = EXPECT_RVA[key];
            if (exp) rec.expect = '0x' + exp.toString(16);
            if (k !== null && !k.isNull()) {
                rec.klass = readStr(fn.class_get_name(k));
                const m = methodInfoOf(k, spec.name, spec.argc);
                if (m && !m.ptr.isNull()) {
                    rec.ptr = m.ptr.toString();
                    if (m.ptr.compare(gaBase) >= 0 && m.ptr.compare(gaBase.add(gaSize)) < 0) {
                        const rva = m.ptr.sub(gaBase);
                        rec.rva = rva.toString();
                        rec.inModule = true;
                        /* 前 8 字节（prologue 校验） */
                        try { rec.sig = m.ptr.readByteArray(8); rec.sig = Array.from(new Uint8Array(rec.sig))
                            .map(b => b.toString(16).padStart(2, '0')).join(' '); } catch (e) { /* 探测失败：跳过这一项 */ }
                        if (exp) rec.match = (rva.toInt32() === exp);
                    } else {
                        rec.inModule = false;
                    }
                } else {
                    rec.ptr = 'null';
                }
            }
            out.results[key] = rec;
        }
        out.ok = true;
    } catch (e) {
        out.err = String(e);
    }
    return out;
}

/* ================= P3：两套牌库同时存在 ================= */
function deckCandidates() {
    const out = [];
    for (const h of instancesOf('M_GameplayHand')) {
        const deck = atOff(h, fieldOffOf(h, '<DeckImpl>k__BackingField'));
        if (deck.isNull()) continue;
        let dt = -1;
        for (const f of ['<DeckType>k__BackingField', '_DeckType_k__BackingField']) {
            const o = fieldOffOf(deck, f);
            if (o >= 0) { dt = i32Off(deck, o); break; }
        }
        let inited = false;
        for (const f of ['<isInitialised>k__BackingField', '_isInitialised_k__BackingField']) {
            const o = fieldOffOf(deck, f);
            if (o >= 0) { inited = boolOff(deck, o); break; }
        }
        out.push({
            deck: deck, hand: h, deckType: dt,
            deckCls: readStr(fn.class_get_name(deck.readPointer())),
            handCls: readStr(fn.class_get_name(h.readPointer())),
            inited: inited,
            active: boolOff(h, fieldOffOf(h, '_isActive')),
            handCount: readListPtrs(atOff(h, fieldOffOf(h, '_heldCards'))).length,
            handSize: i32Off(h, fieldOffOf(h, '_handSize')),
            draw: atOff(deck, fieldOffOf(deck, '_normalShuffledDrawCards')).isNull() ? -1
                : readListPtrs(atOff(deck, fieldOffOf(deck, '_normalShuffledDrawCards'))).length,
            discard: atOff(deck, fieldOffOf(deck, '_discards')).isNull() ? -1
                : readListPtrs(atOff(deck, fieldOffOf(deck, '_discards'))).length,
            exhaust: atOff(deck, fieldOffOf(deck, '_exhausted')).isNull() ? -1
                : readListPtrs(atOff(deck, fieldOffOf(deck, '_exhausted'))).length,
        });
    }
    return out;
}

const DECK_TYPE_NAME = { 0: '未知', 1: '探索', 2: '战斗', 3: '预视', 4: '特殊' };

function probeDecks() {
    const out = { ok: false, count: 0, decks: [], err: '' };
    try {
        const cs = deckCandidates();
        out.count = cs.length;
        for (const c of cs) {
            out.decks.push({
                deckType: c.deckType,
                deckTypeName: DECK_TYPE_NAME[c.deckType] || '?',
                deckCls: c.deckCls,
                handCls: c.handCls,
                inited: c.inited,
                active: c.active,
                hand: c.handCount,
                handSize: c.handSize,
                draw: c.draw,
                discard: c.discard,
                exhaust: c.exhaust,
                deckPtr: c.deck.toString(),
            });
        }
        out.ok = true;
    } catch (e) { out.err = String(e); }
    return out;
}

/* ================= P4：四堆的真实内容 ================= */
function cardDataNameOf(dataPtr) {
    if (dataPtr === null || dataPtr.isNull()) return '';
    const k = dataPtr.readPointer();
    for (const f of ['<Name>k__BackingField', '_overrideDisplayName', 'LastLocKey']) {
        const off = fieldOff(k, f);
        if (off < 0) continue;
        const v = readCsString(atOff(dataPtr, off));
        if (v) return v;
    }
    return '';
}

function stackNames(deck, fieldName, limit) {
    const off = fieldOffOf(deck, fieldName);
    if (off < 0) return { off: -1, n: 0, names: [] };
    const ptrs = readListPtrs(atOff(deck, off));
    return {
        off: off,
        n: ptrs.length,
        names: ptrs.slice(0, limit || 12).map(cardDataNameOf),
    };
}

const FORGET_STACKS = [
    '_normalShuffledDrawCards',
    '_discards',
    '_exhausted',
    '_exiles',
    '_lodged',
    '_setAside',
    '_handSaveCards',
];

function probeStacks(limit) {
    const out = { ok: false, decks: [], err: '' };
    try {
        for (const c of deckCandidates()) {
            const rec = {
                deckType: c.deckType,
                deckTypeName: DECK_TYPE_NAME[c.deckType] || '?',
                inited: c.inited,
                active: c.active,
                stacks: {},
            };
            for (const f of FORGET_STACKS) {
                rec.stacks[f] = stackNames(c.deck, f, limit || 8);
            }
            /* 手牌实例 */
            const held = readListPtrs(atOff(c.hand, fieldOffOf(c.hand, '_heldCards')));
            const dOff = fieldOffOf(c.hand, '_heldCards');
            rec.hand = {
                off: dOff, n: held.length,
                names: held.slice(0, limit || 8).map(inst => {
                    const di = atOff(inst, fieldOffOf(inst, '<DataImpl>k__BackingField'));
                    return di.isNull() ? '' : cardDataNameOf(di);
                }),
            };
            out.decks.push(rec);
        }
        out.ok = true;
    } catch (e) { out.err = String(e); }
    return out;
}

/* ================= 汇总 ================= */
function probeAll() {
    const out = { pid: Process.id, module: ga.toString(), moduleSize: gaSize };
    try { out.calib = { decksFound: decksFromHands().length }; } catch (e) { out.calib = { err: String(e) }; }
    out.fields = probeDeckFields();
    out.methods = probeMethods();
    out.decks = probeDecks();
    out.stacks = probeStacks(8);
    return out;
}

function init() {
    const b = bind();
    if (b.missing.length) {
        return { bound: b.bound, missing: b.missing };
    }
    const dom = fn.domain_get();
    fn.thread_attach(dom);

    /* 枚举全部程序集镜像，供 findClass / findAllClasses 用 */
    const countPtr = Memory.alloc(4);
    const arr = fn.domain_get_assemblies(dom, countPtr);
    const n = countPtr.readS32();
    for (let i = 0; i < n; i++) {
        try {
            const asm = arr.add(i * Process.pointerSize).readPointer();
            if (asm.isNull()) continue;
            const img = fn.assembly_get_image(asm);
            if (!img.isNull()) assemblyImages.push(img);
        } catch (e) { /* 探测失败：跳过这一项 */ }
    }
    return { assemblies: assemblyImages.length };
}

/* 取 GameAssembly 导出（Frida 17：用模块的 getExportByName） */
function _modExport(n) {
    return Process.getModuleByName(MODULE_NAME).getExportByName(n);
}

/* il2cpp_image_get_class_count / get_class（延迟绑定，很多版本有） */
let _imgCnt = null, _imgCls = null;
function imgCnt(img) {
    if (_imgCnt === null) {
        const a = _modExport('il2cpp_image_get_class_count');
        _imgCnt = a === null ? false : new NativeFunction(a, 'uint32', ['pointer']);
    }
    if (_imgCnt === false) return 0;
    return _imgCnt(img);
}
function imgCls(img, i) {
    if (_imgCls === null) {
        const a = _modExport('il2cpp_image_get_class');
        _imgCls = a === null ? false : new NativeFunction(a, 'pointer', ['pointer', 'int']);
    }
    if (_imgCls === false) return NULL;
    return _imgCls(img, i);
}

rpc.exports = {
    init() {
        try { return { ok: true, info: init() }; }
        catch (e) { return { ok: false, error: String(e), stack: (e && e.stack) ? String(e.stack) : '', fnKeys: Object.keys(fn) }; }
    },
    /* 主入口：全部标定 */
    probe() {
        try { return probeAll(); } catch (e) { return { ok: false, error: String(e) }; }
    },
    fields() { try { return probeDeckFields(); } catch (e) { return { ok: false, error: String(e) }; } },
    methods() { try { return probeMethods(); } catch (e) { return { ok: false, error: String(e) }; } },
    decks() { try { return probeDecks(); } catch (e) { return { ok: false, error: String(e) }; } },
    stacks(limit) { try { return probeStacks(limit); } catch (e) { return { ok: false, error: String(e) }; } },
    ping() { return 'pong'; }
};
