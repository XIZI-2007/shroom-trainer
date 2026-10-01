/*
 * deck_probe.js —— 牌库顺序标定探针（**只读**，不挂钩任何函数、不改游戏状态）
 *
 * 目的：验证「牌库顺序」能否被忠实读出，并用「牌库减少的牌」对账「手牌增加的牌」，
 *       反推 M_Deck.DrawNextCard 的取牌方向（头部 / 尾部 / 带过滤）。
 *
 * 读取目标（均来自 Il2CppDumper 对 0.6.41 的 dump）：
 *   M_GameplayDeck._normalShuffledDrawCards  List<GameplayCardData>   抽牌堆（有序）
 *   M_GameplayDeck._discards                 List<GameplayCardData>   弃牌堆
 *   M_GameplayDeck._exhausted                List<GameplayCardData>   消耗堆
 *   M_GameplayDeck._topOfDrawPileCards       List<ValueTuple<..>>     牌库顶覆盖
 *   M_GameplayHand._heldCards                List<M_CardInstance<..>> 手牌
 *   M_GameplayHand._handSize                 int32                    手牌上限
 *
 * 泛型基类字段（如 M_Deck<TCardData>）的偏移用 il2cpp_field_get_offset 动态取，
 * 不硬编码 —— 这也是本探针要顺带验证的点。
 */

'use strict';

const MODULE_NAME = 'GameAssembly.dll';

const OFF = {
    listItems: 0x10,
    listSize: 0x18,
    arrayData: 0x20,      /* IL2CPP 数组：obj(0) + bounds(0x10) + max_length(0x18) → 元素从 0x20 */
    strLen: 0x10,
    strChars: 0x14,
    mainWorld: 0x48,
    futureWorld: 0x50,
    pureModels: 0x10,
    runtimeModels: 0x20,
};

let ga = null;
let gaSize = 0;
const fn = {};
let fldWorldMgr = null;
let servicesField = null;
let ready = false;
let lastErr = '';

/* ---------------- il2cpp API 绑定 ---------------- */
function bind() {
    const mod = Process.getModuleByName(MODULE_NAME);
    ga = mod.base;
    gaSize = mod.size;
    const ex = (n) => mod.getExportByName(n);

    fn.domain_get = new NativeFunction(ex('il2cpp_domain_get'), 'pointer', []);
    fn.domain_get_assemblies = new NativeFunction(ex('il2cpp_domain_get_assemblies'), 'pointer', ['pointer', 'pointer']);
    fn.assembly_get_image = new NativeFunction(ex('il2cpp_assembly_get_image'), 'pointer', ['pointer']);
    fn.class_from_name = new NativeFunction(ex('il2cpp_class_from_name'), 'pointer', ['pointer', 'pointer', 'pointer']);
    fn.class_get_name = new NativeFunction(ex('il2cpp_class_get_name'), 'pointer', ['pointer']);
    fn.class_get_field_from_name = new NativeFunction(ex('il2cpp_class_get_field_from_name'), 'pointer', ['pointer', 'pointer']);
    fn.field_get_offset = new NativeFunction(ex('il2cpp_field_get_offset'), 'int', ['pointer']);
    fn.field_static_get_value = new NativeFunction(ex('il2cpp_field_static_get_value'), 'void', ['pointer', 'pointer']);
    fn.thread_attach = new NativeFunction(ex('il2cpp_thread_attach'), 'pointer', ['pointer']);
}

function cstr(s) { return Memory.allocUtf8String(s); }

function readStr(p) {
    if (p === null || p.isNull()) return '';
    try { return p.readUtf8String(); } catch (e) { return ''; }
}

/* 读托管字符串：+0x10 length(int32) / +0x14 utf16 chars */
function readCsString(p) {
    if (p === null || p.isNull()) return '';
    try {
        const len = p.add(OFF.strLen).readS32();
        if (len <= 0 || len > 8192) return '';
        return p.add(OFF.strChars).readUtf16String(len) || '';
    } catch (e) { return ''; }
}

/* 注：这里原有一个 inModule(p)（判断指针是否落在 GameAssembly 模块内，做法是
 *     `p.sub(ga)` 的结果落在 [0, gaSize) 区间）。它只被上面的 isManaged() 调用，
 *     两者于 2026-10-01 一并清掉（本探针是只读标定工具，标定完成后已无人调用）；
 *     以后若需要，按上面这句话重写即可。
 */

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

/* ---------------- 字段偏移（动态解析 + 缓存） ---------------- */
const fieldOffCache = {};

