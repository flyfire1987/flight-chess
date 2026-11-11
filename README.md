# 🎲 飞行棋 (Flight Chess)

网页版飞行棋，支持**单人 AI 对战**与**多人联机**两种模式，人数 2~4 人可选，人数不足自动用 AI 补位。

## 技术栈

- Python 3 + FastAPI + uvicorn + python-socketio（ASGI 模式）
- 前端：原生 HTML/CSS/JS（零依赖零构建），Socket.IO 客户端已本地化到 `static/vendor/`
- 规则引擎：`engine.py`（纯逻辑，无 Web 框架依赖，可独立测试）
- 房间系统：`rooms.py`（4 位数字房间号、座位管理、断线 AI 托管）
- 配置与棋盘数据：`data/`（JSON，与代码彻底分离）
- 存储：内存单局态（进程重启清空）

## 📋 官方规则实现 (2026-11-10 版)

引擎已按官方 10 条规则重写，`data/board.json` + `data/config.json` 驱动，前后端共用同一份数据。

| # | 规则 | 实现 |
|---|---|---|
| 1 | 比赛人数 2~4 人 | `GameError` 拦截 <2 或 >4；单人模式 `count` 可选 2/3/4 |
| 2 | 任选已起飞棋子；5/6 起飞；无子可动盖轮 | `_movable_pieces()` 返回全部可动棋子；`_advance_turn()` 盖轮跳过 |
| 3 | 5/6 点从基地起飞进停机坪 | `START_CELLS[color]`，起飞点按颜色区分 |
| 4 | 精确抵达终点，超出回退 | `_advance_home_stretch()` / `_enter_home()`，超额按 `bounce` 事件回退 |
| 5a | 落在同色格 → 跳到前方下一个同色格 | `board.json > nextSame`（28 条），**只跳一次** |
| 5b | 落在飞行起点 → 沿红箭头飞向对面，飞后再继续跳 | `board.json > flightMap` / `flightPaths` |
| 6a | 普通行棋路过敌方不撞，落到同一格才撞 | `_advance_ring()` 只在落点判撞 |
| 6b | 飞行途中经过敌方可以撞回 | `_resolve_chain()` 扫 `flightPaths.mid` |
| 6c | 跳跃途中经过敌方不能撞回 | `_resolve_chain()` 跳跃分支不判撞 |
| 6d | 叠子参与撞与被撞视为一颗 | 敌方整叠一起回基地 |
| 7 | 己方棋子落同格 → 叠在一起共同行动 | `_stack_at()` 整叠同步移动 |
| 8 | 掷 6 奖励一次，最多 3 次，先行棋再掷 | `six_bonus_rolls` 独立计数，`game.maxBonusRolls = 3`；奖励额度在换手时复位 |
| 9 | 随机选红色先手，其余按座位顺时针 | `first_seat` 随机，先手 `color_index = 0`（红） |
| 10 | 一方 4 枚全部到终点即结束 | `all(pc.finished ...)` → `finished` + `winner` |

### 棋盘关系数据 (`data/board.json`)

| 字段 | 说明 |
|---|---|
| `nextSame[]` | 28 条 `{from,to,color}`，前方下一个同色格 |
| `flightMap[]` | 4 条 `{from,to,owner}`，飞行起点 → 对面飞行终点 |
| `flightPaths{}` | 每条飞行路径的 `mid[]`（途中格，供规则 6b 判撞） |
| `jumpRule` | 跳跃模式说明 (`nextSameColor`) |

当前四色飞行映射（由棋盘结构自洽推导：飞落后继续跳恰好进本家终点入口，4/4 成立）：

```text
红 15 → 47 → 跳 7    (红入口)
蓝 41 → 21 → 跳 33   (蓝入口)
黄 28 →  8 → 跳 20   (黄入口)
绿  2 → 34 → 跳 46   (绿入口)
```

### 验收测试

| 文件 | 覆盖 |
|---|---|
| `test_rules.py` | **41 条断言**，逐条覆盖 10 条规则（2~4人/盖轮/精确到达与回退/同色跳跃/飞行连锁/路过不撞/落点撞/飞行撞/跳跃不撞/叠子/掷6奖励顺序/随机先手/终局）|
| `test_ai_auto.py` | 单人模式真人 10 步 + AI 接管 30 次，验 AI 不卡死 |
| `test_fullgame.py` | 全流程对局，打到分出胜负 |
| `test_multiplayer.py` | 联机 200 步交互 + 三类非法请求拒绝 |

```bash
python3 test_rules.py      # 41 PASS / 0 FAIL
```

> ⚠️ 改完 `engine.py` 必须先 `systemctl restart flight-chess`，否则测的是旧代码。
> 视觉验收必须走 Caddy 公共路径 `http://127.0.0.1/flight-chess/`，直连 3500 会因静态资源路径 404 导致棋盘 0×0。
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
| `data/board.json` | 棋盘定义 v2.0：52 格跑道（`tiles[].colorIndex` 配色）、4 家机场/停机坪/终点道（`slots[]`）、起点与入口 |
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

