/*
 * Copyright (C) 2026 XIZI-2007. All rights reserved.
 * 未经作者书面许可，禁止复制、修改、衍生、二次分发或商用本项目全部或部分代码。
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
    /* 迭代字段（静态字段名表用）：class_get_fields(klass, &iter) 逐个返回 FieldInfo* */
    fn.class_get_fields = new NativeFunction(ex('il2cpp_class_get_fields'), 'pointer', ['pointer', 'pointer']);
    /* 父类链：class_get_fields **不遍历父类**，要 dump 继承来的字段得自己往上走 */
    fn.class_get_parent = new NativeFunction(ex('il2cpp_class_get_parent'), 'pointer', ['pointer']);
    fn.field_get_name = new NativeFunction(ex('il2cpp_field_get_name'), 'pointer', ['pointer']);
    fn.field_get_flags = new NativeFunction(ex('il2cpp_field_get_flags'), 'uint', ['pointer']);
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

/* 是否落在 GameAssembly.dll 的地址范围内。
 * ⚠️⚠️ **只适用于代码地址 / 模块内静态数据**。
 *    绝不能拿来判**托管对象引用**（GameplayCardData / List / string / ScriptableEnum…）：
 *    它们在 Unity **托管堆**上（实测 0x2844… 段），inModule 对它们**恒 false**
 *    ⇒ 会把有效数据当垃圾全丢掉。这正是"手牌名读空 / 效果 0 条"的根因。
 *    判托管引用请用 looksLikeObj()。 */
function inModule(p) {
    if (p === null || p.isNull()) return false;
    try {
        const d = p.sub(ga);
        return d.compare(ptr(0)) > 0 && d.compare(ptr(gaSize)) < 0;
    } catch (e) { return false; }
}

/* 该地址看起来是不是一个**已映射内存里的托管对象**。
 * 判据：本身已映射 且 它指向的 klass 指针也已映射（IL2CPP 对象 0x00 是 klass*）。
 * 比 inModule 宽松（托管堆也算），但能挡掉小整数/野指针 —— 结构化字段按 8 字节读
 * 时，`int` 字段会被读成 0x1/0x101 这类值，必须挡掉，否则后面读 klass 会崩。 */
