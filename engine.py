"""
🎲 飞行棋 (Flight Chess) — 核心规则引擎

纯逻辑模块, 无 Web 框架依赖, 可独立测试。

规则要点:
  - 4 色环形跑道 (每色 13 格), 外圈共用起跑格
  - 4 枚棋子/玩家, 掷骰前进; 6 可继续掷 (最多连续 3 个 6, 第 3 个 6 惩罚)
  - 跳跃格: 直接跳到目标格
  - 飞跃格: 飞到对面同色出发区再前进 4 格
  - 进终点跑道: 掷到对应点数进入本色终点道, 需精确点数抵达终点
  - 撞机: 落到敌方棋子所在格 → 敌方被击飞回起点
  - 起飞: 掷出 6 且起点为空可起飞 (可配置)
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field, asdict
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
START_CELLS = BOARD["starts"]          # 各色起点格 (红0/蓝13/黄26/绿39)
HOME_STRETCH_FIRST = min(p[1] for p in HOME_PATHS)   # 52: 终点跑道编号起点
FINISHED_POS = {o: p[-1] for o, p in enumerate(HOME_PATHS)}  # 各色终点位置

# 棋盘格颜色索引 (0=红 1=蓝 2=黄 3=绿) —— 跑道 4 色交替
COLOR_NAMES = ["red", "blue", "yellow", "green"]
TILE_COLOR_IDX = [t.get("colorIndex", t["id"] % 4) for t in TILES]

# 飞跃规则: 落到与棋子同色的格子 → 自动往前飞 N 格
FLY_RULE = BOARD.get("flyRule", {})
FLY_ENABLED = bool(FLY_RULE.get("enabled", False))
FLY_STEPS = int(FLY_RULE.get("steps", 4))


GAME_CFG = CONFIG["game"]
BONUS_CFG = CONFIG["bonus"]

# 起飞所需点数 (经典规则: 掷出 5 或 6 即可让飞机离开机场)
TAKEOFF_ROLLS = set(GAME_CFG.get("takeoffRolls", [5, 6]))

LANE_COUNT = len(HOME_PATHS)
PIECES_PER_PLAYER = GAME_CFG["piecesPerPlayer"]
DICE_SIDES = GAME_CFG["diceSides"]
MAX_SIXES = GAME_CFG["maxConsecutiveSixes"]

# 位置语义:
#   pos < 0                    -> 在起点待起飞 (pos = -1 - pieceIndex)
#   0 <= pos < 52              -> 在环形跑道 (lane 0..3 共用同一圈)
#   52 <= pos <= 75            -> 在自己颜色的终点跑道 (每色 6 格独立编号)
#   pos == FINISHED_POS[owner] -> 已抵达终点 (每色终点位置不同)
#   抵达终点后 pos 保持为该色终点值, 由 piece.finished 标记


class GameError(Exception):
    """规则错误 (非法操作), 由 API 层转成 400。"""


@dataclass
class Piece:
    """单枚棋子。"""
    id: int                       # 全局唯一 id
    owner: int                    # 所属玩家索引
    index: int                    # 玩家内序号 0..3
    pos: int                      # 见上方位置语义
    finished: bool = False

    @property
    def waiting(self) -> bool:
        """是否还在起点待起飞。"""
        return self.pos < 0

    @property
    def in_home_stretch(self) -> bool:
        """是否已进入本色终点跑道 (含最后一格)。"""
        path = HOME_PATHS[self.owner]
        return path[1] <= self.pos <= path[-1]


@dataclass
class Player:
    id: int
    name: str
    token: str
    color: str
    pieces: List[Piece] = field(default_factory=list)
    points: int = 0
    finished_pieces: int = 0
    is_ai: bool = False
    seat: int = 0

    def piece_positions(self) -> List[int]:
        return [p.pos for p in self.pieces]


@dataclass
class LogEntry:
    turn: int
    player: int
    text: str
    kind: str = "info"   # info | roll | move | capture | bonus | penalty | finish
    payload: Dict[str, Any] = field(default_factory=dict)


class FlightChessGame:
    """单局飞行棋。对局状态全部在内存, 进程重启即清空。"""

    def __init__(self, players: List[Dict[str, Any]], seed: Optional[int] = None):
        if len(players) < 2:
            raise GameError("至少需要 2 名玩家")
        if len(players) > LANE_COUNT:
            raise GameError(f"最多 {LANE_COUNT} 名玩家")
        self.rng = random.Random(seed if seed is not None else random.randint(0, 10**9))
        self.players: List[Player] = []
        self.turn = 0
        self.turn_count = 0
        self.log: List[LogEntry] = []
        self.finished = False
        self.winner: Optional[int] = None
        self.consecutive_sixes = 0
        self.pending_roll: Optional[int] = None   # 已掷出但未走子的骰点
        self.last_roll: Optional[int] = None
        self.pending_piece: Optional[int] = None   # 等待玩家选子的棋子 id

        piece_id = 0
        for i, spec in enumerate(players):
            p = Player(
                id=i,
                name=spec.get("name") or META["defaultNames"][i % len(META["defaultNames"])],
                token=spec.get("token") or META["tokens"][i % len(META["tokens"])],
                color=spec.get("color") or META["colors"][i % len(META["colors"])],
                is_ai=bool(spec.get("is_ai", i > 0)),
                seat=i,
            )
            for j in range(PIECES_PER_PLAYER):
                # pos = -1 - index → 4 枚棋子初始位置分别是 -1,-2,-3,-4
                p.pieces.append(Piece(id=piece_id, owner=i, index=j, pos=-1 - j))
                piece_id += 1
            self.players.append(p)

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

    def _home_path(self, owner: int) -> List[int]:
        return HOME_PATHS[owner]

    def _home_index(self, owner: int, pos: int) -> int:
        """终点跑道内索引 (1..6); 入口格 index 0。"""
        path = self._home_path(owner)
        try:
            return path.index(pos)
        except ValueError:
            return -1

    # ─────────────── 走子计算 (纯函数, 不改状态) ───────────────

    def preview_move(self, piece: Piece, roll: int) -> Dict[str, Any]:
        """计算某枚棋子走 roll 步后的落点与效果 (不改状态, 供前端预览)。"""
        if piece.finished:
            raise GameError("该棋子已抵达终点")

        events: List[str] = []
        captured: List[int] = []

        # 1) 起点待起飞: 经典规则 — 掷出 5 或 6 才能起飞
        if piece.waiting:
            if roll in TAKEOFF_ROLLS:
                events.append("takeoff")
                # 起飞落在自己赛段的起点格 (红0/蓝13/黄26/绿39), 不是入口格
                final_pos = START_CELLS[piece.owner]
            else:
                return {
                    "ok": False,
                    "reason": "need_six",
                    "msg": "需掷出 " + "/".join(str(x) for x in sorted(TAKEOFF_ROLLS)) + " 才能起飞",
                    "from": piece.pos,
                    "to": piece.pos,
                }
        elif piece.in_home_stretch:
            # 2a) 已在终点跑道内
            final_pos = self._advance_home_stretch(piece, roll, events)
        else:
            # 2b) 在环形跑道内
            final_pos = self._advance_ring(piece, roll, events)

        if final_pos is None:
            return {"ok": False, "reason": "invalid", "msg": "无法移动", "from": piece.pos, "to": piece.pos}

        # 3) 撞机判定 (终点跑道内不撞机)
        path = self._home_path(piece.owner)
        if not (path[1] <= final_pos <= path[-1]):
            for other in self._pieces_at(final_pos, exclude=piece.id):
                if other.owner != piece.owner:
                    captured.append(other.id)
                    events.append("capture")

        return {
            "ok": True,
            "from": piece.pos,
            "to": final_pos,
            "homeStep": (final_pos - path[1] + 1) if path[1] <= final_pos <= path[-1] else 0,
            "events": events,
            "captured": captured,
            "arrivesFinish": final_pos == FINISHED_POS[piece.owner],
        }

    def _advance_home_stretch(self, piece: Piece, roll: int,
                              events: List[str]) -> int:
        """
        在终点跑道内前进。
        path[0] = 入口格, path[1..6] = 跑道 6 格 (path[6] = 58 为最后一格)。
        规则: 需精确走到最后一格 (path[6]) 才抵达终点 (pos = FINISHED);
        超过则反弹回退。path[1]..path[5] 为中途格。
        """
        path = self._home_path(piece.owner)
        idx = self._home_index(piece.owner, piece.pos)
        if idx < 0:
            idx = 0
        last_cell = len(path) - 1        # = 6 -> path[6] = 58 最后一格
        new_idx = idx + roll
        if new_idx > last_cell:
            if GAME_CFG["bounceOnOvershoot"]:
                over = new_idx - last_cell
                new_idx = last_cell - over
                if new_idx < 1:
                    new_idx = 1
                events.append("bounce")
                return path[new_idx]
            new_idx = last_cell
        if new_idx == last_cell:
            # 精确抵达最后一格 -> 终点
            return FINISHED_POS[piece.owner]
        return path[new_idx]

    def _advance_ring(self, piece: Piece, roll: int,
                      events: List[str]) -> Optional[int]:
        """
        在环形跑道 (0..51) 内前进, 处理跳跃/飞跃。
        进入本色终点跑道: 走到入口格时停下; 掷点数恰好等于入口前进方向
        所需步数时, 直接进入跑道第 1 格 (即 homePath[1])。
        """
        owner = piece.owner
        entrance = HOME_ENTRANCES[owner]
        path = self._home_path(owner)

        cur = piece.pos
        remaining = roll
        my_color = piece.owner      # 玩家索引 == 颜色索引 (红0 蓝1 黄2 绿3)

        # 逐格推进; 落到与自己同色的格子 → 自动往前飞 FLY_STEPS 格
        while remaining > 0:
            nxt = cur + 1
            if nxt >= TILE_COUNT:
                nxt = 0
            cur = nxt
            remaining -= 1

            # 同色格 → 跳 4 格 (只跳一次, 不连锁); 白底格(-1)不触发
            if FLY_ENABLED and TILE_COLOR_IDX[cur] >= 0 and TILE_COLOR_IDX[cur] == my_color:
                events.append("fly")
                for _ in range(FLY_STEPS):
                    n2 = cur + 1
                    if n2 >= TILE_COUNT:
                        n2 = 0
                    cur = n2
                # 飞跃后可能正好落在入口格 → 若还有剩余步数则进跑道
                if cur == entrance and remaining > 0:
                    return path[1] if len(path) > 1 else cur
                continue

            # 落到入口格
            if cur == entrance:
                if remaining == 0:
                    return entrance      # 正好停在入口格, 下回合再进跑道
                # 还有剩余步数 → 进入终点跑道
                step = remaining
                if step >= len(path) - 1:
                    return FINISHED_POS[owner]
                return path[step]

        return cur

    # ─────────────── 实际执行 ───────────────

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
        self.log.append(LogEntry(self.turn_count, player.id, f"{player.name} 掷出 {value} 点", "roll", {"value": value}))

        if value == 6:
            self.consecutive_sixes += 1
            if self.consecutive_sixes >= MAX_SIXES and GAME_CFG["threeSixesPenalty"]:
                self.consecutive_sixes = 0
                self.pending_roll = None
                self._add_log(player.id, f"连续 3 个 6，{player.name} 本回合作废并跳过", "penalty")
                self._advance_turn()
                return self.state()
        else:
            self.consecutive_sixes = 0

        self.pending_roll = value

        # 自动选择: 只有一枚可动棋子时直接走
        movable = self._movable_pieces(player, value)
        if not movable:
            self._add_log(player.id, f"{player.name} 无子可动，跳过回合", "info")
            self.pending_roll = None
            self._advance_turn()
            return self.state()
        if len(movable) == 1:
            return self.move(movable[0].id)

        # 多枚可动 → 交给前端选
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
        piece.pos = to_pos

        # 撞机: 敌方棋子被击飞回起点
        captured_ids = pv.get("captured", [])
        for other_id in captured_ids:
            other = self.piece_by_id(other_id)
            if other is None:
                continue
            op = self.players[other.owner]
            other.pos = -1 - other.index
            op.finished_pieces = max(0, op.finished_pieces)
            self._add_log(op.id, f"被 {player.name} 的棋子撞机，{op.name} 的棋子被击飞回起点", "capture",
                          {"pieceId": other.id, "by": player.id})

        # 到达终点
        if to_pos == FINISHED_POS[player.id]:
            piece.finished = True
            player.finished_pieces += 1
            player.points += BONUS_CFG["reachedHomePoints"]
            self._add_log(player.id, f"🎉 {player.name} 的一枚棋子抵达终点！", "finish", {"pieceId": piece.id})

        # 事件文案
        ev = pv.get("events", [])
        if "takeoff" in ev:
            self._add_log(player.id, f"{player.name} 的棋子起飞！", "move", {"pieceId": piece.id})
        if "jump" in ev:
            self._add_log(player.id, f"{player.name} 的棋子触发跳跃！", "move", {"pieceId": piece.id})
        if "fly" in ev:
            self._add_log(player.id, f"{player.name} 的棋子完成飞跃！", "move", {"pieceId": piece.id})
        if "bounce" in ev:
            self._add_log(player.id, f"{player.name} 的棋子到达终点后反弹", "move", {"pieceId": piece.id})

        # 移动基础文案
        self._add_log(player.id, f"{player.name} 移动 {from_pos} → {to_pos}", "move",
                      {"pieceId": piece.id, "from": from_pos, "to": to_pos})

        # 掷 6 继续
        extra_turn = (roll == 6 and GAME_CFG["sixRollsAgain"] and self.consecutive_sixes < MAX_SIXES)
        self.pending_roll = None
        self.pending_piece = None

        # 全部棋子到家 → 获胜
        if all(pc.finished for pc in player.pieces):
            self.finished = True
            self.winner = player.id
            player.points += BONUS_CFG["winnerBonusPoints"]
            self._add_log(player.id, f"🏆 {player.name} 全部棋子抵达终点，赢得本局！", "finish", {"winner": True})
            return self.state()

        if not extra_turn:
            self._advance_turn()

        return self.state()

    def _advance_turn(self) -> None:
        self.turn = (self.turn + 1) % len(self.players)
        # 跳过已全部到家的玩家
        for _ in range(len(self.players)):
            p = self.players[self.turn]
            if all(pc.finished for pc in p.pieces):
                self.turn = (self.turn + 1) % len(self.players)
            else:
                break

    def _add_log(self, player_id: int, text: str, kind: str = "info", payload: Optional[Dict] = None) -> None:
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
                "points": p.points,
                "finishedPieces": p.finished_pieces,
                "isAI": p.is_ai,
                "seat": p.seat,
                "pieces": [
                    {"id": pc.id, "index": pc.index, "pos": pc.pos, "finished": pc.finished, "waiting": pc.waiting}
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
            "players": players,
            "board": {
                "tileCount": TILE_COUNT,
                "homeEntrances": HOME_ENTRANCES,
                "homePaths": HOME_PATHS,
                "starts": START_CELLS,
                "finishedPos": FINISHED_POS,
                "tileColors": TILE_COLOR_IDX,
                "colorNames": COLOR_NAMES,
                "flyRule": {"enabled": FLY_ENABLED, "steps": FLY_STEPS},
                "tiles": TILES,
            },
            "rules": GAME_CFG,
            "log": [
                {"turn": e.turn, "player": e.player, "text": e.text, "kind": e.kind, "payload": e.payload}
                for e in self.log[-40:]
            ],
        }
