# 🎲 飞行棋 (Flight Chess)

网页版飞行棋，支持**单人 AI 对战**与**多人联机**两种模式，人数 2~4 人可选，人数不足自动用 AI 补位。

## 技术栈

- Python 3 + FastAPI + uvicorn + python-socketio（ASGI 模式）
- 前端：原生 HTML/CSS/JS（零依赖零构建），Socket.IO 客户端已本地化到 `static/vendor/`
- 规则引擎：`engine.py`（纯逻辑，无 Web 框架依赖，可独立测试）
- 房间系统：`rooms.py`（4 位数字房间号、座位管理、断线 AI 托管）
- 配置与棋盘数据：`data/`（JSON，与代码彻底分离）
- 存储：内存单局态（进程重启清空）

## 运行

```bash
python3 app.py          # 监听 0.0.0.0:3500
```

生产环境由 systemd 托管：

```bash
systemctl restart flight-chess
```

## 目录结构

| 路径 | 角色 |
|---|---|
| `app.py` | FastAPI 后端：REST API（单人模式）+ Socket.IO 事件（联机模式）|
| `engine.py` | 游戏规则引擎（掷骰、走子、跳跃/飞跃、撞机、终点道、胜负）|
| `rooms.py` | 房间系统：4 位房间号、座位、AI 补位、断线托管、TTL 清理 |
| `data/board.json` | 棋盘定义：52 格跑道、跳跃/飞跃格、4 条终点道 |
| `data/config.json` | 玩法参数（骰子面数、每方棋子数、反弹规则、积分、AI 速度）|
| `static/index.html` | 前端页面（大厅 + 棋盘渲染 + 交互 + 音效）|
| `static/vendor/socket.io.min.js` | Socket.IO 客户端（本地化，不依赖 CDN）|
| `test_multiplayer.py` | 联机流程自动化测试（双客户端模拟两名真人）|
| `test_ai_auto.py` | 单人模式 AI 接管测试（真人走完后 AI 是否自动行动）|
| `test_fullgame.py` | 全流程对局测试（打到分出胜负，校验棋子前进与终点判定）|

## 两种模式

### 单人模式（REST API）

一名真人 + 1~3 个 AI，AI 不足时自动补位。选择人数后直接开局，无需联网房间。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 游戏页面 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 读取配置 |
| GET | `/api/state` | 当前局面 |
| POST | `/api/new` | 开新局（`{count, names}`）|
| POST | `/api/roll` | 当前玩家掷骰（多枚可动时返回 `needPieceChoice`）|
| POST | `/api/move` | 执行移动（`{pieceId}`）|
| POST | `/api/ai_turn` | AI 自动回合（掷骰 + 启发式选子）|

### 多人联机（Socket.IO）

4 位数字房间号（1000-9999）。房主创建房间并选择总人数，真人不足自动 AI 补位。

**客户端 → 服务端**

| 事件 | 参数 | 说明 |
|---|---|---|
| `create_room` | `{name, maxPlayers}` | 创建房间（房主）|
| `join_room` | `{roomId, name}` | 用 4 位房间号加入 |
| `start_game` | — | 房主开局（仅房主）|
| `roll` | — | 掷骰（仅轮到自己时）|
| `move` | `{pieceId}` | 走子（仅轮到自己时）|
| `restart_game` | — | 房主重开一局 |
| `leave_room` | — | 主动离开 |

**服务端 → 客户端**

| 事件 | 说明 |
|---|---|
| `room_created` | 房间创建成功（含房间号）|
| `room_joined` | 加入房间成功（含座位号）|
| `room_update` | 房间快照（座位 + 对局局面）|
| `dice_rolled` | 掷骰结果（含骰点、是否需选子）|
| `move_done` | 走子完成 |
| `error` | 错误提示（房间不存在 / 已满 / 非房主 / 轮次错误等）|

## 规则实现

- **棋盘**：15×15 网格，外圈 52 格环形跑道（4 lane × 13 格）+ 中心十字形终点道（每色 6 格）+ 中心终点
- **棋子**：每方 4 枚，掷出 6 可起飞
- **跳跃格**：直接跳到目标格（⤴）
- **飞跃格**：飞到对面出发区再前进 4 格（✈）
- **撞机**：落到敌方棋子所在格 → 敌方被击飞回起点
- **终点道**：走到本色入口格后，需精确点数进入；跑满 6 格抵达终点，超出则反弹
- **连续 6**：可继续掷；连续 3 个 6 本回合作废并跳过
- **胜负**：一方 4 枚棋子全部抵达终点即获胜
- **AI 补位**：真人不足目标人数时，空位由 AI 接管（启发式选子：能到终点 > 能撞机 > 终点道内 > 位置靠前）
- **断线托管**：对局中掉线，该座位转 AI 继续，避免卡死

## 部署

- systemd：`/etc/systemd/system/flight-chess.service`（端口 3500）
- Caddy 反代：`/etc/caddy/Caddyfile` 中 `handle_path /flight-chess/*` → `127.0.0.1:3500`
- 入口：`http://106.54.216.226/flight-chess/`
- 门户入口：`english-quiz/templates/portal.html` 游戏板块「飞行棋」卡片

> ⚠️ 前端 API 前缀与 Socket.IO path 均硬编码 `/flight-chess`（对应 Caddy `handle_path` strip 前缀后的路径）。
> 若改用 `handle` 而非 `handle_path`，需同步修改 `static/index.html` 的 `API` 常量与 `io({path})`。

## 测试

```bash
python3 test_ai_auto.py       # 单人模式 AI 自动接管
python3 test_fullgame.py      # 全流程对局直到分出胜负
python3 test_multiplayer.py   # 联机双客户端流程
```

覆盖：双客户端创建/加入房间、AI 补位开局、双方状态同步、真人回合掷骰走子、
非房主开局被拒、不存在房间被拒、非法房间号被拒；单人模式 AI 接管无卡死；
整局对局能正常推进到有人 4 枚棋子全部到家并弹出胜利结算。

> ⚠️ 单人模式的 AI 行动由前端循环调用 `POST /api/ai_turn` 驱动（AI 掷 6 有续掷权，一次调用走不完一整轮）。
> 联机模式的 AI 行动由服务端 `rooms._maybe_run_ai()` 自动循环推进。

## 已知限制

- 单局内存态，重启清空，无存档/排行榜
- 联机无断线重连（重连需重新加入房间）
- 移动端横屏棋盘格子较小，未做专门的触摸优化
