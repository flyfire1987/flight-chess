"""
🎲 飞行棋 房间系统 — 多人联机 (FastAPI + Socket.IO)

房间数据 (内存):
{
  "room_id": "1234",              # 4 位数字房间号
  "host_sid": "socket_session_id",  # 房主 socket id
  "seats": {                      # seat -> 玩家信息
      0: {"sid": ..., "name": ..., "is_ai": False, "connected": True, "player_id": 0},
      ...
  },
  "max_players": 4,               # 目标人数 (不足用 AI 补)
  "game": FlightChessGame,        # 开局后才有
  "status": "WAITING"/"PLAYING"/"FINISHED",
  "created_at": timestamp,
}

状态机:
  WAITING  → 房主创建, 等人加入 (玩家不足时 AI 补位)
  PLAYING  → 达到人数或房主开局, 对局中
  FINISHED → 分出胜负, 可重开
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional

import socketio

from engine import (
    FlightChessGame, GameError, META, CONFIG, LANE_COUNT, TILE_COUNT,
    START_CELLS,
)

# ─────────────── 房间存储 ───────────────
ROOMS: Dict[str, dict] = {}
ROOM_LOCK = asyncio.Lock()
ROOM_TTL_SECONDS = 600          # 10 分钟无人加入 → 自动解散
ROOM_ID_MIN, ROOM_ID_MAX = 1000, 9999

MAX_PLAYERS = LANE_COUNT        # 4
AI_DELAY_MS = CONFIG["game"].get("aiSpeedMs", 700)


def _new_room(room_id: str, host_sid: str, host_name: str, max_players: int) -> dict:
    return {
        "room_id": room_id,
        "host_sid": host_sid,
        "seats": {
            0: {
                "sid": host_sid,
                "name": host_name,
                "is_ai": False,
                "connected": True,
                "player_id": 0,
            }
        },
        "max_players": max_players,
        "game": None,
        "status": "WAITING",
        "created_at": time.time(),
        "last_active": time.time(),
    }


def _generate_room_id() -> Optional[str]:
    """生成不冲突的 4 位数字房间号。"""
    for _ in range(200):
        rid = str(random.randint(ROOM_ID_MIN, ROOM_ID_MAX))
        if rid not in ROOMS:
            return rid
    return None


def _used_seats(room: dict) -> List[int]:
    return sorted(room["seats"].keys())


def _seat_count(room: dict) -> int:
    """真人数量 (不含 AI)。"""
    return sum(1 for s in room["seats"].values() if not s["is_ai"])


def _serialize_room(room: dict) -> dict:
    """房间公开快照 (不含私有信息)。"""
    seats = room["seats"]
    players = []
    for seat in range(room["max_players"]):
        s = seats.get(seat)
        if s is None:
            # 空位 → AI 补位预告
            players.append({
                "seat": seat,
                "name": f"电脑{chr(65 + seat)}",
                "token": META["tokens"][seat % len(META["tokens"])],
                "color": META["colors"][seat % len(META["colors"])],
                "isAI": True,
                "empty": True,
                "connected": False,
            })
        else:
            players.append({
                "seat": seat,
                "name": s["name"],
                "token": META["tokens"][seat % len(META["tokens"])],
                "color": META["colors"][seat % len(META["colors"])],
                "isAI": s["is_ai"],
                "empty": False,
                "connected": s["connected"],
                "playerId": s.get("player_id"),
            })
    return {
        "roomId": room["room_id"],
        "hostSid": room["host_sid"],
        "status": room["status"],
        "maxPlayers": room["max_players"],
        "humanCount": _seat_count(room),
        "seats": players,
    }


def _room_state_for(room: dict, sid: str) -> dict:
    """给指定 socket 的完整状态 (含对局局面)。"""
    snap = _serialize_room(room)
    game = room.get("game")
    if game is not None:
        gs = game.state()
        snap["game"] = gs
        # 标记每个座位对应的 game player id
        seat_to_player = {}
        for seat, s in room["seats"].items():
            if s.get("player_id") is not None:
                seat_to_player[seat] = s["player_id"]
        snap["seatToPlayer"] = seat_to_player
        snap["mySeat"] = next((seat for seat, s in room["seats"].items() if s["sid"] == sid), None)
    return snap


# ─────────────── 开局 ───────────────

def _build_players(room: dict) -> List[Dict[str, Any]]:
    """
    按座位构建玩家列表。
    真人座位用实际名字, 空位用 AI 补足。
    """
    specs = []
    for seat in range(room["max_players"]):
        s = room["seats"].get(seat)
        token = META["tokens"][seat % len(META["tokens"])]
        color = META["colors"][seat % len(META["colors"])]
        if s is not None and not s["is_ai"]:
            specs.append({
                "name": s["name"],
                "token": token,
                "color": color,
                "is_ai": False,
            })
        else:
            specs.append({
                "name": f"电脑{chr(65 + seat)}",
                "token": token,
                "color": color,
                "is_ai": True,
            })
    return specs


def _start_game(room: dict) -> None:
    """按当前座位开局 (真人不足自动 AI 补位)。"""
    specs = _build_players(room)
    game = FlightChessGame(specs, seed=random.randint(0, 10**9))
    room["game"] = game
    room["status"] = "PLAYING"
    # 回写 seat -> player_id 映射
    for seat in range(room["max_players"]):
        s = room["seats"].get(seat)
        if s is not None and not s["is_ai"]:
            s["player_id"] = seat   # player id 与 seat 对齐 (座位顺序一致)


# ─────────────── AI 托管 ───────────────

def _ai_choose_piece(game: FlightChessGame, player, roll: int,
                     movable_ids: List[int]) -> Optional[int]:
    """
    AI 选子启发式:
      1. 能到终点的优先
      2. 能撞到敌方棋子的优先
      3. 已在终点跑道内的优先
      4. 否则选位置最靠前的
    """
    best_id = None
    best_score = -1e9
    for pid in movable_ids:
        piece = game.piece_by_id(pid)
        if piece is None:
            continue
        pv = game.preview_move(piece, roll)
        if not pv.get("ok"):
            continue
        score = 0.0
        to = pv["to"]
        if pv.get("arrivesFinish"):
            score += 1000
        if pv.get("captured"):
            score += 500 * len(pv["captured"])
        if piece.in_home_stretch:
            score += 120
        _path = game._home_path(piece.owner)
        if _path[1] <= to <= _path[-1]:
            score += 80
        entrance = START_CELLS[piece.owner]
        dist = (to - entrance) % TILE_COUNT
        score += (TILE_COUNT - dist) * 1.0
        if "takeoff" in pv.get("events", []):
            score += 60
        if score > best_score:
            best_score = score
            best_id = pid
    return best_id


async def _run_ai_turn(room: dict, sio) -> Optional[dict]:
    """
    执行 AI 回合 (掷骰 + 选子), 返回最终状态。
    若 AI 掷完仍需选子, 由启发式自动选。
    """
    game = room.get("game")
    if game is None or game.finished:
        return None
    player = game.current_player()
    if not player.is_ai:
        return None

    try:
        res = game.roll()
    except GameError:
        return game.state()

    if res.get("needPieceChoice"):
        movable = res.get("movablePieces", [])
        best = _ai_choose_piece(game, player, game.pending_roll or 0, movable)
        if best is not None:
            try:
                res = game.move(best)
            except GameError:
                game.pending_roll = None
                game._advance_turn()
                res = game.state()
        else:
            game.pending_roll = None
            game._advance_turn()
            res = game.state()
    return res


async def _maybe_run_ai(room: dict, sio) -> None:
    """
    若当前轮到 AI, 自动行动 (带延时), 并广播状态。
    AI 掷 6 有续掷权, 故必须循环执行, 直到回合交回真人。
    """
    game = room.get("game")
    if game is None or game.finished:
        return
    guard = 0
    while guard < 200:
        if game.finished:
            return
        player = game.current_player()
        if not player.is_ai:
            return
        await asyncio.sleep(AI_DELAY_MS / 1000.0)
        seat = player.seat
        name = player.name
        res = await _run_ai_turn(room, sio)
        if res is None:
            return
        # 广播 AI 的骰点, 让前端也能看到动画
        await _broadcast_action(room, sio, "dice_rolled", {
            "value": game.last_roll,
            "seat": seat,
            "playerName": name,
            "needPieceChoice": False,
            "movablePieces": [],
        })
        await _broadcast_state(room, sio)
        guard += 1


async def _broadcast_state(room: dict, sio) -> None:
    """把房间最新状态广播给房内所有真人。"""
    for seat, s in room["seats"].items():
        if s["is_ai"] or not s["connected"]:
            continue
        await sio.emit("room_update", _room_state_for(room, s["sid"]), to=s["sid"])


async def _broadcast_action(room: dict, sio, event: str, data: dict) -> None:
    """广播一个动作事件 (dice/move/capture 等), 带动画参数。"""
    for seat, s in room["seats"].items():
        if s["is_ai"] or not s["connected"]:
            continue
        await sio.emit(event, data, to=s["sid"])


# ─────────────── 断线处理 ───────────────

def _find_room_by_sid(sid: str) -> Optional[dict]:
    for room in ROOMS.values():
        for s in room["seats"].values():
            if s["sid"] == sid:
                return room
    return None


def promote_seat_to_ai(room: dict, seat: int) -> bool:
    """
    对局中玩家断线 → 该座位转为 AI 托管, 避免对局卡死。
    游戏 player 索引与 seat 一致 (见 _start_game)。
    返回是否成功转换。
    """
    game = room.get("game")
    if game is None or room.get("status") != "PLAYING":
        return False
    if seat >= len(game.players):
        return False
    p = game.players[seat]
    if p.is_ai:
        return False
    p.is_ai = True
    return True


async def room_cleanup_loop() -> None:
    """周期清理: 超时房间 + 断线座位托管 AI。"""
    while True:
        try:
            await asyncio.sleep(5)
            now = time.time()
            for rid, room in list(ROOMS.items()):
                room["last_active"] = now
                # 超时无真人 → 解散
                if _seat_count(room) == 0 and now - room["created_at"] > ROOM_TTL_SECONDS:
                    ROOMS.pop(rid, None)
                    continue
                # 等待中且超时 → 解散
                if room["status"] == "WAITING" and now - room["created_at"] > ROOM_TTL_SECONDS:
                    ROOMS.pop(rid, None)
        except Exception:
            pass
