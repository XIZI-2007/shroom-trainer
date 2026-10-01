/*
 * Shroom & Gloom 辅助 - Frida agent
 * Unity 2022.3.62f3 / IL2CPP (metadata v31) / GameAssembly.dll x64
 *
 * ── 目标版本 ───────────────────────────────────────────────────────────
 *   游戏版本 : Shroom and Gloom 0.6.41
 *   DLL 大小 : 56216064 字节（2026-09-22 22:23 Steam 更新后）
 *   RVA 来源 : Il2CppDumper 6.7.46 重新 dump（本机 GameAssembly.dll + global-metadata.dat）
 *              并通过 capstone 反汇编逐条确认函数入口与结构偏移。
 *
 * ── 双重保险（防止游戏再次更新导致闪退）─────────────────────────────────
 *   1) 静态 RVA + 16 字节函数序言签名校验：签名不符 → 拒绝挂钩并告警，
 *      而不是把钩子挂在错误的地址上（这正是上一版"出牌闪退"的根因）。
 *   2) 运行时 il2cpp C-API 动态解析（il2cpp_class_get_method_from_name →
 *      MethodInfo->methodPointer @ +0x00），与签名校验互为验证。
 *      动态解析结果优先，失败则回退到静态 RVA。
 *
 * ── 结构偏移（已由 get_Energy / HasEnergy 的反汇编确认）────────────────
 *   M_Player._saveState                @ +0x1A8  -> PlayerSerializableState
 *   PlayerSerializableState.HealthData @ +0x10   -> PlayerHealthData
 *   PlayerSerializableState.EnergyData @ +0x18   -> PlayerEnergyData
 *   PlayerEnergyData.Energy            @ +0x10   (int32)
 *   PlayerEnergyData.EnergySoftCap     @ +0x14   (int32)
 *   PlayerHealthData._health           @ +0x10   (int32)
 *   PlayerHealthData._maxHealth        @ +0x14   (int32)
 *   PlayerHealthData._armour           @ +0x18   (int32)
 *   PlayerHealthData.<Invincible>k__BackingField @ +0x1C (bool)
 *   ServiceLocator._services           @ +0x00   (List<MonoService>)
 *   ServiceLocator._worldStateManager  @ +0x08
 *   S_WorldStateManager.<MainWorld>k__BackingField   @ +0x48
 *   S_WorldStateManager.<FutureWorld>k__BackingField @ +0x50
 *   SimulatedWorldState.PureModels           @ +0x10 (List<IModel>)
 *   SimulatedWorldState._simulationModelObjects (RuntimeModels) @ +0x20
 */

'use strict';

const MODULE_NAME = 'GameAssembly.dll';

/* ---- 目标函数 RVA（对应 DLL / 游戏 0.6.41）---- */
const RVA = {
    M_Player_TakeDamage: 0x7F9B80,
    PlayerHealthData_Damage: 0x6F3C80,
    M_Player_SpendEnergy: 0x7F9350,
    M_Player_HasEnergy: 0x7F5260,
    M_Player_get_Energy: 0x7FDCA0,
    M_CardInstance_get_EnergyCost: 0xC2B570,
    /* 遗忘：M_Deck<T>.RemoveCard(ICardData, bool, int) —— 来自 dump.cs GenericInstMethod 块
       (M_Deck<object>.RemoveCard @ 0xC35720)，methodPointer 的 RVA 与之一致。
       ⚠️ 但**不要调用它**：泛型共享方法的真实机器码读的寄存器数跟 dump 签名对不上
       （TryRemoveSpecificCard 签名 2 参、实际读 r8/r9），硬调必崩 access violation。
       这两条 RVA 只留作"游戏版本指纹"，遗忘改用下面的 List 直改。 */
    M_Deck_RemoveCard: 0xC35720,
    M_Deck_TryRemoveSpecificCard: 0xC37D30,
};

/* 16 字节函数序言签名（十六进制字符串，空格分隔），用于校验 RVA 未漂移 */
const SIG = {
    takeDamage: '40 53 56 57 48 83 ec 70 80 3d 6b 4f ad 02 00 8b',
    phDamage: '48 89 5c 24 08 57 48 83 ec 20 8b 79 18 48 8b d9',
    spendEnergy: '40 53 48 83 ec 20 44 8b c2 48 8b d9 48 8b 91 a8',
    hasEnergy: '48 83 ec 28 48 8b 81 a8 01 00 00 48 85 c0 74 14',
    energyCost: '48 89 5c 24 20 56 48 83 ec 20 80 3d 9f 66 6a 02',
};

/* 动态解析描述表（genergic=true 的类无法直接用 class_from_name 取到实例化方法指针） */
const TARGETS = {
    takeDamage: { cls: 'M_Player', ns: 'Backend', method: 'TakeDamage', rva: RVA.M_Player_TakeDamage, sig: SIG.takeDamage },
    phDamage: { cls: 'PlayerHealthData', ns: '', method: 'Damage', rva: RVA.PlayerHealthData_Damage, sig: SIG.phDamage },
    spendEnergy: { cls: 'M_Player', ns: 'Backend', method: 'SpendEnergy', rva: RVA.M_Player_SpendEnergy, sig: SIG.spendEnergy },
    hasEnergy: { cls: 'M_Player', ns: 'Backend', method: 'HasEnergy', rva: RVA.M_Player_HasEnergy, sig: SIG.hasEnergy },
    energyCost: { cls: 'M_CardInstance`1', ns: 'Backend', method: 'get_EnergyCost', rva: RVA.M_CardInstance_get_EnergyCost, sig: SIG.energyCost, generic: true },
};

const OFF = {
    saveState: 0x1A8,
    healthData: 0x10,
    energyData: 0x18,
    energy: 0x10,
    energySoftCap: 0x14,
    health: 0x10,
    maxHealth: 0x14,
    armour: 0x18,
    invincible: 0x1C,
    mainWorld: 0x48,
    futureWorld: 0x50,
    pureModels: 0x10,
    runtimeModels: 0x20,
    listItems: 0x10,
    listSize: 0x18,
    /* IL2CPP 数组：obj(0x00) + bounds(0x10) + max_length(0x18) → 元素数据从 0x20 开始 */
    arrayData: 0x20,
    /* Il2CppObject 头部：klass 指针 @ +0x00 */
    objKlass: 0x00,
    /* MethodInfo.methodPointer @ +0x00 */
    methodPointer: 0x00,
};

