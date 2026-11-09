"""
单人模式 AI 接管测试: 模拟前端完整链路
真人掷骰 → (若需选子则选) → AI 应自动连续行动直到回合交回真人
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:3500"


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def main():
    print("=== 1. 开 4 人局 (1 真人 + 3 AI) ===")
    r = post("/api/new", {"count": 4, "names": ["你"]})
    s = r["state"]
    print("   ", [(p["name"], "AI" if p["isAI"] else "人") for p in s["players"]])

    print("\n=== 2. 真人连续操作, 观察 AI 是否自动接管 ===")
    ai_turn_count = 0
    human_turns = 0
    stall_detected = False

    for step in range(40):
        s = get("/api/state")
        if s["finished"]:
            print(f"   对局结束, 胜者 = {s['players'][s['winner']]['name']}")
            break
        cur = s["players"][s["turn"]]

        if cur["isAI"]:
            # AI 回合 → 调 ai_turn, 它应完成掷骰+选子+移动
            before_turn = s["turn"]
            before_count = s["turnCount"]
            res = post("/api/ai_turn")
            if res.get("ok") is False:
                print(f"   ❌ ai_turn 失败: {res.get('msg')}")
                return 1
            after = get("/api/state")
            progressed = after["turnCount"] > before_count
            if not progressed:
                print(f"   ❌ AI 回合未推进 (turn {before_turn} -> {after['turn']}, "
                      f"turnCount {before_count} -> {after['turnCount']})")
                stall_detected = True
                break
            ai_turn_count += 1
            # AI 掷 6 会有续掷权, 此时 turn 仍是自己 → 继续循环
            time.sleep(0.05)
        else:
            # 真人回合 → 掷骰
            human_turns += 1
            before = s["turn"]
            res = post("/api/roll")
            if res.get("ok") is False:
                print(f"   ❌ 真人 roll 失败: {res.get('msg')}")
                return 1
            if res.get("needPieceChoice"):
                movable = res.get("movablePieces") or []
                if not movable:
                    print("   ❌ needPieceChoice 但无可动棋子")
                    return 1
                res = post("/api/move", {"pieceId": movable[0]})
                if res.get("ok") is False:
                    print(f"   ❌ 真人 move 失败: {res.get('msg')}")
                    return 1
            time.sleep(0.05)
            # 真人走完 → 立刻检查是否轮到 AI
            after = get("/api/state")
            ncur = after["players"][after["turn"]]
            if ncur["isAI"]:
                print(f"   真人第 {human_turns} 步完成 → 轮到 {ncur['name']} (AI), 开始自动接管")

    print(f"\n=== 3. 结果 ===")
    print(f"    真人操作 {human_turns} 次, AI 自动行动 {ai_turn_count} 次")
    if stall_detected:
        print("    ❌ 存在卡死")
        return 1
    if ai_turn_count == 0:
        print("    ❌ AI 从未自动行动")
        return 1
    print("    ✅ AI 能自动接管, 无卡死")
    return 0


sys.exit(main())
