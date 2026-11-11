# -*- coding: utf-8 -*-
"""
飞行棋 —— 官方 10 条规则自动化测试

覆盖:
  规则 1  比赛人数 2~4 人
  规则 2  可指定任一已起飞棋子; 5/6 点可起飞; 无子可动盖轮
  规则 3  5/6 点从基地起飞
  规则 4  必须精确抵达终点, 超出则回退
  规则 5a 落在同色格 -> 跳到前方下一个同色格
  规则 5b 落在飞行起点 -> 飞向对面飞行终点, 再继续同色跳跃
  规则 6a 普通移动路过敌方不撞, 落到同一格才撞
  规则 6b 飞行途中经过敌方棋子可以撞回
  规则 6c 跳跃途中经过敌方棋子不能撞回
  规则 6d 叠子参与撞与被撞时视为一颗棋子
  规则 7  己方棋子叠在一起共同行动
  规则 8  掷 6 奖励额外一次掷骰, 最多 3 次, 先行棋再掷骰
  规则 9  开局随机红色先手, 其余按座位顺时针
  规则 10 一方 4 枚棋子全部抵达终点即结束
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    tag = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  | " + str(extra)) if extra else ""))


class FakeRng(object):
    """可控随机源 (固定骰子序列), 用于规则 8 的连 6 测试。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def _next(self):
        v = self.seq[self.i % len(self.seq)]
        self.i += 1
        return v

    def randrange(self, n):
        return self._next() % n

    def randint(self, a, b):
        # seq 里直接存最终点数, 只做区间裁剪
        return max(a, min(b, self._next()))


def mk(n=4, seed=7):
    players = [{"name": "P%d" % i, "is_ai": False} for i in range(n)]
    return E.FlightChessGame(players, seed=seed)


def red_player(g):
    for p in g.players:
        if p.color_index == 0:
            return p
    return None


def set_at(g, owner, index, pos):
    for p in g.players:
        for pc in p.pieces:
            if pc.owner == owner and pc.index == index:
                pc.pos = pos
                pc.finished = False
                return pc
    return None


def find_jump(color):
    """找一个不会连锁飞行的同色跳跃 (from -> to), 便于测跳跃途中不撞。"""
    for x in range(E.TILE_COUNT):
        if E.TILE_COLOR_IDX[x] != color:
            continue
        if x == E.HOME_ENTRANCES[color]:
            continue
        if x in E.FLIGHT_TO:
            continue
        y = E.NEXT_SAME.get(x)
        if y is None or y in E.FLIGHT_TO:
            continue
        if E.TILE_COLOR_IDX[y] != color:
            continue
        if any(E.TILE_COLOR_IDX[k] == color for k in range(x + 1, y)):
            continue
        return x, y
    return None, None


def find_flight_with_mid():
    for s in E.FLIGHT_TO:
        mid = E.FLIGHT_MID.get(s) or []
        if mid:
            return s, list(mid)
    return None, []


# ───────────────────────── 规则 1 / 9 ─────────────────────────
def test_player_count_and_order():
    print("\n[规则 1/9] 人数 2~4 人, 随机红色先手, 顺时针行动")
    for n in (2, 3, 4):
        g = mk(n)
        check("规则1: %d 人可开局" % n, len(g.players) == n)
    for n in (1, 5):
        try:
            mk(n)
            check("规则1: %d 人被拒绝" % n, False)
        except E.GameError:
            check("规则1: %d 人被拒绝" % n, True)

    firsts = set()
    order_ok = True
    red_ok = True
    for seed in range(60):
        g = mk(4, seed=seed)
        firsts.add(g.first_seat)
        if g.order != [(g.first_seat + k) % 4 for k in range(4)]:
            order_ok = False
        if g.players[g.first_seat].color_index != 0:
            red_ok = False
        if g.turn != g.first_seat:
            order_ok = False
    check("规则9: 先手随机 (覆盖多个座位)", len(firsts) >= 2, sorted(firsts))
    check("规则9: 先手玩家拿红色 (color_index=0)", red_ok)
    check("规则9: 行动顺序=从先手起顺时针", order_ok)