const state = {
    god: false,
    freeCards: false,
};

const stats = {
    hits: 0, blocked: 0,
    spends: 0, spendsBlocked: 0,
    costCalls: 0, costZeroed: 0,
    hasEnergyCalls: 0, hasEnergyAllowed: 0,
    hookErrors: [],
};

let ga = null;
let gaSize = 0;
let fn = {};
let fldWorldMgr = null;
let worldMgrClass = null;
let player = null;
let listeners = {};
let lastError = '';
let playerClassPtr = null;
let servicesField = null;
let healthDataClass = null;
let resolved = {};   /* name -> { addr: NativePointer, via: 'api'|'rva', ok: bool } */

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
    /* 泛型基类的字段偏移在 dump.cs 里一律是 0x0，只能运行时动态取（详见 §牌库） */
    fn.field_get_offset = new NativeFunction(ex('il2cpp_field_get_offset'), 'int', ['pointer']);
    fn.class_get_method_from_name = new NativeFunction(ex('il2cpp_class_get_method_from_name'), 'pointer', ['pointer', 'pointer', 'int']);
    fn.class_get_methods = new NativeFunction(ex('il2cpp_class_get_methods'), 'pointer', ['pointer', 'pointer']);
    fn.method_get_name = new NativeFunction(ex('il2cpp_method_get_name'), 'pointer', ['pointer']);
    fn.field_static_get_value = new NativeFunction(ex('il2cpp_field_static_get_value'), 'void', ['pointer', 'pointer']);
    fn.thread_attach = new NativeFunction(ex('il2cpp_thread_attach'), 'pointer', ['pointer']);
    fn.runtime_invoke = new NativeFunction(ex('il2cpp_runtime_invoke'), 'pointer',
        ['pointer', 'pointer', 'pointer', 'pointer']);
}

function cstr(s) {
    return Memory.allocUtf8String(s);
}

function readStr(p) {
    if (p === null || p.isNull()) return '';
    try { return p.readUtf8String(); } catch (e) { return ''; }
}

/* 是否落在 GameAssembly.dll 的地址范围内 */
function inModule(p) {
    if (p === null || p.isNull()) return false;
    try {
        const d = p.sub(ga);
        return d.compare(ptr(0)) > 0 && d.compare(ptr(gaSize)) < 0;
    } catch (e) { return false; }
}

/* 校验函数序言 16 字节是否与预期签名一致（防 RVA 漂移） */
function sigOk(addr, sigHex) {
    if (!inModule(addr)) return false;
    try {
        const want = sigHex.split(' ').map((x) => parseInt(x, 16));
        const got = new Uint8Array(addr.readByteArray(want.length));
        for (let i = 0; i < want.length; i++) if (got[i] !== want[i]) return false;
        return true;
    } catch (e) { return false; }
}

/* ⚠️⚠️ 入口是否已被**别人**（含本进程此前的实例、或残留 hook）写成跳转桩。
 *
 * 背景（实测踩过）：`sigOk()` 读的是"此刻内存里的机器码"。一旦目标被 Interceptor
 * 挂过，入口前 5 字节就被改写成 `E9 <rel32>`（jmp 到 trampoline）。此后再调
 * `init()` 重新 `resolveTarget()`，`sigOk` 必然 false ⇒ 被判定为"RVA 漂移" ⇒
 * `hooks` 显示 FAILED ⇒ 无敌/不消耗精力**静默失效**。
 * 更糟的是面板"断开→重连"就会走这条路径 —— 连一次就永久废掉。
 *
 * 所以：**入口已经是 E9 跳转桩 ⇒ 该地址就是对的**（跳转桩只会写在真函数入口上），
 * 判定为"可用"，只是标记 via='hooked'。
 */
function isTrampolined(addr) {
    if (!inModule(addr)) return false;
    try { return addr.readU8() === 0xE9; } catch (e) { return false; }
}

/* 在 Assembly-CSharp 中按名字找类（逐 image 尝试，兼容命名空间差异） */
function findClass(name, namespace) {
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
    return NULL;
}

/* 按方法名在类中查找 MethodInfo*，返回其 methodPointer（找不到返回 null） */
function methodPtrByName(klass, methodName) {
    if (klass === null || klass.isNull()) return null;
    try {
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(NULL);
        let mi;
        let guard = 0;
        while (guard++ < 4000) {
            mi = fn.class_get_methods(klass, iter);
            if (mi.isNull()) break;
            const nm = readStr(fn.method_get_name(mi));
            if (nm === methodName) {
                const p = mi.add(OFF.methodPointer).readPointer();
                if (inModule(p)) return p;
                return null;   /* 泛型共享方法此处可能为 0/桩 */
            }
        }
    } catch (e) { }
    return null;
}

/* ---------------- 目标地址解析：动态 API 优先，RVA+签名兜底 ---------------- */
function resolveTarget(name) {
    const t = TARGETS[name];
    if (!t) return null;

    /* 0) 入口已是跳转桩 ⇒ 说明这里就是真函数入口，且已被挂钩（本进程或残留）。
         不能再用 sigOk 判（必然读到桩）。标 via='hooked'，可直接复用。 */
    try {
        const a0 = ga.add(t.rva);
        if (isTrampolined(a0)) return { addr: a0, via: 'hooked', ok: true, sigOk: true };
    } catch (e) { }

    /* 1) 动态解析：il2cpp API 取 MethodInfo->methodPointer（仅非泛型类可用） */
    if (!t.generic) {
        try {
            const k = findClass(t.cls, t.ns);
            if (k !== null && !k.isNull()) {
                const p = methodPtrByName(k, t.method);
                if (p !== null && sigOk(p, t.sig)) {
                    return { addr: p, via: 'api', ok: true, sigOk: true };
                }
            }
        } catch (e) { }
    }

    /* 2) 静态 RVA + 序言签名校验（签名不符说明游戏已更新、RVA 漂移 → 拒绝挂钩） */
    try {
        const a = ga.add(t.rva);
        if (sigOk(a, t.sig)) return { addr: a, via: 'rva', ok: true, sigOk: true };
    } catch (e) { }

    return { addr: null, via: 'none', ok: false, sigOk: false };
}

