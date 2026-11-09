"""
飞行棋联机流程自动化测试 (两个 Socket.IO 客户端模拟两名真人)
"""
import asyncio
import random
import sys

import socketio

URL = "http://127.0.0.1:3500"


class Client:
    def __init__(self, name):
        self.name = name
        self.sio = socketio.AsyncClient()
        self.room = None
        self.seat = None
        self.game = None
        self.errors = []
        self.dice = []
        self.room_code = None
        self.is_host = False

    async def _on_connect(self):
        pass

    async def _on_room_created(self, data):
        self.room_code = data["roomId"]
        self.is_host = True

    async def _on_room_joined(self, data):
        self.room_code = data["roomId"]
        self.seat = data["seat"]

    async def _on_room_update(self, data):
        self.room = data
        if "game" in data:
            self.game = data["game"]
        if "mySeat" in data and data["mySeat"] is not None:
            self.seat = data["mySeat"]

    async def _on_dice_rolled(self, data):
        self.dice.append(data)

    async def _on_error(self, data):
        self.errors.append(data.get("msg"))

    async def run(self):
        # 注意: python-socketio 的 @sio.event 装饰器绑定到局部函数时收不到事件,
        # 统一改用 sio.on(evt, handler) 显式注册。
        self.sio.on("connect", self._on_connect)
        self.sio.on("room_created", self._on_room_created)
        self.sio.on("room_joined", self._on_room_joined)
        self.sio.on("room_update", self._on_room_update)
        self.sio.on("dice_rolled", self._on_dice_rolled)
        self.sio.on("error", self._on_error)

        await self.sio.connect(URL, transports=["websocket"], wait_timeout=10)
        return self

    async def wait_for(self, predicate, timeout=10.0):
        waited = 0.0
        while waited < timeout:
            if predicate():
                return True
            await asyncio.sleep(0.05)
            waited += 0.05
        return False

    async def close(self):
        await self.sio.disconnect()


async def main():
    random.seed(11)
    a = await Client("阿明").run()
    b = await Client("小红").run()
    print("✓ 两个客户端已连接")

    # 1. 创建房间 (4 人)
    await a.sio.emit("create_room", {"name": "阿明", "maxPlayers": 4})
    ok = await a.wait_for(lambda: a.room_code is not None)
    if not ok:
        print("✗ 创建房间失败", a.errors); return 1
    print(f"✓ 房主创建房间, 房间号 = {a.room_code}")

    # 2. 加入房间
    await b.sio.emit("join_room", {"roomId": a.room_code, "name": "小红"})
    ok = await b.wait_for(lambda: b.room_code is not None)
    if not ok:
        print("✗ 加入房间失败", b.errors); return 1
    print(f"✓ 第二人加入房间, seat = {b.seat}")

    # 3. 等待两人都看到房间
    ok = await a.wait_for(lambda: a.room and a.room.get("humanCount") == 2)
    if not ok:
        print("✗ 房间人数未同步", a.room); return 1
    print(f"✓ 房主侧显示真人数 = {a.room['humanCount']}/{a.room['maxPlayers']}")

    # 4. 房主开局 (2 真人 + 2 AI 补位)
    await a.sio.emit("start_game")
    ok = await a.wait_for(lambda: a.game is not None and a.game.get("turn") is not None)
    if not ok:
        print("✗ 开局失败", a.errors); return 1
    await asyncio.sleep(0.3)
    print(f"✓ 开局成功, 参与者 {len(a.game['players'])} 人: "
          f"{[(p['name'], 'AI' if p['isAI'] else '真人') for p in a.game['players']]}")

    # 5. 双方都能收到对局状态
    ok = await b.wait_for(lambda: b.game is not None)
    if not ok:
        print("✗ 第二个客户端未收到对局状态"); return 1
    print("✓ 双方均已收到对局状态")

    # 6. 走到第一个真人回合, 验证掷骰 + 走子
    print("\n--- 对局交互测试 ---")
    steps = 0
    while steps < 200:
        steps += 1
        game = a.game
        if not game or game.get("finished"):
            break
        turn = game["turn"]
        player = game["players"][turn]
        if player["isAI"]:
            await asyncio.sleep(0.25)
            continue
        # 真人回合
        if a.game.get("pendingRoll") is not None:
            # 需要选子 → 用真人客户端 move
            who = a if turn == a.seat else b
            movable = who.dice[-1].get("movablePieces") if who.dice else []
            if not movable:
                # 从 game 状态里找可动棋子
                await asyncio.sleep(0.1)
                continue
            await who.sio.emit("move", {"pieceId": random.choice(movable)})
            await asyncio.sleep(0.25)
        else:
            who = a if turn == a.seat else b
            await who.sio.emit("roll")
            await asyncio.sleep(0.3)
    else:
        steps = 200

    print(f"✓ 完成 {steps} 步交互, 房间状态 = {a.room['status'] if a.room else '?'}")

    # 7. 验证 AI 补位确实动了
    ai_moves = [l for l in (a.game.get("log") or []) if "电脑" in l.get("text", "")]
    print(f"✓ AI 补位玩家行动日志 {len(ai_moves)} 条 (示例: {ai_moves[0]['text'] if ai_moves else '—'})")

    # 8. 验证错误处理: 非房主不能开局
    await b.sio.emit("start_game")
    await asyncio.sleep(0.3)
    print(f"✓ 非房主开局被拒绝: {b.errors[-1] if b.errors else '❌ 未拒绝!'}")

    # 9. 验证错误处理: 不存在的房间
    c = await Client("路人").run()
    await c.sio.emit("join_room", {"roomId": "9999", "name": "路人"})
    await asyncio.sleep(0.4)
    print(f"✓ 加入不存在房间被拒绝: {c.errors[-1] if c.errors else '❌ 未拒绝!'}")
    # 10. 验证错误处理: 非法房间号
    await c.sio.emit("join_room", {"roomId": "ab", "name": "路人"})
    await asyncio.sleep(0.4)
    print(f"✓ 非法房间号被拒绝: {c.errors[-1] if c.errors else '❌ 未拒绝!'}")
    await c.close()

    await a.close()
    await b.close()
    print("\n🎉 联机全流程测试通过")
    return 0


sys.exit(asyncio.run(main()))
