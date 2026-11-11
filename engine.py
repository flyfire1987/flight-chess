"""
🎲 飞行棋 (Flight Chess) — 核心规则引擎

纯逻辑模块, 无 Web 框架依赖, 可独立测试。

按官方规则实现 (10 条):
  1. 2~4 人; 系统随机选一位玩家先手, 其余按座位顺时针行动
  2. 可指定任一已起飞棋子前进; 5/6 点可起飞基地棋子; 无子可动则盖轮
  3. 5/6 点从基地起飞进入本家起飞点 (停机坪)
  4. 必须以精确点数抵达终点, 超出则回退 (反弹)
  5a. 落在与棋子同色的格子 -> 跳到前方下一个同色格 (数据驱动 nextSame)
  5b. 落在飞行起点格 -> 按红色箭头飞向对面飞行终点, 再继续同色跳跃
  6a. 普通行棋路过敌方棋子不撞回, 落到同一格才撞回
  6b. 飞行途中经过敌方棋子可以撞回
  6c. 跳跃途中经过敌方棋子不能撞回
  6d. 叠子参与撞与被撞时视为一颗棋子
  7. 己方棋子落在同一格则叠在一起共同行动
  8. 掷 6 奖励一次额外掷骰 (最多 3 连 6), 先行棋再掷骰
  9. 开局随机红色先手, 其余顺时针
 10. 一方 4 枚棋子全部抵达终点即结束
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BOARD_PATH = os.path.join(DATA_DIR, "board.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


BOARD = _load(BOARD_PATH)
CONFIG = _load(CONFIG_PATH)

META = BOARD["meta"]
TILES = BOARD["tiles"]
TILE_COUNT = len(TILES)
HOME_ENTRANCES = BOARD["homeEntrances"]
HOME_PATHS = BOARD["homePaths"]
START_CELLS = BOARD["starts"]          # 各色起飞点格 (红15/蓝41/黄28/绿2)
HOME_STRETCH_FIRST = min(p[1] for p in HOME_PATHS)
FINISHED_POS = {o: p[-1] for o, p in enumerate(HOME_PATHS)}  # 各色终点位置

# 棋盘格颜色索引 (0=红 1=蓝 2=黄 3=绿)
COLOR_NAMES = ["red", "blue", "yellow", "green"]
TILE_COLOR_IDX = [t.get("colorIndex", t["id"] % 4) for t in TILES]

# ── 规则 5a: 前方下一个同色格 (数据驱动, 固定步数无法表达) ──
NEXT_SAME: Dict[int, int] = {
    int(e["from"]): int(e["to"]) for e in BOARD.get("nextSame", [])
}
# ── 规则 5b: 飞行起点 -> 对面飞行终点 ──
FLIGHT_TO: Dict[int, int] = {
    int(e["from"]): int(e["to"]) for e in BOARD.get("flightMap", [])
}
# 飞行途中经过的格子 (用于规则 6b: 飞行途中可撞回)
FLIGHT_MID: Dict[int, List[int]] = {
    int(k): [int(m["pos"]) for m in v.get("mid", [])]
    for k, v in BOARD.get("flightPaths", {}).items()
}
JUMP_CFG = BOARD.get("jumpRule", {})
MAX_CHAIN = int(JUMP_CFG.get("maxChain", 8))


GAME_CFG = CONFIG["game"]
BONUS_CFG = CONFIG["bonus"]

# 起飞所需点数 (规则 3: 5 或 6)
TAKEOFF_ROLLS = set(GAME_CFG.get("takeoffRolls", [5, 6]))

LANE_COUNT = len(HOME_PATHS)
PIECES_PER_PLAYER = GAME_CFG["piecesPerPlayer"]
DICE_SIDES = GAME_CFG["diceSides"]
MAX_SIXES = GAME_CFG["maxConsecutiveSixes"]
# 规则 8: 掷 6 奖励多一次掷骰, 最多 3 次 (独立于"连续几个 6"的处罚阈值)
MAX_BONUS_ROLLS = int(GAME_CFG.get("maxBonusRolls", MAX_SIXES))
RANDOM_FIRST = bool(GAME_CFG.get("randomFirstPlayer", True))
STACKING = bool(GAME_CFG.get("stacking", True))


class GameError(Exception):
    """规则错误 (非法操作), 由 API 层转成 400。"""


@dataclass
class Piece:
    """单枚棋子。"""
    id: int
    owner: int
    index: int
    pos: int
    finished: bool = False
    color: int = 0                 # 所属颜色索引 (0红 1蓝 2黄 3绿)

    @property
    def waiting(self) -> bool:
        """是否还在基地待起飞。"""
        return self.pos < 0

    @property
    def in_home_stretch(self) -> bool:
        """是否已进入本色终点跑道 (含最后一格)。"""
        path = HOME_PATHS[self.color]
        return path[1] <= self.pos <= path[-1]


@dataclass
class Player:
    id: int
    name: str
    token: str
    color: str
    color_index: int = 0
    pieces: List[Piece] = field(default_factory=list)
    points: int = 0
    finished_pieces: int = 0
    is_ai: bool = False
    seat: int = 0


@dataclass
class LogEntry:
    turn: int
    player: int
    text: str
    kind: str = "info"
    payload: Dict[str, Any] = field(default_factory=dict)


class FlightChessGame:
    """单局飞行棋。对局状态全部在内存, 进程重启即清空。"""

    def __init__(self, players: List[Dict[str, Any]], seed: Optional[int] = None):
        if len(players) < 2:
            raise GameError("至少需要 2 名玩家")
        if len(players) > LANE_COUNT:
            raise GameError(f"最多 {LANE_COUNT} 名玩家")
        self.rng = random.Random(seed if seed is not None else random.randint(0, 10**9))
        n = len(players)

        # ── 规则 9: 随机选一位玩家作为先手, 其余按位置顺时针 ──
        self.first_seat = self.rng.randrange(n) if RANDOM_FIRST else 0
        # 行动顺序: 从先手开始按座位顺时针
        self.order: List[int] = [(self.first_seat + k) % n for k in range(n)]

        self.players: List[Player] = []
        self.turn = self.first_seat
        self.turn_count = 0
        self.log: List[LogEntry] = []
        self.finished = False
        self.winner: Optional[int] = None
        self.consecutive_sixes = 0
        self.six_bonus_rolls = 0   # 本轮已发放的 6 奖励次数 (规则 8, 上限 3)
        self.max_bonus = MAX_BONUS_ROLLS
        self.pending_roll: Optional[int] = None
        self.pending_piece: Optional[int] = None
        self.last_roll: Optional[int] = None

        piece_id = 0
        for i, spec in enumerate(players):
            # 规则 9: 随机选出的先手即为红色, 其余按座位顺时针依次分配蓝/黄/绿
            color_index = (i - self.first_seat) % n
            p = Player(
                id=i,
                name=spec.get("name") or META["defaultNames"][i % len(META["defaultNames"])],
                token=META["tokens"][color_index % len(META["tokens"])],
                color=META["colors"][color_index % len(META["colors"])],
                color_index=color_index,
                is_ai=bool(spec.get("is_ai", i > 0)),
                seat=int(spec.get("seat", i)),
            )
            for j in range(PIECES_PER_PLAYER):
                p.pieces.append(Piece(id=piece_id, owner=i, index=j,
                                      pos=-1 - j, color=color_index))
                piece_id += 1
            self.players.append(p)

        self._add_log(self.first_seat,
                      f"本局由 {self.players[self.first_seat].name} 先手 (红色先手), "
                      f"其余按座位顺时针行动", "info")

    # ─────────────── 查询辅助 ───────────────

    def current_player(self) -> Player:
        return self.players[self.turn]

    def piece_by_id(self, piece_id: int) -> Optional[Piece]:
        for p in self.players:
            for pc in p.pieces:
                if pc.id == piece_id:
                    return pc
        return None

    def _pieces_at(self, pos: int, exclude: Optional[int] = None) -> List[Piece]:
        out = []
        for p in self.players:
            for pc in p.pieces:
                if pc.finished:
                    continue
                if pc.pos == pos and pc.id != exclude:
                    out.append(pc)
        return out

    def _owner_at(self, pos: int) -> Optional[int]:
        hit = self._pieces_at(pos)
        return hit[0].owner if hit else None

    def _home_path(self, color: int) -> List[int]:
        return HOME_PATHS[color]

    def _home_index(self, color: int, pos: int) -> int:
        path = self._home_path(color)
        try:
            return path.index(pos)
        except ValueError:
            return -1

    def _stack_at(self, owner: int, pos: int) -> List[Piece]:
        """同一方在 pos 处的全部棋子 (叠子)。"""
        return [pc for pc in self.players[owner].pieces
                if not pc.finished and pc.pos == pos]

    # ─────────────── 走子计算 (纯函数, 不改状态) ───────────────

    def preview_move(self, piece: Piece, roll: int) -> Dict[str, Any]:
        """计算某枚棋子走 roll 步后的落点与效果 (不改状态, 供前端预览)。"""
        if piece.finished:
            raise GameError("该棋子已抵达终点")

        events: List[str] = []
        captured: List[int] = []
        color = piece.color

        # 1) 基地待起飞: 规则 3 —— 掷出 5 或 6 才能起飞
        if piece.waiting:
            if roll not in TAKEOFF_ROLLS:
                return {
                    "ok": False,
                    "reason": "need_takeoff",
                    "msg": "需掷出 " + "/".join(str(x) for x in sorted(TAKEOFF_ROLLS)) + " 才能起飞",
                    "from": piece.pos,
                    "to": piece.pos,
                }
            events.append("takeoff")
            final_pos = START_CELLS[color]
        elif piece.in_home_stretch:
            # 2a) 已在终点跑道内 (规则 4: 精确抵达, 超出回退)
            final_pos = self._advance_home_stretch(piece, roll, events)
        else:
            # 2b) 在公共跑道内
            final_pos = self._advance_ring(piece, roll, events, captured)

        if final_pos is None:
            return {"ok": False, "reason": "invalid", "msg": "无法移动",
                    "from": piece.pos, "to": piece.pos}

        # 3) 落点撞机 (规则 6a: 必须走到同一格才能撞回; 终点跑道内不撞)
        path = self._home_path(color)
        if not (path[1] <= final_pos <= path[-1]):
            for other in self._pieces_at(final_pos, exclude=piece.id):
                if other.owner != piece.owner:
                    captured.append(other.id)
                    events.append("capture")

        # 规则 7: 落到己方棋子所在格 -> 叠子
        if STACKING:
            own = [o for o in self._pieces_at(final_pos, exclude=piece.id)
                   if o.owner == piece.owner]
            if own:
                events.append("stack")

        group = self._stack_at(piece.owner, piece.pos) if STACKING else [piece]
        return {
            "ok": True,
            "from": piece.pos,
            "to": final_pos,
            "homeStep": (final_pos - path[1] + 1) if path[1] <= final_pos <= path[-1] else 0,
            "events": events,
            "captured": captured,
            "arrivesFinish": final_pos == FINISHED_POS[color],
            "stackSize": len(group),
        }

    def _advance_home_stretch(self, piece: Piece, roll: int,
                              events: List[str]) -> int:
        """
        在终点跑道内前进 (规则 4)。
        path[0] = 入口格, path[1..6] = 跑道 6 格, path[6] = 终点。
        需精确走到最后一格才算成功; 超出则按多余点数回退。
        """
        color = piece.color
        path = self._home_path(color)
        idx = self._home_index(color, piece.pos)
        if idx < 0:
            idx = 0
        last_cell = len(path) - 1
        new_idx = idx + roll
        if new_idx > last_cell:
            if GAME_CFG.get("bounceOnOvershoot", True):
                over = new_idx - last_cell
                new_idx = last_cell - over
                if new_idx < 1:
                    new_idx = 1
                events.append("bounce")
                return path[new_idx]
            new_idx = last_cell
        if new_idx == last_cell:
            return FINISHED_POS[color]
        return path[new_idx]

    def _enter_home(self, color: int, rem: int, events: List[str]) -> int:
        """从入口格进入本色终点跑道 (规则 4: 精确抵达, 超出回退)。"""
        path = self._home_path(color)
        entrance = HOME_ENTRANCES[color]
        if rem <= 0:
            return entrance
        last = len(path) - 1
        if rem == last:
            return FINISHED_POS[color]
        if rem > last:
            over = rem - last
            idx = last - over
            if idx < 1:
                idx = 1
            events.append("bounce")
            return path[idx]
        return path[rem]

    def _resolve_chain(self, cur: int, piece: Piece,
                       events: List[str], captured: List[int]) -> int:
        """
        规则 5: 同色跳跃 / 飞行连锁。
          规则 5a: 落在同色格 -> 跳到前方下一个同色格 (只跳一次)
          规则 5b: 落在飞行起点格 -> 按红色箭头飞向对面飞行终点, 再继续向前一个同色格作跳跃
                   跳跃的终点是飞行起点格 -> 同样飞往对面飞行终点
        规则 6b: 飞行途中经过敌方棋子可以撞回
        规则 6c: 跳跃途中经过敌方棋子不能撞回
        """
        owner = piece.owner
        color = piece.color
        entrance = HOME_ENTRANCES[color]

        def do_flight(src: int) -> int:
            dest = FLIGHT_TO[src]
            events.append("flight")
            # 规则 6b: 飞行途中经过敌方棋子可以撞回
            for mid in FLIGHT_MID.get(src, []):
                for other in self._pieces_at(mid, exclude=piece.id):
                    if other.owner != owner:
                        captured.append(other.id)
                        events.append("capture")
            return dest

        # 初始落点: 是飞行起点则先飞, 否则直接跳一格 (规则 5a 只跳一次)
        if cur in FLIGHT_TO:
            cur = do_flight(cur)
            if cur == entrance:
                return cur
            nxt = NEXT_SAME.get(cur)
            if nxt is None:
                return cur
            events.append("jump")
            cur = nxt
        else:
            nxt = NEXT_SAME.get(cur)
            if nxt is None:
                return cur
            events.append("jump")
            cur = nxt

        # 连锁: 跳跃的终点是飞行起点 -> 再飞一次, 飞完继续向前一个同色格作跳跃
        seen = set()
        while cur != entrance and cur in FLIGHT_TO:
            if cur in seen:
                break
            seen.add(cur)
            cur = do_flight(cur)
            if cur == entrance:
                return cur
            nxt = NEXT_SAME.get(cur)
            if nxt is None:
                return cur
            events.append("jump")
            cur = nxt

        return cur

    def _advance_ring(self, piece: Piece, roll: int,
                      events: List[str], captured: List[int]) -> Optional[int]:
        """
        在公共跑道 (0..51) 内前进, 处理跳跃 / 飞行 / 进入终点跑道。
        规则 6a: 普通行棋路过敌方棋子不撞, 只有落到同一格才撞。
        """
        color = piece.color
        entrance = HOME_ENTRANCES[color]

        cur = piece.pos
        remaining = roll

        # 规则 4: 已停在终点入口格 -> 本回合直接进入终点跑道
        if cur == entrance:
            return self._enter_home(color, remaining, events)

        while remaining > 0:
            nxt = cur + 1
            if nxt >= TILE_COUNT:
                nxt = 0
            cur = nxt
            remaining -= 1

            # 终点入口优先进入终点跑道
            if cur == entrance:
                return self._enter_home(color, remaining, events)

            # 落在同色格 -> 跳跃 / 飞行连锁
            if TILE_COLOR_IDX[cur] == color:
                cur = self._resolve_chain(cur, piece, events, captured)
                if cur == entrance:
                    return self._enter_home(color, remaining, events)
                if cur >= TILE_COUNT:
                    # 连锁已进入终点道
                    return cur

        return cur

    # ─────────────── 掷骰与行动 ───────────────

    def roll(self) -> Dict[str, Any]:
        """当前玩家掷骰。结果可能要求选子移动。"""
        if self.finished:
            raise GameError("对局已结束")
        if self.pending_roll is not None:
            raise GameError("请先完成上一步移动")

        player = self.current_player()
        value = self.rng.randint(1, DICE_SIDES)
        self.last_roll = value
        self.turn_count += 1
        self.log.append(LogEntry(self.turn_count, player.id,
                                 f"{player.name} 掷出 {value} 点", "roll", {"value": value}))

        if value == 6:
            self.consecutive_sixes += 1
            # 奖励额度已用完还掷出 6 -> 若开启处罚则本回合作废
            if (self.six_bonus_rolls >= MAX_BONUS_ROLLS
                    and GAME_CFG.get("threeSixesPenalty")):
                self.consecutive_sixes = 0
                self.six_bonus_rolls = 0
                self.pending_roll = None
                self._add_log(player.id, f"连续 {MAX_SIXES} 个 6，{player.name} 本回合作废并跳过", "penalty")
                self._advance_turn()
                return self.state()
        else:
            self.consecutive_sixes = 0
            self.six_bonus_rolls = 0

        self.pending_roll = value

        movable = self._movable_pieces(player, value)
        if not movable:
            # 规则 2: 无可用棋子且点数不能起飞 -> 盖轮 (跳过回合)
            self._add_log(player.id, f"{player.name} 无子可动，盖轮跳过", "info")
            self.pending_roll = None
            self._advance_turn()
            return self.state()
        if len(movable) == 1:
            return self.move(movable[0].id)

        self.pending_piece = None
        return {
            **self.state(),
            "needPieceChoice": True,
            "movablePieces": [p.id for p in movable],
        }

    def _movable_pieces(self, player: Player, roll: int) -> List[Piece]:
        out = []
        for pc in player.pieces:
            if pc.finished:
                continue
            try:
                pv = self.preview_move(pc, roll)
            except GameError:
                continue
            if pv.get("ok"):
                out.append(pc)
        return out

    def move(self, piece_id: int) -> Dict[str, Any]:
        """执行移动 (需先 roll)。"""
        if self.finished:
            raise GameError("对局已结束")
        if self.pending_roll is None:
            raise GameError("请先掷骰")
        roll = self.pending_roll
        player = self.current_player()
        piece = self.piece_by_id(piece_id)
        if piece is None:
            raise GameError("棋子不存在")
        if piece.owner != player.id:
            raise GameError("不能移动他人的棋子")
        if piece.id not in [p.id for p in self._movable_pieces(player, roll)]:
            raise GameError("该棋子当前无法移动")

        pv = self.preview_move(piece, roll)
        if not pv.get("ok"):
            raise GameError(pv.get("msg", "无法移动"))

        from_pos = piece.pos
        to_pos = pv["to"]

        # 规则 7: 叠子共同行动 —— 出发格上的己方棋子整叠一起移动
        group = self._stack_at(piece.owner, from_pos) if STACKING else [piece]

        for pc in group:
            pc.pos = to_pos

        # 规则 6: 撞机 —— 敌方棋子被逐回基地; 叠子被撞时整叠回基地
        captured_ids = list(dict.fromkeys(pv.get("captured", [])))
        for other_id in captured_ids:
            other = self.piece_by_id(other_id)
            if other is None:
                continue
            op = self.players[other.owner]
            # 规则 6d: 叠子参与被撞时视为一颗, 整叠回基地
            enemy_group = self._stack_at(other.owner, other.pos) if STACKING else [other]
            for ep in enemy_group:
                ep.pos = -1 - ep.index
            self._add_log(op.id,
                          f"被 {player.name} 的棋子撞机，{op.name} 的 "
                          f"{len(enemy_group)} 枚棋子被逐回基地",
                          "capture", {"pieceId": other.id, "by": player.id,
                                      "count": len(enemy_group)})

        # 规则 4/10: 抵达终点
        if to_pos == FINISHED_POS[piece.color]:
            for pc in group:
                if not pc.finished:
                    pc.finished = True
                    player.finished_pieces += 1
            player.points += BONUS_CFG["reachedHomePoints"] * len(group)
            self._add_log(player.id,
                          f"🎉 {player.name} 的 {len(group)} 枚棋子抵达终点！",
                          "finish", {"pieceId": piece.id})

        ev = pv.get("events", [])
        if "takeoff" in ev:
            self._add_log(player.id, f"{player.name} 的棋子起飞！", "move", {"pieceId": piece.id})
        if "jump" in ev:
            self._add_log(player.id, f"{player.name} 的棋子触发同色跳跃！", "move", {"pieceId": piece.id})
        if "flight" in ev:
            self._add_log(player.id, f"{player.name} 的棋子触发飞行！", "move", {"pieceId": piece.id})
        if "bounce" in ev:
            self._add_log(player.id, f"{player.name} 的棋子点数超过终点，回退", "move", {"pieceId": piece.id})
        if "stack" in ev:
            self._add_log(player.id, f"{player.name} 的棋子与其他棋子叠在一起", "move", {"pieceId": piece.id})

        self._add_log(player.id,
                      f"{player.name} 移动 {from_pos} → {to_pos}"
                      + (f" (叠子 {len(group)} 枚)" if len(group) > 1 else ""),
                      "move", {"pieceId": piece.id, "from": from_pos, "to": to_pos})

        # 规则 8: 掷 6 奖励额外一次掷骰 —— 先完成移动, 再重新掷骰, 最多奖励 3 次
        # six_bonus_rolls 记录本轮已发放的奖励次数; 满 3 次后不再奖励
        extra_turn = False
        if roll == 6 and GAME_CFG.get("sixRollsAgain", True):
            if self.six_bonus_rolls < MAX_BONUS_ROLLS:
                self.six_bonus_rolls += 1
                extra_turn = True
        self.pending_roll = None
        self.pending_piece = None

        # 规则 10: 一方 4 枚棋子全部抵达终点即结束
        if all(pc.finished for pc in player.pieces):
            self.finished = True
            self.winner = player.id
            player.points += BONUS_CFG["winnerBonusPoints"]
            self._add_log(player.id, f"🏆 {player.name} 全部棋子抵达终点，赢得本局！",
                          "finish", {"winner": True})
            return self.state()

        if not extra_turn:
            self._advance_turn()

        return self.state()

    def _advance_turn(self) -> None:
        """按座位顺时针推进到下一位未结束的玩家 (规则 9)。"""
        self.consecutive_sixes = 0
        self.six_bonus_rolls = 0
        n = len(self.players)
        idx = self.order.index(self.turn)
        for _ in range(n):
            idx = (idx + 1) % n
            self.turn = self.order[idx]
            p = self.players[self.turn]
            if not all(pc.finished for pc in p.pieces):
                return

    def _add_log(self, player_id: int, text: str, kind: str = "info",
                 payload: Optional[Dict[str, Any]] = None) -> None:
        self.log.append(LogEntry(self.turn_count, player_id, text, kind, payload or {}))

    # ─────────────── 序列化 ───────────────

    def state(self) -> Dict[str, Any]:
        players = []
        for p in self.players:
            players.append({
                "id": p.id,
                "name": p.name,
                "token": p.token,
                "color": p.color,
                "colorIndex": p.color_index,
                "points": p.points,
                "finishedPieces": p.finished_pieces,
                "isAI": p.is_ai,
                "seat": p.seat,
                "pieces": [
                    {"id": pc.id, "index": pc.index, "pos": pc.pos,
                     "finished": pc.finished, "waiting": pc.waiting}
                    for pc in p.pieces
                ],
            })
        return {
            "ok": True,
            "finished": self.finished,
            "winner": self.winner,
            "turn": self.turn,
            "turnCount": self.turn_count,
            "lastRoll": self.last_roll,
            "pendingRoll": self.pending_roll,
            "consecutiveSixes": self.consecutive_sixes,
            "sixBonusRolls": self.six_bonus_rolls,
            "maxBonusRolls": MAX_BONUS_ROLLS,
            "firstPlayer": self.first_seat,
            "turnOrder": self.order,
            "players": players,
            "board": {
                "tileCount": TILE_COUNT,
                "homeEntrances": HOME_ENTRANCES,
                "homePaths": HOME_PATHS,
                "starts": START_CELLS,
                "finishedPos": FINISHED_POS,
                "tileColors": TILE_COLOR_IDX,
                "colorNames": COLOR_NAMES,
                "nextSame": NEXT_SAME,
                "flightMap": FLIGHT_TO,
                "flightPaths": BOARD.get("flightPaths", {}),
                "tiles": TILES,
                "slots": BOARD.get("slots", []),
            },
            "rules": GAME_CFG,
            "log": [
                {"turn": e.turn, "player": e.player, "text": e.text,
                 "kind": e.kind, "payload": e.payload}
                for e in self.log[-40:]
            ],
        }