/* ⚠️ 挂钩后**必须**刷新 resolved：pre-resolve 是在挂钩前算的，挂钩后入口变成 E9 桩；
 * 若沿用旧结果没问题，但断线重连时旧结果里 ok=false 的项会被永久钉死。
 * 这里在 ensureHooks/ensureSpendHook 之后再 resolve 一次，让 via 反映真实状态。 */
function refreshResolved() {
    for (const k of Object.keys(TARGETS)) {
        const old = resolved[k];
        const fresh = resolveTarget(k);
        /* 旧的已挂钩（listeners 有）且新的也 ok → 保留 hooked 标记 */
        if (old && old.ok && listeners[k] && fresh.ok) {
            fresh.via = fresh.via === 'hooked' ? 'hooked' : fresh.via;
        }
        resolved[k] = fresh;
    }
}

function targetAddr(name) {
    if (!resolved[name]) resolved[name] = resolveTarget(name);
    return resolved[name].ok ? resolved[name].addr : null;
}

/* ---------------- 定位玩家实例 ---------------- */
function staticRef(field) {
    if (field === null || field.isNull()) return NULL;
    const out = Memory.alloc(Process.pointerSize);
    out.writePointer(NULL);
    try { fn.field_static_get_value(field, out); } catch (e) { return NULL; }
    return out.readPointer();
}

function listAt(obj, off) {
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function scanListForClass(list, wantName) {
    if (list === null || list.isNull()) return NULL;
    let items, size;
    try {
        items = list.add(OFF.listItems).readPointer();
        size = list.add(OFF.listSize).readS32();
    } catch (e) { return NULL; }
    if (items.isNull() || size <= 0 || size > 20000) return NULL;
    for (let i = 0; i < size; i++) {
        const obj = items.add(OFF.arrayData + i * Process.pointerSize).readPointer();
        if (obj.isNull()) continue;
        const k = obj.readPointer();
        if (k.isNull()) continue;
        if (readStr(fn.class_get_name(k)) === wantName) return obj;
    }
    return NULL;
}

/* ============================ 牌库读取（纯只读） ============================
 * M_GameplayDeck 标了 IsEmbeddedModelObject = true —— 它**不会出现在世界模型列表里**
 * （实测世界模型只有 13 种类，独独没有 Deck），只能顺 M_GameplayHand.<DeckImpl> 反向取。
 * 且同时存在多个 M_GameplayHand 实例（主世界 / 未来世界），必须遍历找 DeckImpl 非空的那个。
 *
 * 泛型基类（M_Deck<TCardData>）的字段偏移在 dump.cs 里一律印成 0x0，
 * 全部用 il2cpp_field_get_offset 运行时动态取 —— 不硬编码，游戏更新也不会错位。
 */

/* 读托管字符串：+0x10 length(int32) / +0x14 utf16 chars */
function readCsString(p) {
    if (p === null || p.isNull()) return '';
    try {
        const len = p.add(0x10).readS32();
        if (len <= 0 || len > 8192) return '';
        return p.add(0x14).readUtf16String(len) || '';
    } catch (e) { return ''; }
}

/* 字段偏移：动态解析 + 缓存。
 * il2cpp_class_get_field_from_name 会沿 parent 链查找，所以拿子类 klass 也能取到泛型基类字段。 */
const _fieldOffCache = {};
function fieldOff(klass, fieldName) {
    if (klass === null || klass.isNull()) return -1;
    const key = klass.toString() + '|' + fieldName;
    if (key in _fieldOffCache) return _fieldOffCache[key];
    let off = -1;
    try {
        const f = fn.class_get_field_from_name(klass, cstr(fieldName));
        if (!f.isNull()) off = fn.field_get_offset(f);
    } catch (e) { }
    _fieldOffCache[key] = off;
    return off;
}

function fieldOffOf(obj, fieldName) {
    if (obj === null || obj.isNull()) return -1;
    try { return fieldOff(obj.readPointer(), fieldName); } catch (e) { return -1; }
}

function atOff(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return NULL;
    try { return obj.add(off).readPointer(); } catch (e) { return NULL; }
}

function i32Off(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return 0;
    try { return obj.add(off).readS32(); } catch (e) { return 0; }
}

/* 读 List<T>：_items @0x10 / _size @0x18，元素从数组的 0x20 起（obj 0x00 / bounds 0x10 / max_length 0x18） */
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
    } catch (e) { }
    return out;
}

/* 收集世界模型里所有指定类名的实例（主世界 + 未来世界 × 纯模型 + 运行时模型，按指针去重） */
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