function looksLikeObj(p) {
    if (p === null || p.isNull()) return false;
    try {
        if (p.compare(ptr(0x10000)) < 0) return false;      /* 显然不是指针 */
        if (p.and(ptr(7)).compare(ptr(0)) !== 0) return false;  /* 未 8 字节对齐 */
        if (Process.findRangeByAddress(p) === null) return false;
        return Process.findRangeByAddress(p.readPointer()) !== null;
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

/* ---------------- 卡牌效果 = CardIntent 列表（第 60 轮） ----------------
 *
 * 一张 GameplayCardData 的「效果」= `List<CardIntentPair>` @ +0x148（dump.cs 8239 行）。
 * 每条 pair = (意图, 数值, 目标数, 高级修饰)。这是**结构化**的，不是文本 ⇒
 * 面板可据此推演，而不必解析卡面文案。
 *
 * ⚠️⚠️ `CardIntent` 是 ScriptableEnum（每个意图一个资产），实例上**只有** `_guid`/`_assetIndex`，
 *     **没有名字**。但类上有 176 个 `protected static CardIntent __xxx` 静态字段，
 *     字段名 = 意图名（去 `__`、首字母大写）。
 *     ⇒ 用 il2cpp_class_get_fields 迭代静态字段建 `指针 → 名字` 表：
 *        零硬编码偏移、不依赖字段顺序、不依赖外部 JSON，游戏更新也不用改代码。
 *     ⚠️ 顺序方案不可靠：dump 有 176 个而 metadata 的 get_XXX 只有 171 个
 *        （少 Explode/GroupUp/DealDamage/Enrage/Nothing —— 那 5 个正好在 metadata 字符串区缺口里）。
 *     ⚠️ 两个非意图静态字段：`__allCardIntents`（CardIntent[]）与 `__all`，必须排除。
 *     ⚠️ `__shieldDONTUSE` → 去掉 `DONTUSE` 才是真名 `Shield`。
 */
const OFF_INTENT = {
    cardIntents: 0x148,        /* GameplayCardData.<Intents>k__BackingField */
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
const FIELD_STATIC = 0x10;   /* IL2CPP FieldInfo 类型标志位（il2cpp-api.h） */
const EXCLUDE_INTENT_FIELDS = { '__allCardIntents': 1, '__all': 1 };
/* StatusEffect 是**同构的 ScriptableEnum**（见 tools/status_meta.py），排除项同理 */
const EXCLUDE_STATUS_FIELDS = { '__allStatusEffects': 1 };
const MULTIPLICITY_NAME = ['None', 'Single', 'AllButSelf', 'All', 'Random', 'ToTheLeft', 'ToTheRight', 'Self'];
const INTENT_TARGET_NAME = { 0: 'None', 1: 'Player', 2: 'Card', 4: 'EnemyWorld' };

let _intentMap = null;       /* Map<指针字符串, 意图名> */
let _intentMapErr = '';
let _intentMapCount = 0;

function buildIntentMap() {
    if (_intentMap !== null) return _intentMap;
    _intentMap = new Map();
    try {
        const k = findClass('CardIntent', '');
        if (k === null || k.isNull()) { _intentMapErr = 'CardIntent 类未找到'; return _intentMap; }
        try { fn.thread_attach(fn.domain_get()); } catch (e) { /* 已 attach 则忽略 */ }
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(NULL);
        const buf = Memory.alloc(Process.pointerSize);
        let guard = 0;
        while (guard++ < 4096) {
            const f = fn.class_get_fields(k, iter);
            if (f.isNull()) break;
            if ((fn.field_get_flags(f) & FIELD_STATIC) === 0) continue;
            const nm = readStr(fn.field_get_name(f));
            if (nm.indexOf('__') !== 0 || EXCLUDE_INTENT_FIELDS[nm]) continue;
            buf.writePointer(NULL);
            try { fn.field_static_get_value(f, buf); } catch (e) { continue; }
            const v = buf.readPointer();
            /* ⚠️ CardIntent 是 ScriptableEnum 资产 ⇒ 托管堆，**不能**用 inModule 判
               （inModule 只认模块内地址，对它恒 false ⇒ 名表会整个建空）。 */
            if (!looksLikeObj(v)) continue;
            let pretty = nm.slice(2);
            const dont = pretty.indexOf('DONTUSE');
            if (dont > 0) pretty = pretty.slice(0, dont);
            pretty = pretty.charAt(0).toUpperCase() + pretty.slice(1);
            const key = v.toString();
            if (!_intentMap.has(key)) { _intentMap.set(key, pretty); _intentMapCount++; }
        }
    } catch (e) {
        _intentMapErr = String(e);
    }
    return _intentMap;
}

function intentNameOf(p) {
    if (p === null || p.isNull()) return '';
    return buildIntentMap().get(p.toString()) || '';
}

/* 一条 CardIntentPair → 结构化效果（pair 是托管对象 ⇒ 用 looksLikeObj） */
function readIntentPair(p) {
    if (!looksLikeObj(p)) return null;
    let mult = 0, tgt = 0;
    try { mult = p.add(OFF_INTENT.pairMultiplicity).readS32(); } catch (e) { }
    try { tgt = p.add(OFF_INTENT.pairTargetOverride).readS32(); } catch (e) { }
    const it = {
        number: 0, adv: 0, negateArmour: 0, skipArmour: 0,
        mult: MULTIPLICITY_NAME[mult] || ('?' + mult),
        targetOverride: INTENT_TARGET_NAME[tgt] || ('?' + tgt),
        intent: '', guid: '', assetIndex: -1,
        allowedTargets: '', primaryTarget: '', hidden: 0, hasProxy: 0,
    };
    try { it.number = p.add(OFF_INTENT.pairNumber).readS32(); } catch (e) { }
    try { it.adv = p.add(OFF_INTENT.pairAdvanced).readU8() ? 1 : 0; } catch (e) { }
    try { it.negateArmour = p.add(OFF_INTENT.pairNegateArmour).readU8() ? 1 : 0; } catch (e) { }
    try { it.skipArmour = p.add(OFF_INTENT.pairSkipArmour).readU8() ? 1 : 0; } catch (e) { }
    let ip = NULL;
    try { ip = p.add(OFF_INTENT.pairIntent).readPointer(); } catch (e) { }
    if (looksLikeObj(ip)) {
        it.intent = intentNameOf(ip);
        try { it.guid = readCsString(ip.add(OFF_INTENT.seGuid).readPointer()); } catch (e) { }
        try { it.assetIndex = ip.add(OFF_INTENT.seAssetIndex).readS32(); } catch (e) { }
        try {
            const a = ip.add(OFF_INTENT.intentAllowedTargets).readS32();
            it.allowedTargets = INTENT_TARGET_NAME[a] || ('?' + a);
        } catch (e) { }
        try {
            const q = ip.add(OFF_INTENT.intentPrimaryTarget).readS32();
            it.primaryTarget = INTENT_TARGET_NAME[q] || ('?' + q);
        } catch (e) { }
        try { it.hidden = ip.add(OFF_INTENT.intentHidden).readU8() ? 1 : 0; } catch (e) { }
        try { it.hasProxy = ip.add(OFF_INTENT.intentProxy).readPointer().isNull() ? 0 : 1; } catch (e) { }
    }
    return it;
}

/* 一条牌的意图列表（效果）。
 * ⚠️ dataPtr 是 GameplayCardData（托管对象）⇒ 判据用 looksLikeObj 而非 inModule。 */
function cardIntents(dataPtr) {
    const out = [];
    if (!looksLikeObj(dataPtr)) return out;
    let lst = NULL;
    try { lst = dataPtr.add(OFF_INTENT.cardIntents).readPointer(); } catch (e) { return out; }
    for (const pair of readListPtrs(lst)) {
        const r = readIntentPair(pair);
        if (r) out.push(r);
    }
    return out;
}

/* 一条牌的完整信息：名字 + 费用 + 效果文本。
 * ⚠️ 默认**不读**意图列表（牌库顺序卡只需要名字，别拖慢它）；
 *    要效果就走 cardIntents() / deckIntents() 显式请求。 */
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

/* ---------------- 敌人状态（StatusEffect） ----------------
 *
 * 游戏的状态是一套 `StatusEffect` ScriptableEnum（**和 CardIntent 完全同构**）：
 *   类上有 `get_XXX` 属性访问器 + `__xxx` 静态后备字段，**字段名就是状态名**。
 *   成员表由 tools/status_meta.py 从元数据抽（66 个；其中 Thorns / Spellshield /
 *   Regrowth 三个**只有后备字段、没有访问器**，只按 get_XXX 抽会漏）。
 *
 * ⚠️ 敌人持有状态集合的字段名是 **`_statusEffects`**（M_Enemy 上，`get__statusEffects`
 *    的后备字段；2026-10-06 元数据确认）。
 * ⚠️ **不要**去读 `LocalizedEnemyName` 那套来猜状态；也不要以为有"易伤/虚弱/中毒"三件套
 *    —— 那是别的游戏的模型。本游戏 66 个状态里**没有** Weakness/Poison 成员
 *    （Vulnerable 虽不在枚举里、但本地化表有 STATUS_VULNERABLE 与 tooltip，见下）。
 * ⚠️ 状态**效果**别硬编码倍率：游戏自带 TOOLTIP_STATUS_* 官方文案
 *    （已抽成 src/status_tips.json），推演必须照它来。
 *
 * 元素形状**运行时自适应**：先按"托管对象"探（0x00 是 klass*，且 klass 名含 StatusEffect），
 * 不成立再把元素当**内嵌结构体**、按 8 字节槽找指向 StatusEffect 资产的指针。
 * 形状未知时把原始字节 dump 进 `diag`，供一次性标定（避免瞎猜偏移）。
 */

let _statusMap = null;       /* Map<指针字符串, 状态名> */
let _statusMapErr = '';
let _statusMapCount = 0;

/* StatusEffect 静态字段 → 指针表（与 buildIntentMap 同构，零硬编码偏移） */
function buildStatusMap() {
    if (_statusMap !== null) return _statusMap;
    _statusMap = new Map();
    try {
        const k = findClass('StatusEffect', '');
        if (k === null || k.isNull()) { _statusMapErr = 'StatusEffect 类未找到'; return _statusMap; }
        try { fn.thread_attach(fn.domain_get()); } catch (e) { /* 已 attach */ }
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(NULL);
        const buf = Memory.alloc(Process.pointerSize);
        let guard = 0;
        while (guard++ < 4096) {
            const f = fn.class_get_fields(k, iter);
            if (f.isNull()) break;
            if ((fn.field_get_flags(f) & FIELD_STATIC) === 0) continue;
            const nm = readStr(fn.field_get_name(f));
            if (nm.indexOf('__') !== 0 || EXCLUDE_STATUS_FIELDS[nm]) continue;
            buf.writePointer(NULL);
            try { fn.field_static_get_value(f, buf); } catch (e) { continue; }
            const v = buf.readPointer();
            /* ScriptableEnum 资产在托管堆 ⇒ 只能用 looksLikeObj 判（inModule 恒 false） */
            if (!looksLikeObj(v)) continue;
            let pretty = nm.slice(2);
            const dont = pretty.indexOf('DONTUSE');
            if (dont > 0) pretty = pretty.slice(0, dont);
            pretty = pretty.charAt(0).toUpperCase() + pretty.slice(1);
            const key = v.toString();
            if (!_statusMap.has(key)) { _statusMap.set(key, pretty); _statusMapCount++; }
        }
    } catch (e) {
        _statusMapErr = String(e);
    }
    return _statusMap;
}

function statusNameOf(p) {
    if (p === null || p.isNull()) return '';
    return buildStatusMap().get(p.toString()) || '';
}

/* 一个元素的原始 8 字节槽 dump（形状未知时用于一次性标定） */
function _slotDump(p, n) {
    const out = [];
    const cnt = n || 8;
    for (let i = 0; i < cnt; i++) {
        try {
            const v = p.add(i * Process.pointerSize).readPointer();
            let s = v.isNull() ? '0' : (v.toString() + (looksLikeObj(v) ? '*' : ''));
            /* 同时给低 32 位（结构体里常是 int 计数混排） */
            try { const lo = p.add(i * 8).readS32(); s += '/' + lo; } catch (e) { }
            out.push(s);
        } catch (e) { out.push('?'); }
    }
    return out;
}

/* 敌人 → 状态列表 [{name, number, raw}]。
 * 自适应两种元素形状；都不成立时返回带 diag 的空表（**不抛**，别把整个 battle 带崩）。 */
function readEnemyStatuses(enemy, diagOut) {
    const out = [];
    if (enemy === null || enemy.isNull()) return out;
    /* ⚠️⚠️ 真机实证（2026-10-06 第 63 轮）：IL2CPP 里带 `set_` 的属性后备字段，
     *    实际字段名是 **`<_statusEffects>k__BackingField`**（带尖括号与后缀），
     *    `class_get_field_from_name('_statusEffects')` **返回 null ⇒ off=-1**。
     *    表现：statusFieldOff=-1、状态恒空、**不报错**。
     *    ⇒ 必须把带尖括号的形式也当候选。 */
    let off = -1;
    for (const cand of ['<_statusEffects>k__BackingField', '_statusEffects',
                        '<StatusEffects>k__BackingField', 'StatusEffects']) {
        off = fieldOffOf(enemy, cand);
        if (off >= 0) {
            if (diagOut) diagOut.statusFieldName = cand;
            break;
        }
    }
    if (off < 0) {
        if (diagOut) diagOut.statusFieldOff = -1;
        return out;
    }
    if (diagOut) diagOut.statusFieldOff = off;
    const lst = atOff(enemy, off);
    if (lst === null || lst.isNull()) return out;

    /* 先按 List<T> 读（最常见） */
    let elems = readListPtrs(lst);
    if (diagOut) diagOut.statusElemCount = elems.length;

    /* ⚠️ 元素可能不是"指针列表"而是**内嵌结构体数组**（List<struct>）。
     *    这时 readListPtrs 拿到的"指针"其实是结构体头 8 字节，looksLikeObj 会 false。
     *    → 退回：拿 _items 数组基址，按 stride 走（stride 先按 16 探，取值域内能对上的）。 */
    if (elems.length === 0) {
        try {
            const items = lst.add(OFF.listItems).readPointer();
            const size = lst.add(OFF.listSize).readS32();
            if (!items.isNull() && size > 0 && size < 64) {
                const base = items.add(OFF.arrayData);
                const cands = [8, 16, 24, 32];
                for (const stride of cands) {
                    let ok = 0;
                    const probe = [];
                    for (let i = 0; i < size; i++) {
                        const e = base.add(i * stride);
                        let found = NULL;
                        for (let s = 0; s < Math.min(4, stride / 8); s++) {
                            const v = e.add(s * 8).readPointer();
                            if (looksLikeObj(v) && statusNameOf(v)) { found = v; break; }
                        }
                        if (!found.isNull()) { ok++; probe.push({ stride: stride, obj: found, off: i }); }
                    }
                    if (ok > 0) {
                        if (diagOut) diagOut.statusStructStride = stride;
                        for (const pr of probe) {
                            const nm = statusNameOf(pr.obj);
                            const num = base.add(pr.off * stride).readS32();
                            out.push({ name: nm, number: num, raw: pr.obj.toString() });
                        }
                        return out;
                    }
                }
            }
        } catch (e) {
            if (diagOut) diagOut.statusStructErr = String(e);
        }
    }

    /* ---- 元素解析 ----
     * ⚠️⚠️ 真机实证（2026-10-06 第 63 轮，`fieldsOf('StatusEffectPair')` dump）：
     *     `_statusEffects` 是 `List<StatusEffectPair>`，每个元素是 **StatusEffectPair**：
     *        `_statusEffect` @0x18  → StatusEffect 资产指针
     *        `Number`       @0x20  → 层数（int32）
     *        `HiddenFromDisplay` @0x24 / `_cardData` @0x28 / `_enemyData` @0x30
     *     ⚠️ 早先按"槽位扫描 + 数值取 `s*8+4`"读，**取样点在指针高 4 字节上**
     *        ⇒ 层数读成 `0x2A6`=678 这种垃圾数（且 678<9999 不会被 sanity 拦下）。
     *        **症状：状态名对、层数全错**，比读不出来更危险。
     *     ⇒ 改成**按字段名动态取偏移**（零硬编码偏移，游戏更新也不怕），
     *        槽位扫描只作为兜底（兜底时数值取 `s*8+8`，即指针后一个对齐槽）。 */
    for (const el of elems) {
        if (!looksLikeObj(el)) continue;
        let nm = statusNameOf(el);          /* 少数情况下元素直接是 StatusEffect 资产 */
        let amt = 0;
        let host = el;
        if (!nm) {
            /* ① 首选：按字段名解析 StatusEffectPair */
            const seOff = fieldOffOf(el, '_statusEffect');
            const numOff = fieldOffOf(el, 'Number');
            if (seOff >= 0) {
                const se = atOff(el, seOff);
                if (se !== null && !se.isNull()) nm = statusNameOf(se);
                if (nm && numOff >= 0) {
                    try { amt = el.add(numOff).readS32(); } catch (e) { }
                    if (diagOut) diagOut.statusElemShape = 'StatusEffectPair(字段名)';
                }
            }
            /* ② 兜底：槽位扫描。⚠️ 数值必须取 **指针后一个对齐槽**（+8），
                  不是 +4（那是指针自己高 4 字节，会读出天文数字/垃圾）。 */
            if (!nm) {
                for (let s = 0; s < 6; s++) {
                    let v = NULL;
                    try { v = el.add(s * 8).readPointer(); } catch (e) { continue; }
                    if (!looksLikeObj(v)) continue;
                    const cand = statusNameOf(v);
                    if (cand) {
                        nm = cand;
                        try {
                            const n8 = el.add(s * 8 + 8).readS32();
                            amt = (n8 >= 0 && n8 <= 9999) ? n8 : 0;
                        } catch (e) { amt = 0; }
                        host = el;
                        if (diagOut) diagOut.statusElemShape = '槽位扫描(slot=' + s + ')';
                        break;
                    }
                }
            }
        }
        if (!nm) {
            if (diagOut) {
                diagOut.statusUnknown = (diagOut.statusUnknown || []);
                if (diagOut.statusUnknown.length < 4) {
                    diagOut.statusUnknown.push({ ptr: el.toString(), slots: _slotDump(el) });
                }
            }
            continue;
        }
        out.push({ name: nm, number: amt, raw: host.toString() });
    }
    return out;
}

/* ⚠️ 临时标定用：dump 某个 klass 及其父类的全部字段（name/offset/type）。只读。 */
function _dumpFieldChain(k) {
    const chain = [];
    let depth = 0;
    while (k !== null && !k.isNull() && depth++ < 8) {
        const kn = readStr(fn.class_get_name(k)) || '?';
        const fields = [];
        const iter = Memory.alloc(Process.pointerSize);
        iter.writePointer(NULL);
        let guard = 0;
        while (guard++ < 512) {
            const f = fn.class_get_fields(k, iter);
            if (f.isNull()) break;
            let ty = '';
            try { ty = readStr(fn.type_get_name(fn.field_get_type(f))) || ''; } catch (e) { ty = ''; }
            fields.push({
                name: readStr(fn.field_get_name(f)) || '?',
                off: fn.field_get_offset(f),
                type: ty,
            });
        }
        chain.push({ klass: kn, fields: fields });
        try { k = fn.class_get_parent(k); } catch (e) { break; }
    }
    return chain;
}

/* 玩家侧状态。
 * ⚠️⚠️ 真机实证（2026-10-06 第 63 轮，dump 了 `M_Player` 及其父类 `Model`1` 的**全部**
 *    字段链）：**M_Player 上没有任何状态字段**（`_statusEffects` / `_statuses` /
 *    `statusEffects` 一个都没有；`_saveState` 是存档聚合，不是状态列表）。
 *    元数据侧也一致：`_playerStatusEffects` / `PlayerStatus` 全库 0 命中；
 *    `get_StatusEffects`/`_statusEffects` 只挂在 **M_Enemy** 上。
 *
 * ⇒ **玩家状态恒为空是预期行为，不是 bug**。且这不影响推演正确性：
 *    · 推演真正需要的 `SpellWeakness`（打牌自伤）**挂在敌人身上**
 *      —— 真机实测敌人3 就带 `SpellWeakness=<n>`，我们的敌人状态读取已覆盖它；
 *    · 其余 self 侧状态（Blessed/Charged/Thorns）都不改变"本回合出牌"的数值。
 *
 * 本函数保留为**尽力读取**：万一以后版本加了玩家状态字段，这里能自动接上。
 * 读不到就返回空表（**不抛**，也不能因此让 battle 失败）。 */
function readPlayerStatuses(diagOut) {
    const out = [];
    try {
        if (player === null) player = findPlayer();
        if (!playerOk()) { if (diagOut) diagOut.playerNoObj = 1; return out; }
        /* 候选字段名：按"可能就是它"的推测顺序试，命中即可（不硬编偏移）。
           ⚠️ 带 set_ 的属性后备字段真名是 `<xxx>k__BackingField`，两种写法都要试。 */
        const cands = [];
        for (const f of ['statusEffects', 'statuses', 'playerStatuses', 'activeStatuses']) {
            cands.push('<' + f + '>k__BackingField', '_' + f, f);
        }
        for (const f of cands) {
            let off = -1;
            try { off = fieldOffOf(player, f); } catch (e) { off = -1; }
            if (off < 0) continue;
            const lst = atOff(player, off);
            if (lst === null || lst.isNull()) continue;
            /* 元素形状不确定 ⇒ 先按指针列表读；形如"条目对象"的再在前几槽找资产指针 */
            const els = readListPtrs(lst);
            for (const el of els) {
                if (!looksLikeObj(el)) continue;
                let nm = statusNameOf(el);
                let amt = 0;
                if (!nm) {
                    for (let s = 0; s < 6; s++) {
                        let v = NULL;
                        try { v = el.add(s * 8).readPointer(); } catch (e) { continue; }
                        if (!looksLikeObj(v)) continue;
                        const c = statusNameOf(v);
                        if (c) {
                            nm = c;
                            try { amt = el.add(s * 8 + 4).readS32(); } catch (e) { }
                            if (amt <= 0 || amt > 9999) {
                                try { amt = el.add(s * 8 + 8).readS32(); } catch (e) { }
                            }
                            break;
                        }
                    }
                }
                if (nm) out.push({ name: nm, number: amt, raw: el.toString() });
            }
            if (out.length) {
                if (diagOut) diagOut.playerStatusField = f;
                return out;
            }
        }
        if (diagOut) diagOut.playerStatusUnknown = 1;
    } catch (e) {
        if (diagOut) diagOut.playerStatusErr = String(e);
    }
    return out;
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

/* ================= 战况读取（纯只读）—— 打法推演的输入 =================
 *
 * 三件套：敌人（血量/护甲/存活）+ 精力 + 手牌。
 * 全部走 fieldOff() 动态取偏移（泛型基类字段在 dump 里一律 0x0，硬编码必错）。
 *
 * dump.cs / metadata 实证（字段名，非偏移）：
 *   M_EnemyController : enemies(List<M_Enemy>) / _currentEnemies
 *   M_Enemy           : _healthData / _isBoss / _hasRetreated / LocalizedEnemyName
 *   EnemyHealthData   : _startingHealth(当前血) / _maxHealth / _armour
 *     ⚠️ 当前血量字段叫 `_startingHealth`（不是 _currentHealth），别按语义瞎猜。
 *   M_CardInstance<T> : **`<DataImpl>k__BackingField`** → 卡数据资产（GameplayCardData）
 *     ⚠️ 这个字段名不是猜的：9-24 轮真机标定实证「取 `<DataImpl>` 得到的牌名正常」。
 *        却**不在 dump 的字段名表面上**（dump 里叫 get_IData/IData），也不叫 `_cardData`
 *        ⇒ 卡实例 → 卡数据**只能走这个字段**，见 cardDataOfInstance()。
 */

/* 卡实例(M_GameplayCardInstance / M_CardInstance<T>) → 卡数据资产(GameplayCardData)。
 * ⚠️⚠️ **不要用 inModule() 判这个字段**：卡数据是 Unity ScriptableObject，
 *    指针落在**托管堆**上（实测 0x28441836480），inModule 只认 GameAssembly.dll
 *    内部地址 ⇒ 对托管引用**恒 false** ⇒ 会被整段丢掉，表现就是"手牌名读空"。
 *    （同类教训：dump 里所有托管字段都显示 ptr(oom ...)。）
 * 判据改为：字段存在（off>=0）+ 非空。拿到的对象 klass 是 GameplayCardData 才算数，
 * 所以用 klass 名做一次校验，避免误取到别的指针字段。
 * ⚠️ fieldOffOf 走 class_get_field_from_name，会沿父类链找 —— 数据字段挂在泛型基类
 *    M_CardInstance<T> 上（实测 off=0x168），拿子类 klass 也取得到。
 * ⚠️ 候选顺序：`<DataImpl>` 是**真机实证名**，必须排第一；其余只是跨版本兜底。 */
const CARD_DATA_FIELDS = ['<DataImpl>k__BackingField', '_cardData', '<Data>k__BackingField', 'IData', '_data'];

function cardDataOfInstance(inst) {
    if (inst === null || inst.isNull()) return NULL;
    let firstNonNull = NULL;
    for (const f of CARD_DATA_FIELDS) {
        const off = fieldOffOf(inst, f);
        if (off < 0) continue;
        const v = atOff(inst, off);
        if (v.isNull()) continue;
        /* 首选能确认 klass 是卡数据的那一个 */
        try {
            const kn = readStr(fn.class_get_name(v.readPointer()));
            if (kn.indexOf('CardData') >= 0) return v;
        } catch (e) { }
        if (firstNonNull.isNull()) firstNonNull = v;
    }
    return firstNonNull;
}

/* 找 M_EnemyController 实例（世界模型里找；找不到再扫服务列表） */
function findEnemyController() {
    for (const wsm of getWorldManagers()) {
        for (const worldOff of [OFF.mainWorld, OFF.futureWorld]) {
            const world = listAt(wsm, worldOff);
            if (world.isNull()) continue;
            for (const off of [OFF.pureModels, OFF.runtimeModels]) {
                const c = scanListForClass(listAt(world, off), 'M_EnemyController');
                if (c !== null && !c.isNull()) return c;
            }
        }
    }
    try {
        for (const wsm of getWorldManagers()) {
            const svcs = staticRef(servicesField);
            const c = scanListForClass(svcs, 'M_EnemyController');
            if (c !== null && !c.isNull()) return c;
            break;
        }
    } catch (e) { }
    return NULL;
}

/* 敌人们表：M_EnemyController.enemies（拿不到就退 _currentEnemies） */
function enemyListOf(ctrl) {
    for (const f of ['enemies', '_currentEnemies', '_enemies']) {
        const off = fieldOffOf(ctrl, f);
        if (off < 0) continue;
        const lst = atOff(ctrl, off);
        const arr = readListPtrs(lst);
        if (arr.length) return arr;
    }
    return [];
}

/* ⚠️ 敌人名字：**游戏压根没给敌人赋名字**（第 45 轮真机 + 离线双证，别再挖了）
 *
 *   M_Enemy.LocalizedEnemyName @0x150 的类型**不是 string**，而是
 *   `LocalizedFallbackText` 托管类：字段表只有
 *       FallbackText @0x10 : System.String     （实测 = 空串）
 *       LocalizeKey  @0x18 : LocalizedString   （对象在，但 m_TableReference /
 *                            m_TableEntryReference 逐 8 字节扫 0x00–0x78 **全是 0**）
 *   ⇒ 这个物体根本没被填过名字。游戏自己的警告串也这么说：
 *       `MISSING LocalizedEnemyName for [ENEMY_NAME] (NULL-ENEMY-DATA-OR-UNASSIGNED-MODEL)`
 *   ⇒ 把 `atOff(enemy, off)` 当**托管 string** 交给 readCsString 是**读法错的**
 *      （+0x10 处其实是 FallbackText 指针的低 32 位 ≈ 0xfa85f40），必然读空。
 *
 *   唯一真 string 名在 `V_Enemy.<EnemyName>k__BackingField` @0x1C8，但视图不可达：
 *   V_Enemy 不在 world 模型列表里，M_Enemy 逐槽扫 0x08–0x288 也没有任何指向它
 *   或其委托 `_target` 的引用。
 *
 *   ⇒ **放弃取名**，UI 侧按"从左到右"编号（① ② ③…）。本函数只回原始值供诊断，
 *     绝不拿它当显示名。
 */
function readEnemyRawName(enemy) {
    try {
        const off = fieldOffOf(enemy, 'LocalizedEnemyName');
        if (off < 0) return '';
        const v = atOff(enemy, off);
        if (v.isNull()) return '';
        /* 只有 klass 名字确实是 String 才当托管 string 解
           （LocalizedFallbackText 的话这里必不是 String） */
        let kn = '';
        try { kn = readStr(fn.class_get_name(v.readPointer())) || ''; } catch (e) { return ''; }
        if (kn !== 'String' && kn.indexOf('String') !== 0) return '';
        return readCsString(v) || '';
    } catch (e) {
        return '';
    }
}

/* 一个 M_Enemy → 结构化战况。idx 是它在控制器列表里的位置（0 起，UI 显示时 +1）。 */
function readEnemy(enemy, idx) {
    const seq = (idx === undefined || idx < 0) ? -1 : idx + 1;
    const out = {
        name: seq > 0 ? ('敌人' + seq) : '敌人', seq: seq, rawName: '',
        hp: -1, maxHp: -1, armour: 0, alive: true,
        boss: false, retreated: false, ptr: enemy.toString(),
        statuses: [], statusDiag: null,
    };
    try {
        const hd = atOff(enemy, fieldOffOf(enemy, '_healthData'));
        if (!hd.isNull()) {
            const oh = fieldOffOf(hd, '_startingHealth');
            const om = fieldOffOf(hd, '_maxHealth');
            const oa = fieldOffOf(hd, '_armour');
            if (oh >= 0) out.hp = i32Off(hd, oh);
            if (om >= 0) out.maxHp = i32Off(hd, om);
            if (oa >= 0) out.armour = i32Off(hd, oa);
            out.alive = out.hp > 0;
        }
    } catch (e) { }
    try { out.boss = boolOff(enemy, fieldOffOf(enemy, '_isBoss')); } catch (e) { }
    try { out.retreated = boolOff(enemy, fieldOffOf(enemy, '_hasRetreated')); } catch (e) { }
    /* ⚠️ 只读进 rawName 供诊断：游戏没给敌人名字，别拿它当显示名用。
       也别再退回按卡数据字段取名那条路 —— 那是**结构性死代码**：卡名读法按
       GameplayCardData 的 `<Name>k__BackingField` / `_overrideDisplayName` / `LastLocKey`
       读，而这里手上是 M_Enemy 模型，那几个字段在它身上全是 -1。 */
    out.rawName = readEnemyRawName(enemy);
    /* 状态（易伤/震慑/腐朽…）—— 推演要按真实层数算伤害倍率 */
    try {
        const d = {};
        out.statuses = readEnemyStatuses(enemy, d);
        /* ⚠️ **"没有状态"和"读不出来"必须分开**：
           · `statusElemCount === 0` ⇒ 这只敌人**本来就没有状态**，是正常结果；
           · 只有"字段名没找到"（statusFieldOff < 0）、"元素认不出"（statusUnknown）、
             "内嵌结构体探测过"（statusStructStride）才是**真读不出来**。
           ⚠️ 早先把 statusFieldOff（=6 位正数偏移）当 truthy 就判失败 ⇒ 每只没状态的
              敌人都被标成"读取失败"，推演每次都弹一条"状态没读出来"的假告警（踩过）。 */
        const failed = (d.statusFieldOff < 0) || !!d.statusUnknown ||
                       (d.statusStructStride !== undefined) || !!d.statusStructErr;
        if (failed) out.statusDiag = d;
    } catch (e) { out.statusDiag = { err: String(e) }; }
    return out;
}

/* 战况快照：敌人 + 精力 + 手牌（一张牌 → {name, cost, intents}） */
function battleSnapshot(withIntents) {
    const out = {
        ok: false, warning: '',
        enemies: [], enemyCount: 0, aliveEnemyCount: 0, enemyHpTotal: 0,
        hp: -1, maxHp: -1, energy: -1, softCap: -1,
        playerStatuses: [], playerStatusDiag: null,
        hand: [], handCount: 0, handSize: 0,
        deckType: -1, deckTypeName: '',
    };
    /* ---- 玩家：血量 / 精力（已有实现，直接复用） ---- */
    try {
        if (player === null) player = findPlayer();
        if (playerOk()) {
            const h = playerHealth(), e = playerEnergy();
            out.hp = i32Off(h, OFF.health);
            out.maxHp = i32Off(h, OFF.maxHealth);
            out.energy = i32Off(e, OFF.energy);
            out.softCap = i32Off(e, OFF.energySoftCap);
        }
    } catch (e) { out.warning = '玩家数据读取失败: ' + e; }

    /* ---- 玩家状态（自身增益/惩罚）----
       ⚠️ 元数据无确证字段 ⇒ 尽力读，读不到就当空（见 readPlayerStatuses 注释）。 */
    try {
        const d = {};
        out.playerStatuses = readPlayerStatuses(d);
        if (!out.playerStatuses.length && Object.keys(d).length) out.playerStatusDiag = d;
    } catch (e) { out.playerStatusDiag = { err: String(e) }; }

    /* ---- 敌人 ---- */
    try {
        const ctrl = findEnemyController();
        if (ctrl !== null && !ctrl.isNull()) {
            let ei = 0;
            for (const e of enemyListOf(ctrl)) {
                const r = readEnemy(e, ei++);
                out.enemies.push(r);
                if (r.alive && !r.retreated) {
                    out.aliveEnemyCount++;
                    if (r.hp > 0) out.enemyHpTotal += r.hp;
                }
            }
            out.enemyCount = out.enemies.length;
        } else {
            out.warning = (out.warning ? out.warning + '；' : '') + '未找到敌人控制器（可能不在战斗中）';
        }
    } catch (e) { out.warning += '；敌人读取失败: ' + e; }

    /* ---- 手牌：挑对那份手（营地/战斗各一份） ---- */
    try {
        const cands = deckCandidates();
        const pick = pickDeck(cands);
        if (pick) {
            out.deckType = pick.deckType;
            out.deckTypeName = DECK_TYPE_NAME[pick.deckType] || '';
            out.handSize = pick.handSize;
            const held = readListPtrs(atOff(pick.hand, fieldOffOf(pick.hand, '_heldCards')));
            for (const c of held) {
                /* 手牌元素是 M_GameplayCardInstance（运行时实例），不是数据资产。
                   顺卡数据字段拿到 GameplayCardData 再读名字/费用/意图。 */
                const data = cardDataOfInstance(c);
                const src = data.isNull() ? c : data;
                const info = cardInfo(src);
                const row = { name: info.name, cost: info.cost, ptr: src.toString() };
                if (withIntents) row.intents = cardIntents(src);
                out.hand.push(row);
            }
            out.handCount = out.hand.length;
        } else {
            out.warning = (out.warning ? out.warning + '；' : '') + '未找到手牌';
        }
    } catch (e) { out.warning += '；手牌读取失败: ' + e; }

    out.ok = true;
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
    /* 卡牌效果（只读）：牌库每张牌的 (意图, 数值, 目标数) 列表 —— 理解效果用 */
    deckIntents(ptrHex) {
        if (ptrHex) return { ok: true, cards: [{ ptr: ptrHex, intents: cardIntents(ptr(ptrHex)) }] };
        const snap = deckSnapshot();
        const out = { ok: snap.ok, deckTypeName: snap.deckTypeName, cards: [] };
        /* 牌库存的就是 GameplayCardData ⇒ 从快照的指针再取一次意图即可，
           不必让 listOfInfos 每次都背上意图读取的开销 */
        const cands = deckCandidates();
        const pick = pickDeck(cands);
        if (!pick) { out.warning = '未找到牌库（可能不在战斗中）'; return out; }
        for (const e of readListPtrs(atOff(pick.deck, fieldOffOf(pick.deck, '_normalShuffledDrawCards')))) {
            out.cards.push({ name: cardDataName(e), cost: cardInfo(e).cost, intents: cardIntents(e) });
        }
        return out;
    },
    /* 意图名表自检（静态字段扫描结果） */
    intentMap() {
        buildIntentMap();
        const names = [];
        for (const kv of _intentMap) names.push(kv[1]);
        return { ok: !_intentMapErr, count: _intentMapCount, err: _intentMapErr, names: names };
    },
    /* 状态名表自检（StatusEffect 静态字段扫描结果，与 intentMap 同构） */
    statusMap() {
        buildStatusMap();
        const names = [];
        for (const kv of _statusMap) names.push(kv[1]);
        names.sort();
        return { ok: !_statusMapErr, count: _statusMapCount, err: _statusMapErr, names: names };
    },
    /* ⚠️ 标定用：dump 某个类的字段链（name/offset/type）。只读。
       ⚠️ 走 `il2cpp_class_from_name` ⇒ 需要**精确命名空间**；
          `M_Player` 这类查不到（真机实测 'class not found'）。
       第 63 轮用它标定了 `StatusEffectPair`（_statusEffect@0x18 / Number@0x20）与
       `StatusEffect`（68 个 __xxx 静态字段，比离线多 Vulnerable/Poisoned）。 */
    fieldsOf(className) {
        const out = { ok: false, klass: className, chain: [] };
        try {
            const k = findClass(className, '');
            if (k === null || k.isNull()) { out.error = 'class not found'; return out; }
            out.chain = _dumpFieldChain(k);
            out.ok = true;
        } catch (e) { out.error = String(e); }
        return out;
    },
    /* 只读：把每个敌人的状态 + 原始诊断 dump 出来（标定用） */
    enemyStatus() {
        const out = { ok: false, count: 0, enemies: [], statusMapCount: 0 };
        try {
            buildStatusMap();
            out.statusMapCount = _statusMapCount;
            out.statusMapErr = _statusMapErr;
            const ctrl = findEnemyController();
            if (ctrl === null || ctrl.isNull()) {
                out.warning = '未找到敌人控制器';
                out.ok = true;
                return out;
            }
            let ei = 0;
            for (const e of enemyListOf(ctrl)) {
                const d = {};
                const st = readEnemyStatuses(e, d);
                out.enemies.push({
                    seq: ei + 1, hp: -1, statuses: st,
                    statusFieldOff: d.statusFieldOff, elemCount: d.statusElemCount,
                    stride: d.statusStructStride, unknown: d.statusUnknown || [],
                    err: d.statusStructErr || '',
                });
                ei++;
            }
            out.count = out.enemies.length;
            out.ok = true;
        } catch (e) {
            out.error = String(e);
        }
        return out;
    },
    /* 战况（只读）：敌人血量/护甲 + 精力 + 手牌；withIntents=true 时连手牌效果一起给。
       打法推演的输入就靠它，不必让调用方手工填局面。 */
    battle(withIntents) { return battleSnapshot(!!withIntents); },
    /* 遗忘：列出可忘的牌（抽牌堆 + 弃牌堆 + 已消耗，按堆分组、逐张列出） */
    forgetList() { return forgetList(); },
    /* 遗忘：真删一张。ptrHex 来自 forgetList 的 cards[i].ptr，stackKey 是它所在的堆 */
    forget(ptrHex, stackKey) { return forgetCard(ptrHex, stackKey); },
    ping() { return 'pong'; }
};
