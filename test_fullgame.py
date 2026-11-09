"""
单人模式全流程对局测试: 一直打到分出胜负, 验证棋子确实前进且能到终点。
"""
import json
import sys
import time
import urllib.request
import urllib.error

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


def max_pos(state, owner):
    """该玩家棋子的最大进度 (终点道用 52+idx 表示)。"""
    best = -99
    for pc in state["players"][owner]["pieces"]:
        if pc["finished"]:
            best = max(best, 999)
        elif pc["waiting"]:
            best = max(best, -1)
        elif pc["pos"] >= 53:
            best = max(best, 100 + pc["pos"])   # 终点道内
        else:
            best = max(best, pc["pos"])
    return best


def main():
    r = post("/api/new", {"count": 4, "names": ["你"]})
    s = r["state"]
    print("=== 开局 ===")
    print("   ", [(p["name"], "AI" if p["isAI"] else "人") for p in s["players"]])

    progress = {0: -99, 1: -99, 2: -99, 3: -99}
    turns = 0
    ai_calls = 0
    stall_count = 0
    last_turn_count = 0

    while turns < 3000:
        s = get("/api/state")
        if s["finished"]:
            break
        turns += 1
        cur = s["players"][s["turn"]]

        # 记录进度
        for i in range(len(s["players"])):
            progress[i] = max(progress[i], max_pos(s, i))

        # 卡死检测: 连续 60 回合进度完全不变 → 出问题
        if s["turnCount"] == last_turn_count:
            stall_count += 1
            if stall_count > 300:
                print(f"❌ 疑似卡死: 连续 {stall_count} 次无推进")
                print("   progress:", progress)
                return 1
        else:
            stall_count = 0
        last_turn_count = s["turnCount"]

        if cur["isAI"]:
            res = post("/api/ai_turn")
            if res.get("ok") is False:
                print(f"❌ ai_turn 失败: {res.get('msg')}")
                return 1
            ai_calls += 1
        else:
            res = post("/api/roll")
            if res.get("ok") is False:
                print(f"❌ roll 失败: {res.get('msg')}")
                return 1
            if res.get("needPieceChoice"):
                movable = res.get("movablePieces") or []
                if not movable:
                    print("❌ needPieceChoice 但无可动棋子")
                    return 1
                res = post("/api/move", {"pieceId": movable[0]})
                if res.get("ok") is False:
                    print(f"❌ move 失败: {res.get('msg')}")
                    return 1

    s = get("/api/state")
    print(f"\n=== 结果 (总回合 {s['turnCount']}, ai_turn 调用 {ai_calls} 次) ===")
    print(f"    finished = {s['finished']}")
    if s["finished"]:
        w = s["players"][s["winner"]]
        print(f"    🏆 胜者 = {w['name']} (AI={w['isAI']})")
    else:
        print("    ❌ 3000 回合仍未分出胜负")

    print("\n=== 各方棋子最大进度 ===")
    for i, p in enumerate(s["players"]):
        fin = p["finishedPieces"]
        print(f"    {p['name']:8s} 到家 {fin}/4  最大进度 {progress[i]}")

    ok = True
    # 校验1: 必须有人获胜
    if not s["finished"]:
        print("\n❌ 未分出胜负")
        ok = False
    # 校验2: 胜者必须 4 枚全到家
    else:
        w = s["players"][s["winner"]]
        if w["finishedPieces"] != 4:
            print(f"\n❌ 胜者到家数 {w['finishedPieces']} != 4")
            ok = False
    # 校验3: 所有人棋子都要动过 (最大进度 > 0, 说明有棋子起飞过)
    dead = [i for i in range(len(s["players"])) if progress[i] <= 0]
    if dead:
        print(f"\n❌ 以下玩家棋子从未起飞: {dead}")
        ok = False
    # 校验4: 有棋子真的进了终点道
    if not any(progress[i] > 100 for i in range(len(s["players"]))):
        print("\n❌ 没有任何棋子进入终点道")
        ok = False

    if ok:
        print("\n✅ 全流程通过: 棋子正常前进 → 有人 4 枚全到家 → 分出胜负")
    return 0 if ok else 1


sys.exit(main())