/* GameplayCardData 上的显示名（字段在泛型基类上，偏移动态取） */
function cardDataName(dataPtr) {
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

/* ------ 卡牌费用 / 效果 ------
 * dump.cs 实证：
 *   AbstractCardData<T>.Properties : List<CardPropertyPair>（牌名旁边的属性标签）
 *   CardPropertyPair { CardProperty propertyType @0x20; int Number @0x28; int Max @0x2C }
 *   CardProperty : ScriptableEnum { string PropertyName @0x28 }   ← 资产名，如 energyCost
 *   AbstractCardData<T>.CustomDescription  / CustomDescriptionLoc  (LocalizedString)
 *   另有 EnergyUse / SingleDescription 是计算属性（要调 getter，泛型方法指针不好拿）
 *
 * 策略：直接读 Properties 里每一对 (PropertyName, Number)，运行时按名字判定费用；
 *       效果文本优先取 CustomDescription，取不到再试 CustomDescriptionLoc。
 *       都是只读，读不到就返回空，不影响别的功能。
 */
const COST_PROP_NAMES = {
    energycost: 1, cost: 1, energy: 1, extracost: 1,
    extracombatenergy: 1, extracampenergy: 1, overchargecost: 1,
};

/* 设 SHROOM_DECK_PROPS=1 时，cardInfo 会附带原始 Properties（诊断用，出货默认关） */
const DECK_DIAG_PROPS = false;

function readPropsPropName(propPtr) {
    /* CardProperty : ScriptableEnum，PropertyName @0x28（dump.cs 静态给出） */
    if (propPtr === null || propPtr.isNull()) return '';
    let v = readCsString(atOff(propPtr, 0x28));
    if (v) return v;
    const off = fieldOff(propPtr.readPointer(), 'PropertyName');
    return off < 0 ? '' : readCsString(atOff(propPtr, off));
}

/* Properties → [{name, number, max}] */
function cardProperties(dataPtr) {
    const out = [];
    if (dataPtr === null || dataPtr.isNull()) return out;
    const off = fieldOffOf(dataPtr, '<Properties>k__BackingField');
    if (off < 0) return out;
    for (const p of readListPtrs(atOff(dataPtr, off))) {
        if (p === null || p.isNull()) continue;
        const ptOff = fieldOffOf(p, 'propertyType');
        const numOff = fieldOffOf(p, 'Number');
        const maxOff = fieldOffOf(p, 'Max');
        out.push({
            name: ptOff < 0 ? '' : readPropsPropName(atOff(p, ptOff)),
            number: numOff < 0 ? 0 : i32Off(p, numOff),
            max: maxOff < 0 ? 0 : i32Off(p, maxOff),
        });
    }
    return out;
}

/* 费用：在 Properties 里找名字命中 COST_PROP_NAMES 的那个；找不到返回 -1 */
function cardCost(props) {
    for (const p of props) {
        if (!p.name) continue;
        if (COST_PROP_NAMES[p.name.toLowerCase().replace(/[\s_]/g, '')]) return p.number;
    }
    return -1;
}

/* LocalizedString 里的实际文本（结构因 Localization 版本而异，多路兜底） */
function localizedText(locPtr) {
    if (locPtr === null || locPtr.isNull()) return '';
    for (const f of ['m_Localized', '_localizedValue', 'm_Value']) {
        const off = fieldOff(locPtr.readPointer(), f);
        if (off < 0) continue;
        const v = readCsString(atOff(locPtr, off));
        if (v) return v;
    }
    return '';
}

/* 效果文本 */
function cardText(dataPtr) {
    if (dataPtr === null || dataPtr.isNull()) return '';
    const k = dataPtr.readPointer();
    let off = fieldOff(k, 'CustomDescription');
    if (off >= 0) {
        const v = readCsString(atOff(dataPtr, off));
        if (v) return v;
    }
    off = fieldOff(k, 'CustomDescriptionLoc');
    if (off >= 0) {
        const v = localizedText(atOff(dataPtr, off));
        if (v) return v;
    }
    return '';
}

/* 一条牌的完整信息：名字 + 费用 + 效果 */
function cardInfo(dataPtr) {
    const info = { name: cardDataName(dataPtr), cost: -1, text: '' };
    try {
        const props = cardProperties(dataPtr);
        info.cost = cardCost(props);
        if (DECK_DIAG_PROPS) info.props = props;
    } catch (e) { }
    try { info.text = cardText(dataPtr); } catch (e) { }
    return info;
}

/* 牌库里存的是 GameplayCardData（数据资产），不是 M_CardInstance —— 所以直接取 Name */
function listOfNames(deck, fieldName) {
    const names = [];
    for (const e of readListPtrs(atOff(deck, fieldOffOf(deck, fieldName)))) {
        names.push(cardDataName(e));
    }
    return names;
}

/* 同上，但每张牌带费用/效果 */
function listOfInfos(deck, fieldName) {
    const out = [];
    for (const e of readListPtrs(atOff(deck, fieldOffOf(deck, fieldName)))) {
        out.push(cardInfo(e));
    }
    return out;
}

/* ---------------- 牌库：营地牌库和战斗牌库同时存在，必须挑对 ----------------
 *
 * dump.cs 实证：
 *   M_Deck<T> 自带 `<DeckType>k__BackingField`（DeckType: None0/Explore1/Combat2/Foresight3/Special4）
 *   M_GameplayHand 上另有 `_isActive`（当前在用的那只手）与 `_heldCards`
 *   游戏自己取手牌走 PlayerClassProxyHands.GetHandOfType(DeckType) ——
 *   也就是说内存里同时躺着好几份牌库，随便挑一个必然拿到营地那份。
 */
const DECK_TYPE_NAME = { 0: '未知', 1: '探索', 2: '战斗', 3: '预视', 4: '特殊' };

function boolOff(obj, off) {
    if (obj === null || obj.isNull() || off < 0) return false;
    try { return atOff(obj, off).readU8() !== 0; } catch (e) { return false; }
}
/* 把所有 (手牌, 牌库) 组合连同判别依据一起收集出来 */
function deckCandidates() {
    const out = [];
    for (const h of instancesOf('M_GameplayHand')) {
        const deck = atOff(h, fieldOffOf(h, '<DeckImpl>k__BackingField'));
        if (deck === null || deck.isNull()) continue;
        const held = readListPtrs(atOff(h, fieldOffOf(h, '_heldCards')));
        let dt = -1;
        try { dt = i32Off(deck, fieldOffOf(deck, '<DeckType>k__BackingField')); } catch (e) { }
        out.push({
            hand: h, deck: deck, deckType: dt,
            inited: boolOff(deck, fieldOffOf(deck, '<isInitialised>k__BackingField')),
            active: boolOff(h, fieldOffOf(h, '_isActive')),
            handCount: held.length,
            handSize: i32Off(h, fieldOffOf(h, '_handSize')),
            draw: readListPtrs(atOff(deck, fieldOffOf(deck, '_normalShuffledDrawCards'))).length,
            discard: readListPtrs(atOff(deck, fieldOffOf(deck, '_discards'))).length,
            exhaust: readListPtrs(atOff(deck, fieldOffOf(deck, '_exhausted'))).length,
        });
    }
    return out;
}

/* 挑当前这份牌库：
 *   1. 排除没初始化完的（开新档时会有空壳）
 *   2. 有牌被打出去过的那份 = 这局正在用的，优先
 *   3. 都没动过时，看谁是当前激活的手（_isActive）
 *   4. 再按 DeckType 收口：战斗 > 探索 > 其它
 */
function pickDeck(cands) {
    const live = cands.filter(c => c.inited);
    const pool = live.length ? live : cands;
    if (!pool.length) return null;
    const rank = c => (c.deckType === 2 ? 3 : c.deckType === 1 ? 2 : c.deckType === 3 ? 1 : 0);
    const deployed = c => c.handCount + c.discard + c.exhaust;
    const sorted = pool.slice().sort((a, b) =>
        (deployed(b) > 0 ? 1 : 0) - (deployed(a) > 0 ? 1 : 0) ||
        (b.active ? 1 : 0) - (a.active ? 1 : 0) ||
        rank(b) - rank(a));
    return sorted[0];
}

/* ================= 遗忘手牌 =================
 *
 * 真机标定结论（tools/forget_probe.js + _tmp_e2e.js，游戏 0.6.41）：
 *   - 牌库实例真实 class = M_GameplayDeck（不是 M_Deck；M_Deck 是泛型基类，查不到）
 *   - 字段偏移定义在 M_GameplayDeck 上，必须传**实例 klass** 给 class_get_field_from_name
 *   - ⚠️ <isInitialised> 运行时恒为 false、_isActive 两份都是 true
 *     → 营地/战斗只能靠 DeckType 区分（1=探索 / 2=战斗）
 *   - ⚠️ _exiles 是衍生牌囤积桶（实测 66/121 张），**不是**已遗忘清单
 *     → 不能靠 AddToExileCardPile 冒充"遗忘"
 *   - ⚠️ 牌名在 AbstractCardData`1.<Name>k__BackingField @ **+0x20**（不是泛型类实例上的其他名字）；
 *     实测读到 'Blowtorch' / 'Tool' / 'Shovel' 等真实英文名
 *   - ⚠️ 堆里每一个元素指针都是**唯一**的：重复牌（Toasty×2）是两个不同的 GameplayCardData 对象
 *     → "逐张列出、删哪张删哪张"在数据层是成立的
 *
 * ☠️ 为什么不用 M_Deck<T>.RemoveCard：
 *   dump.cs 里 RemoveCard(ICardData, bool, int) 是 argc=3、methodPointer 的 RVA 也对得上
 *   （0xC35720），但**直接 NativeFunction 调用必崩** —— 泛型共享方法的真实机器码用到的
 *   寄存器数跟 dump 签名对不上（TryRemoveSpecificCard 签名 2 参、实际读 r8/r9）。
 *   il2cpp_runtime_invoke 路线同样崩。结论：泛型共享方法**不能按 argc 硬调**。
 *
 * ✅ 采用方案：直接对 List 做 RemoveAt 等价的内存操作。
 *   实测 5/5 全绿（首 / 中 / 尾 / 清空到 0 / 重复牌），删除后**其余元素顺序逐项不变**。
 *   绕过了游戏的同步逻辑（洗牌队列、事件通知），但用户明确说：
 *   "不在乎破坏游戏平衡、不需要备份回滚"。
 *
 * 用户要求：随时可忘（战斗+营地）、无视额度、不花资源、不写计数、不做存档备份。
 */

/* 可遗忘的三堆（不含当前手牌，用户已确认） */
const FORGET_STACKS = [
    { key: '_normalShuffledDrawCards', label: '抽牌堆' },
    { key: '_discards', label: '弃牌堆' },
    { key: '_exhausted', label: '已消耗' },
];

/* List.RemoveAt 的等价内存操作。
 * IL2CPP 的 List<T>：_items @0x10 / _size @0x18；元素数据从数组对象的 0x20 起。
 * 做法：index 之后的元素整体前移一格，末尾清 0 释放引用，_size 减一。
 * 不触碰 _items 的 capacity，也不重新分配数组 —— 这是最安全的最小改动。 */
function listRemoveAt(listPtr, index) {
    if (listPtr === null || listPtr.isNull()) return false;
    const items = atOff(listPtr, OFF.listItems);
    const size = i32Off(listPtr, OFF.listSize);
    if (items.isNull() || index < 0 || index >= size) return false;
    const base = items.add(OFF.arrayData);
    const ps = Process.pointerSize;
    for (let i = index; i < size - 1; i++) {
        base.add(i * ps).writePointer(base.add((i + 1) * ps).readPointer());
    }
    base.add((size - 1) * ps).writePointer(NULL);   /* 清掉尾部残留的引用 */
    listPtr.add(OFF.listSize).writeS32(size - 1);
    return true;
}

/* 列出可遗忘的牌（按堆分组，逐张列出 —— 用户已确认）
 * 每项：{ idx, stack, stackLabel, index, name, cost, ptr } */
function forgetList() {
    const out = {
        ok: false, cards: [], deckType: -1, deckTypeName: '',
        counts: { draw: 0, discard: 0, exhaust: 0 }, total: 0,
        warning: '', canForget: false, err: '',
    };
    try {
        const pick = pickDeck(deckCandidates());
        if (!pick) {
            out.warning = '未找到牌库（可能还没进游戏）';
            return out;
        }
        const deck = pick.deck;
        out.deckType = pick.deckType;
        out.deckTypeName = DECK_TYPE_NAME[pick.deckType] || '';
        /* 用 List 直改删牌，不依赖任何游戏方法指针，所以永远可用 */
        out.canForget = true;

        let counter = 0;
        for (const st of FORGET_STACKS) {
            const off = fieldOffOf(deck, st.key);
            if (off < 0) continue;
            const ptrs = readListPtrs(atOff(deck, off));
            for (let i = 0; i < ptrs.length; i++) {
                const cd = ptrs[i];
                if (cd.isNull()) continue;
                const info = cardInfo(cd);
                const name = info.name || '(未命名)';
                out.cards.push({
                    idx: counter++,          /* 全局序号，滚轮用这个 */
                    stack: st.key,
                    stackLabel: st.label,
                    index: i,                /* 堆内序号 */
                    name: name,
                    /* 汉化在 Python 侧做（src/i18n_cards.json），这里只给原始英文名 */
                    cost: info.cost,
                    ptr: cd.toString(),
                });
            }
            if (st.key === '_normalShuffledDrawCards') out.counts.draw = ptrs.length;
            else if (st.key === '_discards') out.counts.discard = ptrs.length;
            else if (st.key === '_exhausted') out.counts.exhaust = ptrs.length;
        }
        out.total = out.cards.length;
        if (!out.total) out.warning = '这份牌库里没有可遗忘的牌';
        out.ok = true;
    } catch (e) {
        out.err = String(e);
    }
    return out;
}

/* 执行遗忘：从牌库里彻底摘掉一张牌（用户已确认不写额度、不花资源、不备份）
 * ptrHex：forgetList() 里给的 cards[i].ptr（GameplayCardData 指针）
 * stackKey：该牌所在的堆
 *
 * 实现：找到指针在堆里的索引 → listRemoveAt → 前后校验数量 -1 且顺序逐项不变。
 * 重复牌各是独立对象，所以"删哪张删哪张"是精确的。 */
function forgetCard(ptrHex, stackKey) {
    const out = { ok: false, before: -1, after: -1, name: '', err: '' };
    try {
        const target = ptr(ptrHex);
        if (target.isNull()) { out.err = '空指针'; return out; }
        const pick = pickDeck(deckCandidates());
        if (!pick) { out.err = '未找到牌库'; return out; }
        const deck = pick.deck;

        /* 只允许删这三堆里的牌 */
        if (!FORGET_STACKS.some(s => s.key === stackKey)) {
            out.err = '不支持的牌堆: ' + stackKey;
            return out;
        }
        const listOff = fieldOffOf(deck, stackKey);
        if (listOff < 0) { out.err = '取不到字段偏移'; return out; }
        const listPtr = atOff(deck, listOff);

        out.name = cardDataName(target);
        const cur = readListPtrs(listPtr);
        out.before = cur.length;
        out.nameBefore = cur.map(p => cardDataName(p));

        /* 校验目标真的在这堆里（防止 UI 递过来一个过期的序号/指针）。
         * 只删**第一个**命中的索引 —— 重复牌各是独立对象，删一个只少一个。 */
        let hit = -1;
        for (let i = 0; i < cur.length; i++) {
            if (cur[i].equals(target)) { hit = i; break; }
        }
        if (hit < 0) { out.err = '这张牌已不在这堆里（可能场景已切换）'; return out; }
        out.hitIndex = hit;

        if (!listRemoveAt(listPtr, hit)) { out.err = '摘除失败（索引越界或表损坏）'; return out; }

        /* 前后校验：数量必须真的 -1，且剩下的牌顺序逐项不变 */
        const after = readListPtrs(listPtr);
        out.after = after.length;
        out.nameAfter = after.map(p => cardDataName(p));
        if (after.length !== out.before - 1) {
            out.err = '摘除后数量异常（' + out.before + ' -> ' + after.length + '）';
            return out;
        }
        const expect = out.nameBefore.slice(0, hit).concat(out.nameBefore.slice(hit + 1));
        out.orderOk = JSON.stringify(expect) === JSON.stringify(out.nameAfter);
        out.ok = true;
    } catch (e) {
        out.err = String(e);
    }
    return out;
}

/* 牌库快照：抽牌堆按**抽牌顺序**给出（已实机标定：DrawNextCard 从 List 头部 index 0 取） */
function deckSnapshot() {
    const out = {
        ok: false, draw: [], drawCount: 0, cards: [],
        discard: 0, exhaust: 0, aside: 0, top: 0,
        hand: 0, handSize: 0, warning: '',
        deckType: -1, deckTypeName: '', active: false, candidates: [],
    };
    try {
        const cands = deckCandidates();
        out.candidates = cands.map(c => ({
            deckType: c.deckType, inited: c.inited, active: c.active,
            hand: c.handCount, draw: c.draw, discard: c.discard, exhaust: c.exhaust,
        }));
        const pick = pickDeck(cands);
        if (!pick) {
            out.warning = '未找到牌库（可能不在战斗中）';
            return out;
        }
        const deck = pick.deck;
        out.deckType = pick.deckType;
        out.deckTypeName = DECK_TYPE_NAME[pick.deckType] || '';
        out.active = pick.active;
        out.cards = listOfInfos(deck, '_normalShuffledDrawCards');
        out.draw = out.cards.map(c => c.name);
        out.drawCount = out.draw.length;
        out.discard = listOfNames(deck, '_discards').length;
        out.exhaust = listOfNames(deck, '_exhausted').length;
        out.aside = listOfNames(deck, '_setAside').length;
        out.top = listOfNames(deck, '_topOfDrawPileCards').length;
        /* 手牌数必须取自**同一个** hand：之前固定取 hands[0]，营地和战斗各一份，
           数出来的永远是营地的空手牌 */
        out.hand = pick.handCount;
        out.handSize = pick.handSize;
        /* 手动洗牌/自动洗牌都会重排抽牌堆，顺序预测随即失效 → 提前提示 */
        if (out.drawCount === 0) {
            out.warning = '抽牌堆已空，下次抽牌会洗牌';
        } else if (out.drawCount <= 3) {
            out.warning = '抽牌堆仅剩 ' + out.drawCount + ' 张，即将洗牌';
        }
        out.ok = true;
    } catch (e) {
        out.warning = '读取异常: ' + e;
    }
    return out;
}

/* 找任意一个 M_CardInstance<...> 实例（用于解析泛型共享方法） */
function findAnyCardInstance() {
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
            const world = listAt(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                const list = listAt(world, off);
                if (list === null || list.isNull()) continue;
                let items, size;
                try {
                    items = list.add(OFF.listItems).readPointer();
                    size = list.add(OFF.listSize).readS32();
                } catch (e) { continue; }
                if (items.isNull() || size <= 0 || size > 20000) continue;
                for (let i = 0; i < size; i++) {
                    const obj = items.add(OFF.arrayData + i * Process.pointerSize).readPointer();
                    if (obj.isNull()) continue;
                    const k = obj.readPointer();
                    if (k.isNull()) continue;
                    if (readStr(fn.class_get_name(k)).indexOf('M_CardInstance') === 0) return obj;
                }
            }
        }
    }
    return NULL;
}

