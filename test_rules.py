#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞行棋 —— 官方 10 条规则 自动化审计

两部分:
  A. 参考实现差分测试: 用独立实现按规则 4/5 计算落点, 与引擎 preview_move
     在全部 (颜色, 位置, 点数) 组合上对比 (52 x 6 x 4 = 1248 组)。
  B. 逐条规则定向测试: 10 条规则各自的可执行断言。

用法: python3 test_rules.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import (  # noqa: E402
    FlightChessGame, GameError, BOARD, NEXT_SAME, FLIGHT_TO, FLIGHT_MID,
    START_CELLS, HOME_ENTRANCES, HOME_PATHS, FINISHED_POS, TILE_COLOR_IDX,
    TILE_COUNT,
)

FAILS = []
PASSED = 0


def check(cond, msg):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILS.append(msg)
        print("  FAIL  " + msg)


def section(title):
    print("")
    print("=== " + title + " ===")


def mk(n=4, seed=7):
    specs = [{"name": "P%d" % i, "is_ai": False} for i in range(n)]
    return FlightChessGame(specs, seed=seed)


def pcol(g, c):
    """取颜色索引为 c 的玩家 (注意: 玩家下标 != 颜色下标, 随机先手会打乱)。"""
    for p in g.players:
        if p.color_index == c:
            return p
    raise AssertionError("no player with color %d" % c)


def put(g, c, idx, pos, finished=False):
    """把颜色 c 的第 idx 枚棋子放到 pos。"""
    p = pcol(g, c)
    pc = p.pieces[idx]
    pc.pos = pos
    pc.finished = finished
    if finished:
        p.finished_pieces += 1
    return pc


FLIGHT_OWNER = {int(e["from"]): int(e["owner"]) for e in BOARD["flightMap"]}

# ─────────────────────────────────────────────
# A. 独立参考实现 (按规则 4/5 文字直接实现)
# ─────────────────────────────────────────────
def ref_enter_home(color, rem):
    path = HOME_PATHS[color]
    last = len(path) - 1
    if rem <= 0:
        return path[0], []
    if rem == last:
        return path[last], []
    if rem > last:
        over = rem - last
        idx = last - over
        if idx < 1:
            idx = 1
        return path[idx], ["bounce"]
    return path[rem], []


def ref_resolve(cur, color):
    """落点后的跳跃/飞行连锁 (规则 5)。"""
    events = []
    if cur in FLIGHT_TO:
        events.append("flight")
        cur = FLIGHT_TO[cur]
        nxt = NEXT_SAME.get(cur)
        if nxt is not None:
            events.append("jump")
            cur = nxt
    elif TILE_COLOR_IDX[cur] == color:
        nxt = NEXT_SAME.get(cur)
        if nxt is not None:
            events.append("jump")
            cur = nxt
    # 跳跃终点是飞行起点 -> 按红箭头飞向对面飞行终点, 再继续同色跳跃
    guard = 0
    while cur in FLIGHT_TO and guard < 10:
        guard += 1
        events.append("flight")
        cur = FLIGHT_TO[cur]
        nxt = NEXT_SAME.get(cur)
        if nxt is not None:
            events.append("jump")
            cur = nxt
    return cur, events


def ref_advance(pos, roll, color):
    path = HOME_PATHS[color]
    entrance = HOME_ENTRANCES[color]
    events = []
    if path[1] <= pos <= path[-1]:
        idx = path.index(pos)
        last = len(path) - 1
        ni = idx + roll
        if ni > last:
            over = ni - last
            ni = last - over
            if ni < 1:
                ni = 1
            events.append("bounce")
        return path[ni], events
    cur = pos
    remaining = roll
    if cur == entrance:
        return ref_enter_home(color, remaining)
    while remaining > 0:
        cur = (cur + 1) % TILE_COUNT
        remaining -= 1
        if cur == entrance:
            return ref_enter_home(color, remaining)
        if TILE_COLOR_IDX[cur] == color:
            cur, ev = ref_resolve(cur, color)
            events.extend(ev)
            if cur == entrance:
                return ref_enter_home(color, remaining)
    return cur, events


section("A. 参考实现差分测试 (52 格 x 6 点 x 4 色 = 1248 组)")
diff_bad = []
for c in range(4):
    g = mk(4)
    p = pcol(g, c)
    for pos in range(TILE_COUNT):
        for roll in range(1, 7):
            if pos in HOME_PATHS[c][1:]:
                continue
            pc = p.pieces[0]
            pc.pos = pos
            pc.finished = False
            exp_pos, _ = ref_advance(pos, roll, c)
            try:
                pv = g.preview_move(pc, roll)
            except GameError as e:
                diff_bad.append((c, pos, roll, "raise %s" % e, None))
                continue
            if not pv.get("ok") or pv["to"] != exp_pos:
                diff_bad.append((c, pos, roll, exp_pos, pv.get("to")))