function fieldOff(klass, fieldName) {
    if (klass === null || klass.isNull()) return -1;
    const key = klass.toString() + '|' + fieldName;
    if (key in fieldOffCache) return fieldOffCache[key];
    let off = -1;
    try {
        /* il2cpp_class_get_field_from_name 会沿 parent 链查找，所以子类 klass 也能拿到继承字段 */
        const f = fn.class_get_field_from_name(klass, cstr(fieldName));
        if (f !== null && !f.isNull()) {
            off = fn.field_get_offset(f);
        }
    } catch (e) { /* 探测失败：跳过这一项 */ }
    fieldOffCache[key] = off;
    return off;
}

/* 按已找到的实例动态取字段偏移（实例的 klass 才是真正实例化的泛型类） */
function fieldOffOf(obj, fieldName) {
    if (!obj || obj.isNull()) return -1;
    try { return fieldOff(obj.readPointer(), fieldName); } catch (e) { return -1; }
}

function at(obj, off) {
    if (!obj || obj.isNull() || off < 0) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function atI32(obj, off) {
    if (!obj || obj.isNull() || off < 0) return 0;
    try { return obj.add(off).readS32(); } catch (e) { return 0; }
}

/* ---------------- 读 List<T> ---------------- */
function readList(list) {
    const out = [];
    if (!list || list.isNull()) return out;
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

/* ---------------- 实例定位（与 agent.js 完全同路） ---------------- */
function staticRef(field) {
    if (field === null || field.isNull()) return NULL;
    const out = Memory.alloc(Process.pointerSize);
    out.writePointer(NULL);
    try { fn.field_static_get_value(field, out); } catch (e) { return NULL; }
    return out.readPointer();
}

function scanListForClass(list, wantName) {
    if (!list || list.isNull()) return NULL;
    const items = readList(list);
    for (const obj of items) {
        if (obj.isNull()) continue;
        const k = obj.readPointer();
        if (k.isNull()) continue;
        if (readStr(fn.class_get_name(k)) === wantName) return obj;
    }
    return NULL;
}

function getWorldManagers() {
    const res = [];
    try {
        const wsm = staticRef(fldWorldMgr);
        if (!wsm.isNull()) res.push(wsm);
    } catch (e) { /* 探测失败：跳过这一项 */ }
    try {
        const svcs = staticRef(servicesField);
        const m = scanListForClass(svcs, 'S_WorldStateManager');
        if (m !== null && !m.isNull()) res.push(m);
    } catch (e) { /* 探测失败：跳过这一项 */ }
    return res;
}

/* 收集所有指定类名的实例（主世界 + 未来世界 + 纯模型 + 运行时模型） */
function instancesOf(wantName) {
    const res = [];
    const seen = {};
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
            const world = at(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                const list = at(world, off);
                if (list.isNull()) continue;
                for (const obj of readList(list)) {
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

/* 牌库是 IsEmbeddedModelObject（不注册进模型列表），只能顺着手牌的 DeckImpl 反向取。
 * 有多副手牌（主世界 / 未来世界 / 预知），逐个试，取第一个拿到有效指针的。 */
function deckFromHands(hands) {
    const raw = [];
    for (let i = 0; i < hands.length; i++) {
        const h = hands[i];
        const off = fieldOffOf(h, '<DeckImpl>k__BackingField');
        const d = at(h, off);
        raw.push({ i: i, off: off, ptr: d ? d.toString() : null });
        if (d !== null && !d.isNull()) return { deck: d, raw: raw, idx: i };
    }
    return { deck: NULL, raw: raw, idx: -1 };
}

/* ---------------- 卡牌名字 ---------------- */
/* GameplayCardData / AbstractCardData<GameplayCardData> 上的显示名 */
function cardDataName(dataPtr) {
    if (!dataPtr || dataPtr.isNull()) return '';
    const k = dataPtr.readPointer();
    for (const f of ['<Name>k__BackingField', '_overrideDisplayName', 'LastLocKey']) {
        const off = fieldOff(k, f);
        if (off < 0) continue;
        const s = at(dataPtr, off);
        const v = readCsString(s);
        if (v) return v;
    }
    return '';
}

/* M_CardInstance<TCardData>.DataImpl -> GameplayCardData */
function instanceCardName(instPtr) {
    if (!instPtr || instPtr.isNull()) return '';
    const off = fieldOffOf(instPtr, '<DataImpl>k__BackingField');
    const data = at(instPtr, off);
    return cardDataName(data);
}

function klassNameOf(obj) {
    if (!obj || obj.isNull()) return '';
    try { return readStr(fn.class_get_name(obj.readPointer())); } catch (e) { return ''; }
}

function loadListOfNames(deck, fieldName) {
    const off = fieldOffOf(deck, fieldName);
    const list = at(deck, off);
    const out = [];
    for (const e of readList(list)) out.push(cardDataName(e));
    return { off: off, count: readList(list).length, names: out };
}

/* ---------------- 快照 ---------------- */
function snapshot() {
    const out = {
        ok: false, err: lastErr, ready: ready,
        offsets: {}, deckInstances: [], handInstances: [],
        deck: null, hand: null,
    };
    if (!ready) return out;

    try {
        const decks = instancesOf('M_GameplayDeck');
        const hands = instancesOf('M_GameplayHand');
        out.deckInstances = decks.map((d) => d.toString());
        out.handInstances = hands.map((h) => h.toString());

        if (decks.length === 0 && hands.length === 0) {
            out.err = '未找到 M_GameplayDeck / M_GameplayHand（可能还没进入战斗场景）';
            return out;
        }

        /* 牌库优先从世界模型里找；找不到就顺着手牌的反向引用拿 */
        let d = decks.length > 0 ? decks[0] : NULL;
        let deckVia = decks.length > 0 ? 'worldModels' : '';
        if ((d === null || d.isNull()) && hands.length > 0) {
            const r = deckFromHands(hands);
            out.handDeckImplRaw = r.raw;
            if (r.deck !== null && !r.deck.isNull()) {
                d = r.deck;
                deckVia = 'hand.DeckImpl#' + r.idx;
            }
        }
        out.deckVia = deckVia;

        if (d !== null && !d.isNull()) {
            const draw = loadListOfNames(d, '_normalShuffledDrawCards');
            const disc = loadListOfNames(d, '_discards');
            const exh = loadListOfNames(d, '_exhausted');
            const aside = loadListOfNames(d, '_setAside');
            const top = loadListOfNames(d, '_topOfDrawPileCards');
            const exact = loadListOfNames(d, '_drawExactlyTheseNextTime');
            const save = loadListOfNames(d, '_handSaveCards');

            out.offsets['_normalShuffledDrawCards'] = draw.off;
            out.offsets['_discards'] = disc.off;
            out.offsets['_exhausted'] = exh.off;
            out.offsets['_setAside'] = aside.off;
            out.offsets['_topOfDrawPileCards'] = top.off;
            out.offsets['_drawExactlyTheseNextTime'] = exact.off;
            out.offsets['_handSaveCards'] = save.off;

            out.deck = {
                ptr: d.toString(),
                klass: klassNameOf(d),
                deckType: atI32(d, fieldOffOf(d, '<DeckType>k__BackingField')),
                draw: draw.names,
                drawCount: draw.count,
                discards: disc.names,
                discardsCount: disc.count,
                exhausted: exh.names,
                exhaustedCount: exh.count,
                setAside: aside.names,
                setAsideCount: aside.count,
                topOfDrawPile: top.names,
                topOfDrawPileCount: top.count,
                exactDraw: exact.names,
                exactDrawCount: exact.count,
                handSave: save.names,
                handSaveCount: save.count,
                totalCount: draw.count + disc.count + exh.count,
            };
        }

        if (hands.length > 0) {
            const h = hands[0];
            const heldOff = fieldOffOf(h, '_heldCards');
            const held = at(h, heldOff);
            const cards = readList(held).map(instanceCardName);
            out.offsets['_heldCards'] = heldOff;
            out.offsets['_handSize'] = fieldOffOf(h, '_handSize');
            out.offsets['hand.<DeckImpl>'] = fieldOffOf(h, '<DeckImpl>k__BackingField');

            out.hand = {
                ptr: h.toString(),
                klass: klassNameOf(h),
                cards: cards,
                count: cards.length,
                handSize: atI32(h, fieldOffOf(h, '_handSize')),
                isActive: !!atI32(h, fieldOffOf(h, '_isActive')),
            };
        }

        out.ok = true;
    } catch (e) {
        out.err = 'snapshot 异常: ' + e;
    }
    return out;
}

/* ---------------- 启动 ---------------- */
function init() {
    try {
        bind();
        const dom = fn.domain_get();
        fn.thread_attach(dom);

        const sc = findClass('ServiceLocator', '');
        if (sc !== null && !sc.isNull()) {
            const f = fn.class_get_field_from_name(sc, cstr('_worldStateManager'));
            if (!f.isNull()) fldWorldMgr = f;
            const f2 = fn.class_get_field_from_name(sc, cstr('_services'));
            if (!f2.isNull()) servicesField = f2;
        }

        /* 顺带记录关键类是否解析得到，便于定位问题 */
        const probeClasses = ['M_GameplayDeck', 'M_GameplayHand', 'GameplayCardData', 'M_GameplayCardInstance'];
        const found = {};
        for (const c of probeClasses) {
            let k = findClass(c, '');
            if (k === null || k.isNull()) k = findClass(c, 'Backend');
            found[c] = !!(k && !k.isNull());
        }

        lastErr = 'klass: ' + JSON.stringify(found) +
                  ' svcField=' + !(servicesField === null || servicesField.isNull()) +
                  ' worldMgrField=' + !(fldWorldMgr === null || fldWorldMgr.isNull());
        ready = true;
    } catch (e) {
        lastErr = 'init 异常: ' + e;
        ready = false;
    }
}

init();

rpc.exports = {
    ping() { return 'pong'; },
    snapshot() { return snapshot(); },
    /* 列出世界模型里出现过的所有类名，用于排查牌库到底挂在哪 */
    modelClasses() {
        const out = {};
        try {
            for (const wsm of getWorldManagers()) {
                for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
                    const world = at(wsm, worldOff);
                    if (world.isNull()) continue;
                    for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                        const list = at(world, off);
                        if (list.isNull()) continue;
                        for (const obj of readList(list)) {
                            const n = klassNameOf(obj);
                            if (n) out[n] = (out[n] || 0) + 1;
                        }
                    }
                }
            }
        } catch (e) { out['_err'] = String(e); }
        return out;
    },
    diag() { return { ready: ready, err: lastErr, ga: ga ? ga.toString() : null, gaSize: gaSize }; },

    /* ---- 第二版标定：dump 一张 GameplayCardData 的费用/效果字段 ----
     * 用来确认 COST_PROP_NAMES 里到底该写哪个名字（agent.js 的 cardCost 靠它匹配）。
     * 只读，不挂钩。 */
    cardprops(limit) {
        const out = { cards: [], err: '' };
        limit = limit || 6;
        try {
            /* 从牌库里捞 GameplayCardData（跟 agent.js 一样走 <DeckImpl> → 抽牌堆） */
            const seen = {};
            const datas = [];
            const collect = (listPtr) => {
                for (const e of readList(listPtr)) {
                    if (e.isNull()) continue;
                    const key = e.toString();
                    if (seen[key]) continue;
                    seen[key] = 1;
                    datas.push(e);
                }
            };
            for (const h of instancesOf('M_GameplayHand')) {
                const deck = at(h, fieldOffOf(h, '<DeckImpl>k__BackingField'));
                if (deck.isNull()) continue;
                collect(at(deck, fieldOffOf(deck, '_normalShuffledDrawCards')));
                collect(at(deck, fieldOffOf(deck, '_discards')));
            }
            for (const d of datas.slice(0, limit)) {
                const k = d.readPointer();
                const item = { name: '', props: [], customDesc: '', customDescLoc: '', energyUseGuess: null };
                item.name = readCsString(at(d, fieldOff(k, '<Name>k__BackingField')));
                /* Properties : List<CardPropertyPair> */
                const pOff = fieldOff(k, '<Properties>k__BackingField');
                for (const p of readList(at(d, pOff))) {
                    if (p.isNull()) continue;
                    const pk = p.readPointer();
                    const pt = at(p, fieldOff(pk, 'propertyType'));
                    let pname = '';
                    if (!pt.isNull()) {
                        /* CardProperty.PropertyName @0x28（dump.cs 给的静态偏移），取不到再走动态 */
                        pname = readCsString(at(pt, 0x28));
                        if (!pname) {
                            pname = readCsString(at(pt, fieldOff(pt.readPointer(), 'PropertyName')));
                        }
                        /* 顺带把资产名（ScriptableObject 的 m_Name）也带出来对照 */
                        const objName = readCsString(at(pt, fieldOff(pt.readPointer(), 'm_Name')));
                        if (objName && objName !== pname) item.props.push({ _assetName: objName });
                    }
                    item.props.push({
                        prop: pname,
                        number: atI32(p, fieldOff(pk, 'Number')),
                        max: atI32(p, fieldOff(pk, 'Max')),
                    });
                }
                item.customDesc = readCsString(at(d, fieldOff(k, 'CustomDescription')));
                const locOff = fieldOff(k, 'CustomDescriptionLoc');
                if (locOff >= 0) {
                    const loc = at(d, locOff);
                    if (!loc.isNull()) {
                        for (const f of ['m_Localized', '_localizedValue', 'm_Value']) {
                            const v = readCsString(at(loc, fieldOff(loc.readPointer(), f)));
                            if (v) { item.customDescLoc = v; break; }
                        }
                    }
                }
                out.cards.push(item);
            }
            if (!out.cards.length) out.err = '没找到任何 GameplayCardData（不在有牌库的场景？）';
        } catch (e) { out.err = String(e); }
        return out;
    },
};