/* 所有可能的 S_WorldStateManager 实例来源 */
function getWorldManagers() {
    const res = [];
    try {
        const wsm = staticRef(fldWorldMgr);
        if (!wsm.isNull()) res.push(wsm);
    } catch (e) { }
    try {
        const svcs = staticRef(servicesField);
        const m = scanListForClass(svcs, 'S_WorldStateManager');
        if (m !== null && !m.isNull()) res.push(m);
    } catch (e) { }
    return res;
}

function findPlayer() {
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
            const world = listAt(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                const p = scanListForClass(listAt(world, off), 'M_Player');
                if (p !== null && !p.isNull()) return p;
            }
        }
    }
    return NULL;
}

function playerOk() {
    if (player === null || player.isNull()) return false;
    try {
        /* 额外校验：对象头部的 klass 指针必须仍是 M_Player，防止场景切换后写入已释放对象 */
        if (playerClassPtr !== null && !playerClassPtr.isNull()) {
            if (player.add(OFF.objKlass).readPointer().compare(playerClassPtr) !== 0) return false;
        }
        const ss = player.add(OFF.saveState).readPointer();
        if (ss.isNull()) return false;
        return !ss.add(OFF.energyData).readPointer().isNull();
    } catch (e) { return false; }
}

function playerHealth() { return player.add(OFF.saveState).readPointer().add(OFF.healthData).readPointer(); }
function playerEnergy() { return player.add(OFF.saveState).readPointer().add(OFF.energyData).readPointer(); }