if diff_bad:
    for row in diff_bad[:15]:
        print("  FAIL  色%d 位置%d 点%d -> 期望 %s 实际 %s" % row)
    check(False, "差分测试 %d/%d 组不一致" % (len(diff_bad), 1248))
else:
    check(True, "差分测试 1248 组全部一致 (参考实现 vs 引擎)")

# ───────────────────────── 规则 1 ─────────────────────────
section("规则 1: 比赛人数 2~4 人")
for n in (2, 3, 4):
    try:
        g = mk(n)
        check(len(g.players) == n, "%d 人开局成功" % n)
    except GameError as e:
        check(False, "%d 人开局失败: %s" % (n, e))
for n in (1, 5):
    try:
        mk(n)
        check(False, "%d 人应被拒绝" % n)
    except GameError:
        check(True, "%d 人被正确拒绝" % n)

# ───────────────────────── 规则 9 ─────────────────────────
section("规则 9: 先后手 (随机红色先手 + 顺时针)")
firsts = set()
ok9 = True
for seed in range(12):
    g = mk(4, seed=seed)
    firsts.add(g.first_seat)
    if g.order != [(g.first_seat + k) % 4 for k in range(4)]:
        ok9 = False
    if g.players[g.first_seat].color_index != 0:
        ok9 = False
    if g.turn != g.first_seat:
        ok9 = False
check(ok9, "12 局: 行动顺序顺时针 + 先手为红色 + 首回合是自己")
check(len(firsts) > 1, "先手玩家随机 (出现 %d 种不同先手)" % len(firsts))

# ───────────────────────── 规则 2/3 ─────────────────────────
section("规则 2/3: 起飞规则 (5 或 6 点) 与盖轮")
g = mk(4)
pc = pcol(g, 0).pieces[0]
for roll in (1, 2, 3, 4):
    pv = g.preview_move(pc, roll)
    check(pv.get("reason") == "need_takeoff", "点数 %d 不能起飞" % roll)
for roll in (5, 6):
    pv = g.preview_move(pc, roll)
    check(pv.get("ok") and pv["to"] == START_CELLS[0] and "takeoff" in pv["events"],
          "点数 %d 起飞到停机坪 (格 %d)" % (roll, START_CELLS[0]))

g = mk(4)
g.rng = type("F", (), {"randint": lambda s, a, b: 3, "calls": 0})()
before = g.turn
st = g.roll()
check(g.turn != before and any("盖轮" in e["text"] for e in st["log"]),
      "无可用棋子 + 点数 3 -> 盖轮跳过")

g = mk(4)
put(g, 0, 0, 0)
put(g, 0, 1, 30)
red = pcol(g, 0)
g.turn = red.id
g.rng = type("F", (), {"randint": lambda s, a, b: 1, "calls": 0})()
st = g.roll()
check(st.get("needPieceChoice") is True and len(st.get("movablePieces", [])) == 2,
      "2 枚已起飞棋子 -> 可任选其一 (可选 %d 枚)" % len(st.get("movablePieces", [])))

# ───────────────────────── 规则 5a ─────────────────────────
section("规则 5a: 落在同色格 -> 跳到前方下一个同色格")
for c in range(4):
    cells = [i for i in range(TILE_COUNT)
             if TILE_COLOR_IDX[i] == c and i in NEXT_SAME]
    hit = None
    for src in cells:
        if src in HOME_ENTRANCES:
            continue
        if TILE_COLOR_IDX[NEXT_SAME[src]] != c:
            continue
        if NEXT_SAME[src] in FLIGHT_TO:
            continue
        if src in FLIGHT_TO:
            continue   # 飞行起点: 规则 5b 要求先飞再跳, 单独测试
        hit = src
        break
    if hit is None:
        continue
    g = mk(4)
    put(g, c, 0, (hit - 1) % TILE_COUNT)
    p = pcol(g, c)
    g.turn = p.id
    pv = g.preview_move(p.pieces[0], 1)
    check(pv.get("ok") and pv["to"] == NEXT_SAME[hit] and "jump" in pv["events"],
          "颜色 %d 落格 %d -> 跳到 %d (实际 %s)" % (c, hit, NEXT_SAME[hit], pv.get("to")))

# ───────────────────────── 规则 5b ─────────────────────────
section("规则 5b: 飞行起点 -> 对面飞行终点 -> 继续跳")
for frm, to in sorted(FLIGHT_TO.items()):
    owner = FLIGHT_OWNER[frm]
    g = mk(4)
    put(g, owner, 0, (frm - 1) % TILE_COUNT)
    p = pcol(g, owner)
    g.turn = p.id
    pv = g.preview_move(p.pieces[0], 1)
    after = NEXT_SAME.get(to, to)
    check("flight" in pv["events"], "格 %d 触发飞行 -> 格 %d" % (frm, to))
    check(pv["to"] == after,
          "飞行落点 %d 后继续同色跳跃 -> %d (实际 %s)" % (to, after, pv.get("to")))

