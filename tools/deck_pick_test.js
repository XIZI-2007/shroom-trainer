/* 牌库挑选逻辑的单元测试（node 直接跑，不依赖游戏）
 *
 * 为什么单独测这个：内存里同时躺着营地牌库和战斗牌库，"挑错一份"是最容易复发的
 * 毛病（用户第一版报的就是这个）。这里把 src/agent.js 里的 pickDeck 原文抠出来跑，
 * 测的就是**出货的那份代码**，不是抄一遍的副本。
 *
 * 用法：node tools/deck_pick_test.js
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src', 'agent.js');
const src = fs.readFileSync(SRC, 'utf8');

function extract(name) {
    const start = src.indexOf('function ' + name + '(');
    if (start < 0) throw new Error('找不到函数 ' + name);
    let i = src.indexOf('{', start), depth = 0;
    for (let j = i; j < src.length; j++) {
        if (src[j] === '{') depth++;
        else if (src[j] === '}') {
            depth--;
            if (depth === 0) return src.slice(start, j + 1);
        }
    }
    throw new Error('花括号不配对: ' + name);
}

const code = extract('pickDeck');
const DECK_TYPE_NAME = { 0: '未知', 1: '探索', 2: '战斗', 3: '预视', 4: '特殊' };
const pickDeck = new Function(code + '; return pickDeck;')();

let fails = [];
function check(name, ok, info) {
    console.log((ok ? '  [OK]  ' : '  [FAIL]') + ' ' + name + (info !== undefined ? ' ' + info : ''));
    if (!ok) fails.push(name);
}

/* cand: [deckType, inited, active, handCount, draw, discard, exhaust] */
const c = (deckType, inited, active, hand, draw, discard, exhaust) =>
    ({ deckType, inited, active, handCount: hand, draw, discard, exhaust });

console.log('=== 牌库挑选（pickDeck）===');

// 1. 营地：只有探索牌库被初始化，手牌空
let r = pickDeck([c(1, true, true, 0, 12, 0, 0), c(2, false, false, 0, 0, 0, 0)]);
check('营地里挑中探索牌库', r && r.deckType === 1, r && DECK_TYPE_NAME[r.deckType]);

// 2. 战斗进行中：探索那份一动不动，战斗那份有手牌/弃牌
r = pickDeck([c(1, true, false, 0, 12, 0, 0), c(2, true, true, 3, 9, 4, 0)]);
check('★ 战斗中挑中战斗牌库（哪怕探索那份更"完整"）', r && r.deckType === 2,
    r && DECK_TYPE_NAME[r.deckType]);

// 3. 战斗第一回合：两边都是干净的一副牌，只能靠 _isActive 区分
r = pickDeck([c(1, true, false, 0, 12, 0, 0), c(2, true, true, 0, 12, 0, 0)]);
check('★ 战斗开局（都没动过）靠 _isActive 挑中战斗牌库', r && r.deckType === 2,
    r && DECK_TYPE_NAME[r.deckType]);

// 4. 顺序颠倒也要稳（别写成"取第一个"）
r = pickDeck([c(2, true, true, 3, 9, 4, 0), c(1, true, false, 0, 12, 0, 0)]);
check('候选顺序颠倒结论不变', r && r.deckType === 2, r && DECK_TYPE_NAME[r.deckType]);

// 5. 战斗牌库还没初始化（开新档的空壳）→ 不能选它
r = pickDeck([c(2, false, true, 0, 0, 0, 0), c(1, true, true, 0, 12, 0, 0)]);
check('★ 未初始化的空壳牌库被排除', r && r.deckType === 1, r && DECK_TYPE_NAME[r.deckType]);

// 6. 探索也在打着（例如营地事件）时，探索优先于预视
r = pickDeck([c(3, true, true, 2, 5, 1, 0), c(1, true, true, 2, 5, 1, 0)]);
check('探索优先于预视', r && r.deckType === 1, r && DECK_TYPE_NAME[r.deckType]);

// 7. 只认手里有牌的：两份都动过，手牌多的赢
r = pickDeck([c(1, true, true, 1, 10, 1, 0), c(2, true, true, 5, 6, 3, 0)]);
check('两份都动过时以 _isActive / DeckType 收口', r && r.deckType === 2,
    r && DECK_TYPE_NAME[r.deckType]);

// 8. 空列表
check('候选为空返回 null', pickDeck([]) === null, String(pickDeck([])));

// 9. 全部未初始化时也不崩（退化用全部候选）
r = pickDeck([c(2, false, false, 0, 0, 0, 0), c(1, false, false, 0, 0, 0, 0)]);
check('全是未初始化时也不返回 null', r !== null, r && DECK_TYPE_NAME[r.deckType]);

console.log(fails.length ? `\n失败 ${fails.length} 项: ${fails}` : '\n全部通过');
process.exit(fails.length ? 1 : 0);