/* ---------------- 挂钩 ---------------- */
function tryAttach(key, name, callbacks) {
    const a = targetAddr(name);
    if (a === null) {
        stats.hookErrors.push(name + ': 地址校验失败（疑似游戏已更新，RVA 漂移），已跳过挂钩');
        return null;
    }
    try {
        /* ⚠️ 入口已是 E9 桩（本进程此前挂过、或残留 hook）时，Interceptor.attach 会
           在桩上再套一层 —— 功能仍可用（callbacks 照样被调用），只是多一跳。
           真正的坏情况是"桩指向的 trampoline 已随旧进程消失"，那时函数本身已死，
           重启游戏才能恢复 —— 这属于游戏进程状态，代码侧无法自愈，只能在
           `isTrampolined` 命中且 `resolved.via === 'hooked'` 时照常挂钩兜住。 */
        return Interceptor.attach(a, callbacks);
    } catch (e) {
        stats.hookErrors.push(name + ': attach 失败 ' + e);
        return null;
    }
}

function ensureHooks() {
    /* 无敌：入口伤害清零，模型层与数据层都拦 */
    if (!listeners.takeDamage) {
        listeners.takeDamage = tryAttach('takeDamage', 'takeDamage', {
            onEnter(args) {
                stats.hits++;
                if (state.god) { stats.blocked++; args[1] = ptr(0); }
            }
        });
    }
    if (!listeners.phDamage) {
        listeners.phDamage = tryAttach('phDamage', 'phDamage', {
            onEnter(args) {
                if (state.god) args[1] = ptr(0);
            }
        });
    }
}