# 飞行起点格: 落在上面 -> 先飞向后, 再继续同色跳跃 (规则 5b 前半句)
for frm, to in sorted(FLIGHT_TO.items()):
    owner = FLIGHT_OWNER[frm]
    g = mk(4)
    put(g, owner, 0, (frm - 1) % TILE_COUNT)
    p = pcol(g, owner)
    g.turn = p.id
    pv = g.preview_move(p.pieces[0], 1)
    check("flight" in pv["events"] and "jump" in pv["events"],
          "落在飞行起点格 %d -> 先飞向 %d 再继续同色跳跃 (to=%s)" % (frm, to, pv.get("to")))

jt = [(s, d) for s, d in NEXT_SAME.items() if d in FLIGHT_TO]
if jt:
    src, flightstart = sorted(jt)[0]
    owner = FLIGHT_OWNER[flightstart]
    g = mk(4)
    put(g, owner, 0, (src - 1) % TILE_COUNT)
    p = pcol(g, owner)
    g.turn = p.id
    pv = g.preview_move(p.pieces[0], 1)
    check("jump" in pv["events"] and "flight" in pv["events"],
          "跳跃终点为飞行起点 (格 %d -> %d) 时继续飞行" % (src, flightstart))
else:
    check(False, "数据中找不到 '跳跃终点为飞行起点' 的链路")

# ───────────────────────── 规则 6a ─────────────────────────
section("规则 6a: 路过敌方不撞回, 落到同一格才撞回")
W = 1   # 白格, 两侧 0 与 2 分别为白格与黄格
g = mk(4)
put(g, 0, 0, (W - 1) % TILE_COUNT)
put(g, 1, 0, W)
red = pcol(g, 0)
g.turn = red.id
pv = g.preview_move(red.pieces[0], 2)
check(pv.get("ok") and pv["to"] == (W + 1) % TILE_COUNT,
      "走 2 点落到格 %d (实际 %s)" % ((W + 1), pv.get("to")))
check(pv["captured"] == [], "路过敌方 (格 %d) 不撞回" % W)

g = mk(4)
put(g, 0, 0, (W - 1) % TILE_COUNT)
put(g, 1, 0, W)
red = pcol(g, 0)
g.turn = red.id
pv = g.preview_move(red.pieces[0], 1)
check(pv.get("ok") and pv["to"] == W and pv["captured"] == [pcol(g, 1).pieces[0].id],
      "走到同一格撞回敌方")

# ───────────────────────── 规则 6b ─────────────────────────
section("规则 6b: 飞行途中经过敌方棋子可以撞回")
for frm, to in sorted(FLIGHT_TO.items()):
    mids = FLIGHT_MID.get(frm, [])
    if not mids:
        check(False, "飞行格 %d 缺少 mid 数据" % frm)
        continue
    owner = FLIGHT_OWNER[frm]
    g = mk(4)
    put(g, owner, 0, (frm - 1) % TILE_COUNT)
    victim = put(g, (owner + 1) % 4, 0, mids[0])
    p = pcol(g, owner)
    g.turn = p.id
    pv = g.preview_move(p.pieces[0], 1)
    check(victim.id in pv["captured"],
          "飞行途中经过格 %d 的敌子被撞回 [captured=%s]" % (mids[0], pv["captured"]))

# ───────────────────────── 规则 6c ─────────────────────────
section("规则 6c: 跳跃途中经过敌方棋子不能撞回")
found = False
for c in range(4):
    for src in [i for i in range(TILE_COUNT)
                if TILE_COLOR_IDX[i] == c and i in NEXT_SAME]:
        dst = NEXT_SAME[src]
        if (dst - src) % TILE_COUNT < 2:
            continue
        between = (src + 1) % TILE_COUNT
        g = mk(4)
        put(g, c, 0, (src - 1) % TILE_COUNT)
        victim = put(g, (c + 1) % 4, 0, between)
        p = pcol(g, c)
        g.turn = p.id
        pv = g.preview_move(p.pieces[0], 1)
        check(victim.id not in pv["captured"],
              "跳跃途中 (格 %d) 的敌子不被撞回" % between)
        found = True
        break
    if found:
        break
if not found:
    check(False, "找不到跨度 >= 2 的跳跃链路")