# ───────────────────────── 规则 2 / 3 ─────────────────────────
def test_takeoff_and_pass():
    print("\n[规则 2/3] 起飞点数 5/6, 无子可动盖轮, 可指定任一已起飞棋子")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    pc = rp.pieces[0]

    pv = g.preview_move(pc, 4)
    check("规则3: 4 点不能起飞", pv.get("ok") is False and pv.get("reason") == "need_takeoff", pv.get("reason"))
    pv5 = g.preview_move(pc, 5)
    check("规则3: 5 点起飞到本家起点格", pv5.get("ok") and pv5["to"] == E.START_CELLS[0], pv5.get("to"))
    pv6 = g.preview_move(pc, 6)
    check("规则3: 6 点起飞到本家起点格", pv6.get("ok") and pv6["to"] == E.START_CELLS[0], pv6.get("to"))

    # 盖轮: 全员在基地 + 掷 4 点
    g2 = mk(4, seed=3)
    rp2 = red_player(g2)
    g2.turn = rp2.id
    for p in rp2.pieces:
        p.pos = -1 - p.index
    g2.rng = FakeRng([3])   # 3 点: 既不能起飞, 也无已起飞棋子
    g2.roll()
    check("规则2: 无子可动 -> 盖轮并跳过回合", g2.turn != rp2.id, "turn=%s" % g2.turn)

    # 可指定任一已起飞棋子
    g3 = mk(4, seed=3)
    rp3 = red_player(g3)
    g3.turn = rp3.id
    a = rp3.pieces[0]
    b = rp3.pieces[1]
    a.pos = 0
    b.pos = 10
    g3.pending_roll = 4
    mv = g3._movable_pieces(rp3, 4)
    check("规则2: 存在多枚可选棋子", len(mv) >= 2, [m.id for m in mv])
    g3.move(b.id)
    check("规则2: 可指定第二枚棋子移动", b.pos != 10 and a.pos == 0, "b=%s a=%s" % (b.pos, a.pos))


# ───────────────────────── 规则 4 ─────────────────────────
def test_finish_exact_and_bounce():
    print("\n[规则 4] 精确抵达终点, 超出回退")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    path = E.HOME_PATHS[0]
    pc = rp.pieces[0]

    pc.pos = path[5]
    pv = g.preview_move(pc, 1)
    check("规则4: 恰好 1 点抵达终点", pv["to"] == E.FINISHED_POS[0], pv["to"])

    pc.pos = path[5]
    pv = g.preview_move(pc, 3)
    check("规则4: 超出终点 2 点 -> 回退到 path[4]", pv["to"] == path[4], pv["to"])

    pc.pos = path[1]
    pv = g.preview_move(pc, 5)
    check("规则4: 终点道内 5 点抵达终点", pv["to"] == E.FINISHED_POS[0], pv["to"])

    pc.pos = E.HOME_ENTRANCES[0]
    pv = g.preview_move(pc, 6)
    check("规则4: 入口格掷 6 恰好进终点", pv["to"] == E.FINISHED_POS[0], pv["to"])

    pc.pos = E.HOME_ENTRANCES[0]
    pv = g.preview_move(pc, 5)
    check("规则4: 入口格掷 5 进入终点道第 5 格", pv["to"] == path[5], pv["to"])