function ensureSpendHook() {
    if (!listeners.spendEnergy) {
        listeners.spendEnergy = tryAttach('spendEnergy', 'spendEnergy', {
            onEnter(args) {
                if (player === null && !args[0].isNull()) player = args[0];
                stats.spends++;
                if (state.freeCards) { stats.spendsBlocked++; args[1] = ptr(0); }
            }
        });
    }
}

/* 免费出牌：三重保险
 *   get_EnergyCost → 0   （费用显示/校验都变 0）
 *   HasEnergy      → true（无论费用多少都判定为"能量足够"）
 *   SpendEnergy    → 0   （实际扣费归零）
 */
function ensureCostHook(on) {
    if (on) {
        if (!listeners.energyCost) {
            listeners.energyCost = tryAttach('energyCost', 'energyCost', {
                onEnter() { stats.costCalls++; },
                onLeave(retval) {
                    if (state.freeCards) { stats.costZeroed++; retval.replace(ptr(0)); }
                }
            });
        }
        if (!listeners.hasEnergy) {
            listeners.hasEnergy = tryAttach('hasEnergy', 'hasEnergy', {
                onLeave(retval) {
                    stats.hasEnergyCalls++;
                    if (state.freeCards) { stats.hasEnergyAllowed++; retval.replace(ptr(1)); }
                }
            });
        }
    } else {
        for (const k of ['energyCost', 'hasEnergy']) {
            if (listeners[k]) {
                try { listeners[k].detach(); } catch (e) { }
                listeners[k] = null;
            }
        }
    }
}

/* ---------------- 周期写入 ---------------- */
function tick() {
    if (!state.god) return;
    if (!playerOk()) { player = null; }
    if (player === null || player.isNull()) {
        const p = findPlayer();
        if (p === null || p.isNull()) return;
        player = p;
    }
    try {
        if (state.god) {
            const h = playerHealth();
            h.add(OFF.invincible).writeU8(1);
        }
    } catch (e) {
        player = null;
    }
}

function init() {
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
    const mp = findClass('M_Player', 'Backend');
    if (mp !== null && !mp.isNull()) playerClassPtr = mp;
    healthDataClass = findClass('PlayerHealthData', '');

    /* 预解析全部目标，记录解析方式 */
    for (const k of Object.keys(TARGETS)) resolved[k] = resolveTarget(k);

    ensureHooks();
    ensureSpendHook();

    /* 挂钩后再解析一次：让 resolved 的 via/hooked 反映真实状态，
       并且把"因入口已是 E9 桩而首次解析失败"的项救回来（断线重连场景）。 */
    refreshResolved();

    setInterval(tick, 120);
    tick();
}