# ───────────────────────── 规则 7 / 6d ─────────────────────────
section("规则 7/6d: 叠子共同行动, 撞与被撞视作一颗")
g = mk(4)
put(g, 0, 0, 0)
put(g, 0, 1, 1)     # 目的地已有己方棋子
red = pcol(g, 0)
g.turn = red.id
pv = g.preview_move(red.pieces[0], 1)
check("stack" in pv["events"], "落到己方棋子所在格 -> 叠子")
check(pv["stackSize"] == 1, "出发时叠子数 = 1 (实际 %s)" % pv["stackSize"])

g = mk(4)
put(g, 0, 0, 0)
put(g, 0, 1, 0)     # 两枚同格
red = pcol(g, 0)
g.turn = red.id
g.pending_roll = 1
g.move(red.pieces[0].id)
check(all(p.pos == 1 for p in red.pieces[:2]),
      "叠子共同行动: 移动一枚, 两枚一起到格 1")
check(red.pieces[0].pos == red.pieces[1].pos,
      "两枚棋子位置一致 (共同行动)")

g = mk(4)
put(g, 0, 0, 0)
put(g, 1, 0, 1)
put(g, 1, 1, 1)
red = pcol(g, 0)
g.turn = red.id
g.pending_roll = 1
g.move(red.pieces[0].id)
check(all(p.waiting for p in pcol(g, 1).pieces[:2]),
      "叠子被撞: 敌方两枚整叠被逐回基地")

g = mk(4)
put(g, 0, 0, 0)
put(g, 0, 1, 0)
put(g, 1, 0, 1)
red = pcol(g, 0)
g.turn = red.id
g.pending_roll = 1
g.move(red.pieces[0].id)
check(pcol(g, 1).pieces[0].waiting,
      "叠子撞击: 视为一颗棋子, 敌方一枚被逐回基地")

# ───────────────────────── 规则 4 ─────────────────────────
section("规则 4: 必须精确抵达终点, 超出点数回退")
path = HOME_PATHS[0]
last = len(path) - 1
g = mk(4)
put(g, 0, 0, path[last - 1])
red = pcol(g, 0)
g.turn = red.id
pv = g.preview_move(red.pieces[0], 1)
check(pv.get("ok") and pv["arrivesFinish"] is True,
      "点数刚好到达终点 -> 抵达 (to=%s)" % pv.get("to"))

g = mk(4)
put(g, 0, 0, path[last - 2])
red = pcol(g, 0)
g.turn = red.id
pv = g.preview_move(red.pieces[0], 4)
check("bounce" in pv["events"] and pv["to"] == path[last - 2],
      "点数 4 超出终点 2 格 -> 回退到格 %d (实际 %s)" % (path[last - 2], pv.get("to")))

# ───────────────────────── 规则 8 ─────────────────────────
section("规则 8: 掷 6 奖励额外一次掷骰 (最多 3 次, 先移动再掷骰)")
g = mk(4)
put(g, 0, 0, 0)
red = pcol(g, 0)
g.turn = red.id
for i in range(1, 4):
    g.pending_roll = 6
    turn_before = g.turn
    g.move(red.pieces[0].id)
    check(g.six_bonus_rolls == i and g.turn == turn_before,
          "第 %d 次掷 6: 奖励额度=%d, 仍由当前玩家行动" % (i, g.six_bonus_rolls))
g.pending_roll = 6
turn_before = g.turn
g.move(red.pieces[0].id)
check(g.turn != turn_before,
      "第 4 次掷 6: 奖励封顶 3 次, 回合交给下一家")

g = mk(4)
put(g, 0, 0, 0)
red = pcol(g, 0)
g.turn = red.id
g.pending_roll = 6
g.move(red.pieces[0].id)
check(g.pending_roll is None and g.turn == red.id,
      "掷 6 后: 先完成移动, 再进行下一次掷骰")

# ───────────────────────── 规则 10 ─────────────────────────
section("规则 10: 一方 4 枚棋子全部抵达终点即结束")
g = mk(4)
for j in range(3):
    put(g, 0, j, FINISHED_POS[0], finished=True)
put(g, 0, 3, HOME_PATHS[0][len(HOME_PATHS[0]) - 2])
red = pcol(g, 0)
g.turn = red.id
g.pending_roll = 1
st = g.move(red.pieces[3].id)
check(st["finished"] is True and st["winner"] == red.id,
      "第 4 枚抵达终点 -> 对局结束, 赢家=红色玩家")
check(all(p.finished for p in red.pieces), "赢家 4 枚棋子全部抵达")

# ───────────────────────── 汇总 ─────────────────────────
print("")
print("=" * 56)
print("通过 %d 项, 失败 %d 项" % (PASSED, len(FAILS)))
if FAILS:
    print("")
    for f in FAILS:
        print("  FAILED: " + f)
    sys.exit(1)
print("全部规则测试通过")