# ───────────────────────── 规则 5a/5b ─────────────────────────
def test_jump_and_flight():
    print("\n[规则 5] 同色跳跃 / 飞行连锁")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    x, y = find_jump(0)
    check("规则5a: 存在同色跳跃数据 (nextSame)", x is not None, (x, y))
    if x is not None:
        pc = rp.pieces[0]
        pc.pos = x - 1
        pv = g.preview_move(pc, 1)
        check("规则5a: 落在同色格 -> 跳到前方下一个同色格", pv["to"] == y, "%s -> %s (期望 %s)" % (x, pv["to"], y))

    src = 15
    if src in E.FLIGHT_TO:
        pc = rp.pieces[0]
        pc.pos = src - 1
        pv = g.preview_move(pc, 1)
        check("规则5b: 落在飞行起点 -> 飞向对面飞行终点并连锁跳跃",
              "flight" in pv.get("events", []), pv.get("events"))


# ───────────────────────── 规则 6 ─────────────────────────
def test_capture_rules():
    print("\n[规则 6] 撞子: 路过不撞 / 同格撞 / 飞行途中撞 / 跳跃途中不撞")
    # 6a 路过不撞 (普通移动)
    g = mk(4, seed=3)
    rp = red_player(g)
    enemy = g.players[(rp.id + 1) % 4]
    g.turn = rp.id
    ep = set_at(g, enemy.id, 0, 2)
    pc = rp.pieces[0]
    pc.pos = 0
    pv = g.preview_move(pc, 4)
    check("规则6a: 普通移动路过敌方棋子不撞", pv.get("ok") and not pv.get("captured"), pv.get("captured"))
    g.pending_roll = 4
    g.move(pc.id)
    check("规则6a: 路过敌方后其仍在原格", ep.pos == 2, ep.pos)

    # 6a 落到同一格撞回
    g = mk(4, seed=3)
    rp = red_player(g)
    enemy = g.players[(rp.id + 1) % 4]
    g.turn = rp.id
    ep = set_at(g, enemy.id, 0, 4)
    pc = rp.pieces[0]
    pc.pos = 0
    pv = g.preview_move(pc, 4)
    check("规则6a: 落到同一格撞回", ep.id in (pv.get("captured") or []), pv.get("captured"))
    g.pending_roll = 4
    g.move(pc.id)
    check("规则6a: 被撞棋子逐回基地", ep.pos < 0, ep.pos)

    # 6c 跳跃途中不撞
    x, y = find_jump(0)
    if x is not None:
        g = mk(4, seed=3)
        rp = red_player(g)
        enemy = g.players[(rp.id + 1) % 4]
        g.turn = rp.id
        mid = x + 1
        ep = set_at(g, enemy.id, 0, mid)
        pc = rp.pieces[0]
        pc.pos = x - 1
        pv = g.preview_move(pc, 1)
        check("规则6c: 跳跃途中经过敌方棋子不撞", not pv.get("captured"), pv.get("captured"))
        g.pending_roll = 1
        g.move(pc.id)
        check("规则6c: 跳跃后敌方仍在原格", ep.pos == mid, ep.pos)

    # 6b 飞行途中撞
    src, mid = find_flight_with_mid()
    check("规则6b: 飞行路径带 mid 数据 (途中格)", src is not None, (src, mid))
    if src is not None:
        g = mk(4, seed=3)
        rp = red_player(g)
        enemy = g.players[(rp.id + 1) % 4]
        g.turn = rp.id
        ep = set_at(g, enemy.id, 0, mid[0])
        pc = rp.pieces[0]
        pc.pos = src - 1
        pv = g.preview_move(pc, 1)
        check("规则6b: 飞行途中经过敌方棋子可以撞回", ep.id in (pv.get("captured") or []),
              pv.get("captured"))
        g.pending_roll = 1
        g.move(pc.id)
        check("规则6b: 飞行途中被撞棋子回基地", ep.pos < 0, ep.pos)


