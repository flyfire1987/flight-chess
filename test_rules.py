"""飞行棋 · 官方 10 条规则逐条专项测试。"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import (FlightChessGame, GameError, NEXT_SAME, FLIGHT_TO, FLIGHT_MID,
                    START_CELLS, HOME_ENTRANCES, HOME_PATHS, FINISHED_POS,
                    TILE_COUNT, TILE_COLOR_IDX)

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  FAIL  %s  %s" % (name, detail))


def mk(n=4, seed=7):
    specs = [{"name": "P%d" % i, "is_ai": True} for i in range(n)]
    return FlightChessGame(specs, seed=seed)


def owner_of(g, color_index):
    return next(i for i, pl in enumerate(g.players) if pl.color_index == color_index)


def sec(t):
    print("")
    print("=== %s ===" % t)


sec("规则1 比赛人数 2~4 人")
for n in (2, 3, 4):
    g = mk(n)
    check("规则1 %d 人可开局" % n, len(g.players) == n)
try:
    mk(1)
    check("规则1 少于 2 人被拒", False)
except GameError:
    check("规则1 少于 2 人被拒", True)
try:
    mk(5)
    check("规则1 多于 4 人被拒", False)
except GameError:
    check("规则1 多于 4 人被拒", True)

sec("规则2 行棋与盖轮")
g = mk(4)
pv = g.preview_move(g.players[0].pieces[0], 3)
check("规则2 基地棋子掷 3 点不能起飞",
      pv.get("ok") is False and pv.get("reason") == "need_takeoff")
r = owner_of(g, 0)
pv = g.preview_move(g.players[r].pieces[0], 5)
check("规则2 基地棋子掷 5 点可起飞", pv.get("ok") and pv["to"] == START_CELLS[0], str(pv))
pv = g.preview_move(g.players[r].pieces[0], 6)
check("规则2 基地棋子掷 6 点可起飞", pv.get("ok") and pv["to"] == START_CELLS[0])
g.players[r].pieces[0].pos = 10
g.players[r].pieces[1].pos = 20
mv = g._movable_pieces(g.players[r], 3)
check("规则2 已起飞的多枚棋子都可选", len(mv) == 2, "got %d" % len(mv))
g2 = mk(4)
r2 = owner_of(g2, 0)
mv2 = g2._movable_pieces(g2.players[r2], 3)
check("规则2 无可用棋子 -> 盖轮 (无可动列表)", len(mv2) == 0)

sec("规则3 起飞规则 5/6")
for c in range(4):
    g3 = mk(4)
    ow = owner_of(g3, c)
    pv = g3.preview_move(g3.players[ow].pieces[0], 5)
    check("规则3 色%d 起飞落到本家起飞点 %d" % (c, START_CELLS[c]),
          pv["to"] == START_CELLS[c], "got %s" % pv.get("to"))

sec("规则4 终点回退")
g4 = mk(4)
r4 = owner_of(g4, 0)
path = HOME_PATHS[0]
last = len(path) - 1
g4.players[r4].pieces[0].pos = path[5]
pv = g4.preview_move(g4.players[r4].pieces[0], 1)
check("规则4 恰好到终点 -> 成功",
      pv["arrivesFinish"] is True and pv["to"] == FINISHED_POS[0], str(pv))
g4.players[r4].pieces[1].pos = path[5]
pv = g4.preview_move(g4.players[r4].pieces[1], 3)
expect = path[last - ((5 + 3) - last)]
check("规则4 超出 3 点 -> 回退到 path[%d]" % (expect - path[0]),
      pv["to"] == expect and "bounce" in pv["events"], str(pv))
g4.players[r4].pieces[2].pos = path[2]
pv = g4.preview_move(g4.players[r4].pieces[2], 2)
check("规则4 精确落在中途格", pv["to"] == path[4], str(pv))

sec("规则5a 同色跳跃")
g5 = mk(4)
r5 = owner_of(g5, 0)
red_cells = sorted(i for i in range(TILE_COUNT) if TILE_COLOR_IDX[i] == 0)
for i, cell in enumerate(red_cells):
    nxt = red_cells[(i + 1) % len(red_cells)]
    check("规则5a 红格 %d -> 下一同色格 %d" % (cell, nxt),
          NEXT_SAME[cell] == nxt, "got %s" % NEXT_SAME.get(cell))
g5.players[r5].pieces[0].pos = 30
pv = g5.preview_move(g5.players[r5].pieces[0], 1)
check("规则5a 落到红格 31 后跳到下一同色格 35",
      "jump" in pv["events"] and pv["to"] == NEXT_SAME[31], str(pv))
check("规则5a 只跳一次 (不连续跳)", pv["events"].count("jump") == 1, str(pv))

sec("规则5b 飞行起点与连跳")
g5b = mk(4)
r5b = owner_of(g5b, 0)
b5b = owner_of(g5b, 1)
check("规则5b 红色飞行起点 15 -> 47", FLIGHT_TO.get(15) == 47, str(FLIGHT_TO.get(15)))
check("规则5b 蓝色飞行起点 41 -> 21", FLIGHT_TO.get(41) == 21)
check("规则5b 黄色飞行起点 28 -> 8", FLIGHT_TO.get(28) == 8)
check("规则5b 绿色飞行起点 2 -> 34", FLIGHT_TO.get(2) == 34)
g5b.players[r5b].pieces[0].pos = 14
pv = g5b.preview_move(g5b.players[r5b].pieces[0], 1)
check("规则5b 落在飞行起点 15 触发飞行", "flight" in pv["events"], str(pv))
check("规则5b 飞落 47 后继续跳到 7",
      pv["to"] == NEXT_SAME[47], "got %s" % pv.get("to"))
g5b.players[b5b].pieces[0].pos = 37
pv = g5b.preview_move(g5b.players[b5b].pieces[0], 1)
check("规则5b 跳到飞行起点 41 后触发飞行",
      "flight" in pv["events"] and pv["to"] == NEXT_SAME[21], str(pv))

sec("规则6a 普通行棋撞子")
g6 = mk(4)
r6 = owner_of(g6, 0)
b6 = owner_of(g6, 1)
g6.players[r6].pieces[0].pos = 43
g6.players[b6].pieces[0].pos = 45
pv = g6.preview_move(g6.players[r6].pieces[0], 2)
check("规则6a 落到敌方所在格 -> 撞回",
      g6.players[b6].pieces[0].id in pv["captured"], str(pv))
g6.players[b6].pieces[0].pos = 44
pv = g6.preview_move(g6.players[r6].pieces[0], 2)
check("规则6a 路过敌方格子不撞",
      g6.players[b6].pieces[0].id not in pv["captured"], str(pv))

sec("规则6b 飞行途中撞子")
g6b = mk(4)
r6b = owner_of(g6b, 0)
b6b = owner_of(g6b, 1)
mid_red = FLIGHT_MID.get(15, [])
print("   红色飞行途中可撞格数: %d" % len(mid_red))
if mid_red:
    g6b.players[b6b].pieces[0].pos = mid_red[0]
    g6b.players[r6b].pieces[0].pos = 14
    pv = g6b.preview_move(g6b.players[r6b].pieces[0], 1)
    check("规则6b 飞行途中经过敌方 -> 撞回",
          g6b.players[b6b].pieces[0].id in pv["captured"], str(pv))
else:
    check("规则6b 存在飞行途中格数据", False, "FLIGHT_MID[15] 为空")

sec("规则6c 跳跃途中不撞")
g6c = mk(4)
r6c = owner_of(g6c, 0)
b6c = owner_of(g6c, 1)
g6c.players[r6c].pieces[0].pos = 30
g6c.players[b6c].pieces[0].pos = 33
pv = g6c.preview_move(g6c.players[r6c].pieces[0], 1)
check("规则6c 跳跃途中经过敌方不撞",
      g6c.players[b6c].pieces[0].id not in pv["captured"], str(pv))

sec("规则7 + 6d 叠子")
g7 = mk(4)
r7 = owner_of(g7, 0)
g7.players[r7].pieces[0].pos = 10
g7.players[r7].pieces[1].pos = 10
g7.players[r7].pieces[2].pos = -3
g7.turn = r7
g7.pending_roll = 2
mv7 = g7._movable_pieces(g7.players[r7], 2)
check("规则7 叠子可整体移动", len(mv7) == 2, "got %d" % len(mv7))
g7.move(g7.players[r7].pieces[0].id)
check("规则7 叠子整叠移动到同一格",
      g7.players[r7].pieces[0].pos == g7.players[r7].pieces[1].pos,
      "%s vs %s" % (g7.players[r7].pieces[0].pos, g7.players[r7].pieces[1].pos))
g7b = mk(4)
r7b = owner_of(g7b, 0)
b7b = owner_of(g7b, 1)
g7b.players[r7b].pieces[0].pos = 42
g7b.players[r7b].pieces[1].pos = 42
g7b.players[b7b].pieces[0].pos = 44
g7b.players[b7b].pieces[1].pos = 44
g7b.turn = r7b
g7b.pending_roll = 2
g7b.move(g7b.players[r7b].pieces[0].id)
check("规则6d 叠子撞敌方 -> 敌方整叠回基地",
      g7b.players[b7b].pieces[0].pos < 0 and g7b.players[b7b].pieces[1].pos < 0,
      "%s %s" % (g7b.players[b7b].pieces[0].pos, g7b.players[b7b].pieces[1].pos))

sec("规则8 掷6奖励")
g8 = mk(4)
r8 = owner_of(g8, 0)
g8.turn = r8
g8.players[r8].pieces[0].pos = 10
seq = [6, 6, 6, 4]
g8.rng.randint = lambda a, b: seq.pop(0) if seq else 4
g8.roll()
g8.move(g8.players[r8].pieces[0].id)
check("规则8 第 1 个 6 后仍在本方回合", g8.turn == r8, "turn=%s" % g8.turn)
check("规则8 连续 6 计数 = 1", g8.consecutive_sixes == 1, str(g8.consecutive_sixes))
g8.players[r8].pieces[1].pos = 10
g8.roll()
g8.move(g8.players[r8].pieces[1].id)
check("规则8 第 2 个 6 后仍在本方回合", g8.turn == r8, "turn=%s" % g8.turn)
check("规则8 连续 6 计数 = 2", g8.consecutive_sixes == 2, str(g8.consecutive_sixes))
g8.players[r8].pieces[2].pos = 10
g8.roll()
g8.move(g8.players[r8].pieces[2].id)
check("规则8 第 3 个 6 后不再奖励 (回合结束)",
      g8.turn != r8, "turn=%s" % g8.turn)

sec("规则9 先手与顺时针")
seen = set()
for seed in range(40):
    seen.add(mk(4, seed=seed).first_seat)
check("规则9 先手随机 (40 局覆盖 %d 个座位)" % len(seen), len(seen) > 1)
g9 = mk(4, seed=3)
check("规则9 行动顺序 = 先手起顺时针",
      g9.order == [(g9.first_seat + k) % 4 for k in range(4)], str(g9.order))
g9.turn = g9.order[-1]
g9._advance_turn()
check("规则9 _advance_turn 回到先手", g9.turn == g9.first_seat, str(g9.turn))
check("规则9 先手为红色",
      g9.players[g9.first_seat].color_index == 0,
      str(g9.players[g9.first_seat].color_index))
for i, pl in enumerate(g9.players):
    expect = (i - g9.first_seat) % 4
    check("规则9 座位%d 颜色索引 = %d" % (i, expect),
          pl.color_index == expect, str(pl.color_index))

sec("规则10 结束条件")
g10 = mk(4)
r10 = owner_of(g10, 0)
for pc in g10.players[r10].pieces:
    pc.pos = FINISHED_POS[0]
    pc.finished = True
g10.players[r10].pieces[3].pos = HOME_PATHS[0][5]
g10.players[r10].pieces[3].finished = False
g10.turn = r10
g10.pending_roll = 1
g10.move(g10.players[r10].pieces[3].id)
check("规则10 四枚全到终点 -> 对局结束",
      g10.finished and g10.winner == r10,
      "finished=%s winner=%s" % (g10.finished, g10.winner))

print("")
print("=" * 50)
print("PASS: %d   FAIL: %d" % (PASS, FAIL))
if FAILED:
    print("失败用例:")
    for f in FAILED:
        print("  -", f)
sys.exit(1 if FAIL else 0)
