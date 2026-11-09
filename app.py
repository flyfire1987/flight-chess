"""
🎲 飞行棋 (Flight Chess) — FastAPI 后端

支持两种模式:
  1. 单人模式 (单机 AI): 走 REST API, 无需联网
  2. 多人联机: 4 位数字房间号, 走 Socket.IO, 真人不足用 AI 补位

REST API:
- GET  /               -> 静态页面
- GET  /api/state      -> 当前局面 (单机模式)
- POST /api/new        -> 开新局 (单机模式)
- POST /api/roll       -> 当前玩家掷骰
- POST /api/move       -> 执行移动 (需先掷骰)
- POST /api/ai_turn    -> AI 自动行动 (掷骰 + 选子)
- GET  /api/config     -> 读取配置
- GET  /api/health     -> 健康检查

Socket.IO (多人联机):
- create_room(name, maxPlayers) -> room_created
- join_room(roomId, name)       -> room_joined / error
- start_game()                   -> 广播 room_update
- roll()                         -> 广播 dice_rolled
- move(pieceId)                  -> 广播 move_done
- restart_game()                 -> 广播 room_update
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
from typing import Any, Dict, List, Optional

import socketio
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import rooms as R
from engine import FlightChessGame, GameError, META, CONFIG, TILE_COUNT, START_CELLS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.json")

app = FastAPI(title="飞行棋 Flight Chess", version="2.0")
LOCK = threading.Lock()
GAME: Optional[FlightChessGame] = None

# ─────────────── Socket.IO ───────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)
socket_app = socketio.ASGIApp(sio)
app.mount("/socket.io", socket_app)

TOKENS = META["tokens"]
COLORS = META["colors"]
DEFAULT_NAMES = META["defaultNames"]


def ensure_game() -> FlightChessGame:
    global GAME
    if GAME is None:
        GAME = FlightChessGame(_default_players())
    return GAME


def _default_players(count: int = 4) -> List[Dict[str, Any]]:
    specs = []
    for i in range(count):
        specs.append({
            "name": DEFAULT_NAMES[i] if i < len(DEFAULT_NAMES) else f"电脑{chr(65 + i - 1)}",
            "token": TOKENS[i % len(TOKENS)],
            "color": COLORS[i % len(COLORS)],
            "is_ai": i > 0,
        })
    return specs


# ─────────────── 页面 ───────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ─────────────── API ───────────────


@app.get("/api/health")
def api_health():
    with LOCK:
        return {
            "ok": True,
            "service": "flight-chess",
            "hasGame": GAME is not None,
            "finished": GAME.finished if GAME else False,
            "tileCount": TILE_COUNT,
        }


@app.get("/api/config")
def api_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/state")
def api_state():
    with LOCK:
        return ensure_game().state()


@app.post("/api/new")
def api_new(body: Optional[Dict[str, Any]] = None):
    global GAME
    with LOCK:
        body = body or {}
        count = int(body.get("count", CONFIG["game"]["playerCount"]))
        count = max(2, min(4, count))
        specs = _default_players(count)
        names = body.get("names") or []
        for i, spec in enumerate(specs):
            if i < len(names) and names[i]:
                spec["name"] = names[i]
            if "isAI" in body and i < len(body["isAI"]):
                spec["is_ai"] = bool(body["isAI"][i])
        GAME = FlightChessGame(specs, seed=random.randint(0, 10**9))
        return {"ok": True, "state": GAME.state()}


@app.post("/api/roll")
def api_roll():
    with LOCK:
        g = ensure_game()
        try:
            return g.roll()
        except GameError as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)


@app.post("/api/move")
def api_move(body: Optional[Dict[str, Any]] = None):
    with LOCK:
        g = ensure_game()
        body = body or {}
        piece_id = body.get("pieceId")
        if piece_id is None:
            return JSONResponse({"ok": False, "msg": "缺少 pieceId"}, status_code=400)
        try:
            return g.move(int(piece_id))
        except GameError as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)


@app.post("/api/ai_turn")
def api_ai_turn():
    """AI 回合: 掷骰并自动选子移动 (使用启发式选子)。"""
    with LOCK:
        g = ensure_game()
        if g.finished:
            return {"ok": False, "msg": "对局已结束"}
        player = g.current_player()
        if not player.is_ai:
            return JSONResponse({"ok": False, "msg": "当前不是 AI 回合"}, status_code=400)
        try:
            res = g.roll()
        except GameError as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)

        # 需要选子 → AI 启发式选一个
        if res.get("needPieceChoice"):
            movable_ids = res.get("movablePieces", [])
            best = _ai_pick_piece(g, player, g.pending_roll, movable_ids)
            if best is not None:
                res = g.move(best)
            else:
                g.pending_roll = None
                g._advance_turn()
                res = g.state()
        return res


def _ai_pick_piece(g: FlightChessGame, player, roll: int,
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
        piece = g.piece_by_id(pid)
        if piece is None:
            continue
        pv = g.preview_move(piece, roll)
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
        _path = g._home_path(piece.owner)
        if _path[1] <= to <= _path[-1]:
            score += 80
        # 位置越靠前越好 (环形跑道上, 距离自己入口越近越好)
        entrance = START_CELLS[piece.owner]
        dist = (to - entrance) % TILE_COUNT
        score += (TILE_COUNT - dist) * 1.0
        # 起飞也算好
        if "takeoff" in pv.get("events", []):
            score += 60
        if score > best_score:
            best_score = score
            best_id = pid
    return best_id


# ─────────────── Socket.IO 事件 (多人联机) ───────────────
# 客户端 → 服务端:
#   create_room(name, maxPlayers) → room_created
#   join_room(roomId, name)       → room_joined / error
#   start_game()                   → 广播 room_update
#   roll()                         → 广播 dice_rolled
#   move(pieceId)                  → 广播 move_done
#   restart_game()                 → 广播 room_update
#   leave_room()                   → 离开房间


@sio.event
async def connect(sid, environ):
    pass


@sio.event
async def disconnect(sid):
    """断线: 标记座位离线, 对局中转 AI 托管。"""
    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            return
        seat = next((k for k, s in room["seats"].items() if s["sid"] == sid), None)
        if seat is None:
            return
        room["seats"][seat]["connected"] = False
        # 等待中: 直接移除该座位
        if room["status"] == "WAITING":
            room["seats"].pop(seat, None)
            if room["host_sid"] == sid:
                # 房主离开 → 移交给其他人或解散
                others = [k for k, s in room["seats"].items() if s["sid"] != sid]
                if others:
                    room["host_sid"] = room["seats"][others[0]]["sid"]
                else:
                    R.ROOMS.pop(room["room_id"], None)
                    return
        else:
            # 对局中断线 → 该座位转 AI 托管, 避免卡死
            R.promote_seat_to_ai(room, seat)
        await R._broadcast_state(room, sio)
        # 若当前正轮到该座位, 让 AI 接手继续走
        await R._maybe_run_ai(room, sio)


@sio.event
async def create_room(sid, data):
    """创建房间 (房主)。maxPlayers = 目标总人数, 真人不足用 AI 补。"""
    data = data or {}
    name = str(data.get("name") or "玩家").strip()[:20] or "玩家"
    try:
        max_players = int(data.get("maxPlayers") or 4)
    except (TypeError, ValueError):
        max_players = 4
    max_players = max(2, min(R.MAX_PLAYERS, max_players))

    async with R.ROOM_LOCK:
        rid = R._generate_room_id()
        if rid is None:
            await sio.emit("error", {"msg": "房间号生成失败，请重试"}, to=sid)
            return
        room = R._new_room(rid, sid, name, max_players)
        R.ROOMS[rid] = room
    await sio.emit("room_created", {
        "roomId": rid,
        "name": name,
        "maxPlayers": max_players,
        "host": True,
    }, to=sid)
    await R._broadcast_state(room, sio)


@sio.event
async def join_room(sid, data):
    """加入房间 (4 位数字房间号)。"""
    data = data or {}
    rid = str(data.get("roomId") or "").strip()
    name = str(data.get("name") or "玩家").strip()[:20] or "玩家"

    if not (rid.isdigit() and len(rid) == 4):
        await sio.emit("error", {"msg": "房间号必须是 4 位数字"}, to=sid)
        return

    async with R.ROOM_LOCK:
        room = R.ROOMS.get(rid)
        if room is None:
            await sio.emit("error", {"msg": f"房间 {rid} 不存在"}, to=sid)
            return
        if room["status"] != "WAITING":
            await sio.emit("error", {"msg": f"房间 {rid} 已在游戏中"}, to=sid)
            return
        # 满员检查 (按真人座位数)
        if R._seat_count(room) >= room["max_players"]:
            await sio.emit("error", {"msg": "房间已满"}, to=sid)
            return
        # 分配第一个空座位
        seat = None
        for i in range(room["max_players"]):
            if i not in room["seats"]:
                seat = i
                break
        if seat is None:
            await sio.emit("error", {"msg": "房间已满"}, to=sid)
            return
        room["seats"][seat] = {
            "sid": sid,
            "name": name,
            "is_ai": False,
            "connected": True,
            "player_id": None,
        }
        snap = R._room_state_for(room, sid)

    await sio.emit("room_joined", {
        "roomId": rid,
        "name": name,
        "seat": seat,
        "maxPlayers": room["max_players"],
        "host": False,
    }, to=sid)
    await R._broadcast_state(room, sio)


@sio.event
async def start_game(sid, data=None):
    """房主开局 (真人不足自动 AI 补位)。"""
    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            await sio.emit("error", {"msg": "你不在任何房间"}, to=sid)
            return
        if room["host_sid"] != sid:
            await sio.emit("error", {"msg": "只有房主可以开始游戏"}, to=sid)
            return
        if room["status"] == "PLAYING":
            return
        R._start_game(room)

    await R._broadcast_state(room, sio)
    await R._maybe_run_ai(room, sio)


@sio.event
async def roll(sid, data=None):
    """联机掷骰。"""
    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            await sio.emit("error", {"msg": "你不在任何房间"}, to=sid)
            return
        game = room.get("game")
        if game is None or game.finished:
            await sio.emit("error", {"msg": "对局未在进行中"}, to=sid)
            return
        seat = next(k for k, s in room["seats"].items() if s["sid"] == sid)
        if game.turn != seat:
            await sio.emit("error", {"msg": "还没轮到你"}, to=sid)
            return
        if game.pending_roll is not None:
            return
        try:
            res = game.roll()
        except GameError as e:
            await sio.emit("error", {"msg": str(e)}, to=sid)
            return

        value = game.last_roll
        need_choice = res.get("needPieceChoice", False)
        movable = res.get("movablePieces", [])

    # 广播骰点
    await R._broadcast_action(room, sio, "dice_rolled", {
        "value": value,
        "seat": seat,
        "playerName": game.players[game.turn].name,
        "needPieceChoice": need_choice,
        "movablePieces": movable,
    })
    await R._broadcast_state(room, sio)

    if not need_choice:
        await R._maybe_run_ai(room, sio)


@sio.event
async def move(sid, data=None):
    """联机走子。"""
    data = data or {}
    piece_id = data.get("pieceId")

    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            await sio.emit("error", {"msg": "你不在任何房间"}, to=sid)
            return
        game = room.get("game")
        if game is None or game.finished:
            await sio.emit("error", {"msg": "对局未在进行中"}, to=sid)
            return
        seat = next(k for k, s in room["seats"].items() if s["sid"] == sid)
        if game.turn != seat:
            await sio.emit("error", {"msg": "还没轮到你"}, to=sid)
            return
        if piece_id is None:
            await sio.emit("error", {"msg": "缺少 pieceId"}, to=sid)
            return
        try:
            res = game.move(int(piece_id))
        except GameError as e:
            await sio.emit("error", {"msg": str(e)}, to=sid)
            return
        need_choice = res.get("needPieceChoice", False)

    await R._broadcast_action(room, sio, "move_done", {
        "seat": seat,
        "pieceId": piece_id,
        "needPieceChoice": need_choice,
    })
    await R._broadcast_state(room, sio)

    if not need_choice:
        await R._maybe_run_ai(room, sio)


@sio.event
async def restart_game(sid, data=None):
    """房主重开一局 (座位保留)。"""
    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            await sio.emit("error", {"msg": "你不在任何房间"}, to=sid)
            return
        if room["host_sid"] != sid:
            await sio.emit("error", {"msg": "只有房主可以重开"}, to=sid)
            return
        room["status"] = "WAITING"
        room["game"] = None
        for s in room["seats"].values():
            s["player_id"] = None
        R._start_game(room)

    await R._broadcast_state(room, sio)
    await R._maybe_run_ai(room, sio)


@sio.event
async def leave_room(sid, data=None):
    """主动离开房间。"""
    async with R.ROOM_LOCK:
        room = R._find_room_by_sid(sid)
        if room is None:
            return
        seat = next((k for k, s in room["seats"].items() if s["sid"] == sid), None)
        if seat is not None:
            room["seats"].pop(seat, None)
        if room["host_sid"] == sid:
            others = list(room["seats"].keys())
            if others:
                room["host_sid"] = room["seats"][others[0]]["sid"]
            else:
                R.ROOMS.pop(room["room_id"], None)
                return
    await R._broadcast_state(room, sio)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(R.room_cleanup_loop())


# 静态文件挂载 (放最后, 避免拦截 API 路由)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3500, log_level="info")