/* ---------------- RPC ---------------- */
rpc.exports = {
    init() {
        try {
            init();
            return { ok: true };
        } catch (e) {
            lastError = String(e);
            return { ok: false, error: lastError };
        }
    },
    set(feature, on) {
        if (feature === 'god') {
            state.god = !!on;
            if (!on) {
                try {
                    if (playerOk()) playerHealth().add(OFF.invincible).writeU8(0);
                } catch (e) { }
            }
        } else if (feature === 'freeCards') {
            state.freeCards = !!on;
            ensureCostHook(!!on);
        }
        return true;
    },
    status() {
        let hp = -1, maxHp = -1, en = -1, soft = -1, found = false, base = '';
        try {
            if (player === null) player = findPlayer();
            if (playerOk()) {
                found = true;
                const h = playerHealth(), e = playerEnergy();
                hp = h.add(OFF.health).readS32();
                maxHp = h.add(OFF.maxHealth).readS32();
                en = e.add(OFF.energy).readS32();
                soft = e.add(OFF.energySoftCap).readS32();
            }
        } catch (err) { }
        try { base = ga.toString(); } catch (e) { base = ''; }
        const hookInfo = {};
        for (const k of Object.keys(resolved)) {
            hookInfo[k] = resolved[k].ok ? (resolved[k].via + '@' + resolved[k].addr.toString()) : 'FAILED';
        }
        return {
            ok: true, player: found, hp: hp, maxHp: maxHp, energy: en, softCap: soft,
            god: state.god, freeCards: state.freeCards,
            callbacks: true, error: lastError,
            stats: stats, hooks: hookInfo, moduleBase: base,
        };
    },
    diag() {
        const out = {
            module: '', moduleSize: gaSize, worldMgrField: false, servicesField: false,
            playerClass: false, healthClass: false, worldMgrs: [], lists: [], services: [],
            resolved: {}, sigOkAtResolve: {}, err: lastError,
        };
        try { out.module = ga.toString(); } catch (e) { }
        out.worldMgrField = !(fldWorldMgr === null || fldWorldMgr.isNull());
        out.servicesField = !(servicesField === null || servicesField.isNull());
        out.playerClass = !!(playerClassPtr && !playerClassPtr.isNull());
        out.healthClass = !!(healthDataClass && !healthDataClass.isNull());
        for (const k of Object.keys(TARGETS)) {
            const r = resolved[k];
            out.resolved[k] = r ? (r.ok ? (r.via + ' @ ' + r.addr.toString()) : 'none') : '未解析';
            /* 注意：挂钩会把函数入口改写成跳板，因此签名只能在挂钩前校验。
               这里报告的是解析阶段（挂钩前后各一次）记录的结论。
               via='hooked' 表示入口当时已是 E9 桩 —— 地址是对的，但可能是
               "残留 hook 把函数改坏了"（旧进程没 detach 就退出），那种情况
               只能重启游戏才能恢复；此时 sigOkAtResolve 记 true 是刻意的，
               真正判断游戏是否健康请看 `trampoline` 字段。 */
            out.sigOkAtResolve[k] = r ? !!r.sigOk : false;
        }
        /* 入口首字节：E9 = 已被挂钩（正常 or 残留），其余 = 原始序言 */
        out.trampoline = {};
        for (const k of Object.keys(TARGETS)) {
            const t = TARGETS[k];
            try {
                const a = ga.add(t.rva);
                const b = a.readU8();
                out.trampoline[k] = {
                    firstByte: '0x' + b.toString(16),
                    hooked: b === 0xE9,
                    attachImpl: !!listeners[k],
                    preresolve: !!resolved[k] && resolved[k].via,
                    err: '',
                };
                if (b === 0xE9) {
                    const rel = a.add(1).readS32();
                    const dest = a.add(5).add(rel);
                    out.trampoline[k].dest = dest.toString();
                    out.trampoline[k].destInModule = inModule(dest);
                    /* dest 不在 GameAssembly 里 ⇒ 大概率是 Frida trampoline（正常）；
                       dest 是野地址 ⇒ 残留 hook 把函数弄坏了 */
                }
            } catch (e) {
                out.trampoline[k] = { err: String(e) };
            }
        }
        try {
            const svcs = staticRef(servicesField);
            if (!svcs.isNull()) {
                const items = svcs.add(OFF.listItems).readPointer();
                const size = svcs.add(OFF.listSize).readS32();
                for (let i = 0; i < size && i < 60; i++) {
                    const o = items.add(OFF.arrayData + i * Process.pointerSize).readPointer();
                    if (!o.isNull()) out.services.push(readStr(fn.class_get_name(o.readPointer())));
                }
            }
        } catch (e) { out.err3 = String(e); }
        try {
            for (const wsm of getWorldManagers()) {
                out.worldMgrs.push(wsm.toString());
                for (const wo of [OFF.mainWorld, OFF.futureWorld]) {
                    const w = listAt(wsm, wo);
                    if (w.isNull()) continue;
                    for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                        const list = listAt(w, off);
                        const rec = { woff: '0x' + wo.toString(16), off: '0x' + off.toString(16), ptr: list.toString(), count: 0, names: [] };
                        if (!list.isNull()) {
                            const items = list.add(OFF.listItems).readPointer();
                            const size = list.add(OFF.listSize).readS32();
                            rec.count = size;
                            for (let i = 0; i < size && i < 60; i++) {
                                const o = items.add(OFF.arrayData + i * Process.pointerSize).readPointer();
                                if (!o.isNull()) rec.names.push(readStr(fn.class_get_name(o.readPointer())));
                            }
                        }
                        out.lists.push(rec);
                    }
                }
            }
        } catch (e) { out.err2 = String(e); }
        return out;
    },
    /* 功能自检：直接调用 PlayerHealthData.Damage(amount) */
    testDamage(amount) {
        if (!playerOk()) { player = findPlayer(); }
        if (!playerOk()) return { ok: false, error: 'player not found' };
        const h = playerHealth();
        if (h.isNull()) return { ok: false, error: 'health data null' };
        const a = targetAddr('phDamage');
        if (a === null) return { ok: false, error: 'Damage 地址未通过校验' };
        const before = h.add(OFF.health).readS32();
        try {
            const call = new NativeFunction(a, 'bool', ['pointer', 'int']);
            call(h, amount | 0);
        } catch (e) { return { ok: false, error: String(e) }; }
        const after = h.add(OFF.health).readS32();
        return { ok: true, before: before, after: after, delta: after - before };
    },
    /* 校验动态解析与静态 RVA 是否一致 */
    crossCheck() {
        const out = {};
        for (const k of Object.keys(TARGETS)) {
            const t = TARGETS[k];
            const rva = ga.add(t.rva);
            const dyn = (function () {
                if (t.generic) {
                    const card = findAnyCardInstance();
                    if (card === null || card.isNull()) return null;
                    const kk = card.readPointer();
                    return methodPtrByName(kk, t.method);
                }
                const kk = findClass(t.cls, t.ns);
                if (kk === null || kk.isNull()) return null;
                return methodPtrByName(kk, t.method);
            })();
            out[k] = {
                rva: rva.toString(),
                rvaSigOk: sigOk(rva, t.sig),
                dynamic: dyn ? dyn.toString() : 'null',
                dynamicSigOk: dyn ? sigOk(dyn, t.sig) : false,
                match: dyn ? (dyn.compare(rva) === 0) : false,
            };
        }
        return out;
    },
    readMem(addrHex, size) {
        try {
            return Memory.readByteArray(ptr(addrHex), size);
        } catch (e) { return null; }
    },
    /* 牌库顺序（只读）：抽牌堆按抽牌顺序返回牌名 */
    deck() { return deckSnapshot(); },
    /* 遗忘：列出可忘的牌（抽牌堆 + 弃牌堆 + 已消耗，按堆分组、逐张列出） */
    forgetList() { return forgetList(); },
    /* 遗忘：真删一张。ptrHex 来自 forgetList 的 cards[i].ptr，stackKey 是它所在的堆 */
    forget(ptrHex, stackKey) { return forgetCard(ptrHex, stackKey); },
    ping() { return 'pong'; }
};