- **棋盘**：15×15 逻辑网格，外圈 52 格环形跑道（4 lane × 13 格）+ 每色 6 格终点道 + 中心终点圆盘；四角为 L 形彩色机场 + 2×2 白色停机坪（完整占满 3×3 角块）
- **配色**：白底黑线（`#ffffff` + `#1a1a1a` 1.5px 边框），彩色格取自 `board.json` 的 `meta.palette`（红 `#d03020` / 蓝 `#0080c0` / 黄 `#f0f040` / 绿 `#009040`）；跑道实测分布为 **白 24 格 + 各色 7 格**
- **起点**：各色 L 形机场顺时针方向的尖端，四家起点→终点入口距离统一 **44 格**（公平性校验）
- **棋子**：每方 4 枚，**掷出 5 或 6 点**才能从机场起飞，落在自家起点格
- **同色跳跃**：落到与自己同色的格子 → 自动往前跳 **4 格**，**只跳一次、不连锁**；白底格不触发
- **撞机**：落到敌方棋子所在格 → 敌方被击飞回起点（终点道内不撞机）
- **终点道**：走到本色入口格后进入本色终点道；跑满 6 格抵达终点，超出则反弹。**入口判定优先于同色跳跃**（入口格本身是本家颜色，否则棋子每次到入口都会被跳过 4 格而永远进不了终点道）
- **连续 6**：掷出 6 可继续掷；连续 3 个 6 本回合作废并跳过
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

> ⚠️ 三组测试全部通过 **HTTP** 打 `127.0.0.1:3500`，不是 `import engine`。
> **改完 `engine.py` 必须先 `systemctl restart flight-chess.service` 再跑测试**，否则测的是旧代码。
> 排查手法：`stat -c '%y' engine.py` 对比 `systemctl show flight-chess.service -p ActiveEnterTimestamp`。

### 2026-11-11 规则审计结果

对官方 10 条规则逐条审计（`test_rules.py`，41 项断言全部通过），并修复 1 处真实偏差：

| 项 | 审计前 | 审计后 |
|---|---|---|
| 规则 8 掷 6 奖励次数 | 只奖励 **2 次**（与 `maxConsecutiveSixes` 耦合） | **最多 3 次**（独立 `six_bonus_rolls` 计数） |

其余 9 条规则实现与官方规则一致，其中此前**未实现/未验证**的精细撞子规则已全部落地并有断言覆盖：

| 规则 | 断言覆盖 |
|---|---|
| 6a 普通移动路过敌方 **不撞** | ✅ 落点判定 + 敌方位置不变 |
| 6a 落到同一格 **撞回** | ✅ 被撞棋子回基地 |
| 6b **飞行途中**经过敌方可撞 | ✅ `flightPaths.mid` 扫描 |
| 6c **跳跃途中**经过敌方不撞 | ✅ 跳跃分支不判撞 |
| 6d 叠子撞与被撞按 **一颗棋子** | ✅ 敌方整叠回基地 |

### 2026-11-10 v2 验收基线

| 项 | 实测值 |
|---|---|
| DOM | cells 76（52 跑道 + 24 终点道）/ airport 20 / hangar 16 / bigpath 24 / center 1 / pieces 16 / starts 4 |
| 配色 | 白 24 + 红 13 / 蓝 13 / 黄 13 / 绿 13（每色 7 跑道 + 6 终点道） |
| JS 错误 | none |
| `test_fullgame.py` | 705 回合分出胜负，4 枚棋子全部到家 |
| `test_ai_auto.py` | 真人 10 步，AI 自动接管 30 次，无卡死 |
| `test_multiplayer.py` | 200 步联机交互，三类非法请求均被拒 |

### 视觉验收必须走 Caddy 入口

```bash
# ✅ 正确
http://127.0.0.1/flight-chess/
# ❌ 直连 3500 时前端硬编码的 /flight-chess/static/... 会 404，#board 宽度为 0
http://127.0.0.1:3500/
```

> ⚠️ 单人模式的 AI 行动由前端循环调用 `POST /api/ai_turn` 驱动（AI 掷 6 有续掷权，一次调用走不完一整轮）。
> 联机模式的 AI 行动由服务端 `rooms._maybe_run_ai()` 自动循环推进。

## 已知限制

- 单局内存态，重启清空，无存档/排行榜
- 联机无断线重连（重连需重新加入房间）
- 移动端横屏棋盘格子较小，未做专门的触摸优化
- 棋盘配色按参考图实测值还原，未经用户最终视觉确认；调整只需改 `data/board.json` 的 `tiles[].colorIndex` 与 `meta.palette`，前端无需改动
