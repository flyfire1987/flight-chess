# 🎲 飞行棋 (Flight Chess)

网页版飞行棋,支持**单人 AI 对战**与**多人联机**两种模式,人数 2~4 人可选,人数不足自动用 AI 补位。

## 技术栈
- Python 3 + FastAPI + uvicorn + python-socketio(ASGI 模式)
- 前端:原生 HTML/CSS/JS(零依赖零构建),Socket.IO 客户端已本地化到 `static/vendor/`
- 规则引擎 `engine.py` 为纯逻辑模块,无 Web 框架依赖,可独立测试
- 棋盘几何与玩法参数外置在 `data/`,与代码彻底分离

## 运行
```bash
python3 app.py    # 监听 0.0.0.0:3500
```

## 目录
| 路径 | 角色 |
|---|---|
| `app.py` | FastAPI 后端:REST API(单人)+ Socket.IO 事件(联机) |
| `engine.py` | 规则引擎:掷骰、走子、跳跃/飞跃、撞机、终点道、胜负 |
| `rooms.py` | 房间系统:4 位数字房间号、座位、AI 补位、断线托管、TTL 清理 |
| `data/board.json` | 棋盘定义:52 格跑道、配色、4 条终点道、起点格 |
| `data/config.json` | 玩法参数(骰子面数、每方棋子数、起飞点数、反弹规则、积分、AI 速度) |
| `static/index.html` | 前端页面(大厅 + 棋盘渲染 + 交互 + 音效) |
| `static/vendor/` | Socket.IO 客户端(本地化,不依赖 CDN) |
| `test_*.py` | 自动化测试(AI 接管 / 全流程对局 / 联机双客户端) |

## 两种模式

### 单人模式(REST)
一名真人 + 1~3 个 AI,不足时 AI 补位。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 游戏页面 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/state` | 当前局面 |
| POST | `/api/new` | 开新局(`{count, names}`) |
| POST | `/api/roll` | 掷骰(多枚可动时返回 `needPieceChoice`) |
| POST | `/api/move` | 执行移动(`{pieceId}`) |
| POST | `/api/ai_turn` | AI 自动回合(掷骰 + 启发式选子) |

### 多人联机(Socket.IO)
4 位数字房间号,房主创建并选总人数,真人不足自动 AI 补位。

**客户端 → 服务端**

| 事件 | 参数 | 说明 |
|---|---|---|
| `create_room` | `{name, maxPlayers}` | 创建房间(房主) |
| `join_room` | `{roomId, name}` | 用 4 位房间号加入 |
| `start_game` | — | 房主开局(仅房主) |
| `roll` | — | 掷骰(仅轮到自己时) |
| `move` | `{pieceId}` | 走子(仅轮到自己时) |
| `restart_game` | — | 房主重开一局 |
| `leave_room` | — | 主动离开 |

**服务端 → 客户端**

| 事件 | 说明 |
|---|---|
| `room_created` | 房间创建成功(含房间号) |
| `room_joined` | 加入房间成功(含座位号) |
| `room_update` | 房间快照(座位 + 对局局面) |
| `dice_rolled` | 掷骰结果(含骰点、是否需选子) |
| `move_done` | 走子完成 |
| `error` | 错误提示(房间不存在/已满/非房主/轮次错误等) |

## 规则

- **棋盘**:15×15 网格,外圈 52 格环形跑道 + 中心十字形终点道(每色 6 格独立编号)+ 中心终点
- **棋子**:每方 4 枚,掷出 **5 或 6** 即可起飞
- **跳格**:落到与棋子**同色**的格子 → 自动往前跳 **4 格**(只跳一次,不连锁)
- **撞机**:落到敌方棋子所在格 → 敌方被击飞回起点
- **终点道**:需精确点数抵达终点,超出则反弹
- **连续 6**:可继续掷
- **胜负**:一方 4 枚棋子全部抵达终点即获胜
- **AI 补位**:真人不足目标人数时,空位由 AI 接管(启发式选子:能到终点 > 能撞机 > 终点道内 > 位置靠前)
- **断线托管**:对局中掉线,该座位转 AI 继续,避免卡死

## 测试
```bash
python3 test_ai_auto.py       # 单人模式 AI 自动接管
python3 test_fullgame.py      # 全流程对局直到分出胜负
python3 test_multiplayer.py   # 联机双客户端流程
```

## 部署
- systemd 托管,端口 3500
- Caddy 反代:`handle_path /flight-chess/*` → `127.0.0.1:3500`

> ⚠️ 前端 API 前缀与 Socket.IO path 均硬编码 `/flight-chess`(对应 Caddy `handle_path` strip 前缀后的路径)。
> 若改用 `handle` 而非 `handle_path`,需同步修改 `static/index.html` 的 `API` 常量与 `io({path})`。

## 已知限制
- 单局内存态,重启清空,无存档/排行榜
- 联机无断线重连(重连需重新加入房间)