# ───────────────────────── 规则 6d / 7 ─────────────────────────
def test_stacking():
    print("\n[规则 6d/7] 叠子共同行动, 撞与被撞按一颗棋子")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    p1 = rp.pieces[0]
    p2 = rp.pieces[1]
    p1.pos = 0
    p2.pos = 0
    g.pending_roll = 4
    g.move(p1.id)
    check("规则7: 己方棋子叠在一起共同行动", p1.pos == p2.pos and p1.pos == 4, (p1.pos, p2.pos))

    g = mk(4, seed=3)
    rp = red_player(g)
    enemy = g.players[(rp.id + 1) % 4]
    g.turn = rp.id
    e1 = set_at(g, enemy.id, 0, 4)
    e2 = set_at(g, enemy.id, 1, 4)
    pc = rp.pieces[0]
    pc.pos = 0
    g.pending_roll = 4
    g.move(pc.id)
    check("规则6d: 被撞的叠子整叠逐回基地", e1.pos < 0 and e2.pos < 0, (e1.pos, e2.pos))

    g = mk(4, seed=3)
    rp = red_player(g)
    enemy = g.players[(rp.id + 1) % 4]
    g.turn = rp.id
    e1 = set_at(g, enemy.id, 0, 4)
    e2 = set_at(g, enemy.id, 1, 4)
    p1 = rp.pieces[0]
    p2 = rp.pieces[1]
    p1.pos = 0
    p2.pos = 0
    g.pending_roll = 4
    g.move(p1.id)
    check("规则6d: 叠子撞机时按一颗棋子处理 (敌方整叠回基地)", e1.pos < 0 and e2.pos < 0, (e1.pos, e2.pos))


# ───────────────────────── 规则 8 ─────────────────────────
def test_six_bonus():
    print("\n[规则 8] 掷 6 奖励额外一次掷骰, 最多 3 次, 先行棋再掷骰")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    for p in rp.pieces:
        p.pos = -1 - p.index
    g.rng = FakeRng([6, 6, 6, 6, 3])
    seq = []
    for _ in range(5):
        g.roll()
        # 掷 6 后基地棋子均可起飞 -> 引擎会等待选子, 这里自动选第一枚完成移动
        if g.pending_roll is not None:
            movable = g._movable_pieces(rp, g.pending_roll)
            if movable:
                g.move(movable[0].id)
        seq.append((g.turn, g.six_bonus_rolls, rp.pieces[0].pos))
    check("规则8: 第 1 个 6 后仍轮到自己 (奖励再掷)", seq[0][0] == rp.id, seq[0])
    check("规则8: 第 2 个 6 后仍轮到自己 (奖励再掷)", seq[1][0] == rp.id, seq[1])
    check("规则8: 第 3 个 6 后仍轮到自己 (第 3 次奖励)", seq[2][0] == rp.id, seq[2])
    check("规则8: 第 4 个 6 后回合结束 (奖励最多 3 次)", seq[3][0] != rp.id, seq[3])
    check("规则8: 奖励时先完成移动再掷骰 (棋子已离基地)", seq[0][2] >= 0, seq[0])
    check("规则8: 奖励次数上限 = 3", seq[3][1] == 0 and g.max_bonus == 3,
          (seq[3][1], g.max_bonus))


# ───────────────────────── 规则 10 ─────────────────────────
def test_game_end():
    print("\n[规则 10] 一方 4 枚棋子全部抵达终点即结束")
    g = mk(4, seed=3)
    rp = red_player(g)
    g.turn = rp.id
    path = E.HOME_PATHS[0]
    for i in range(3):
        p = rp.pieces[i]
        p.finished = True
        p.pos = E.FINISHED_POS[0]
    last = rp.pieces[3]
    last.finished = False
    last.pos = path[5]
    g.pending_roll = 1
    g.move(last.id)
    check("规则10: 4 枚全到终点 -> 本局结束", g.finished and g.winner == rp.id, (g.finished, g.winner))


def main():
    print("=" * 66)
    print("飞行棋 官方 10 条规则 —— 自动化测试")
    print("=" * 66)
    test_player_count_and_order()
    test_takeoff_and_pass()
    test_finish_exact_and_bounce()
    test_jump_and_flight()
    test_capture_rules()
    test_stacking()
    test_six_bonus()
    test_game_end()
    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print("  - " + f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
