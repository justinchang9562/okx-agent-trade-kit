# OKX Operator-Controlled Automatic Trading Agent v0.4.2

这是一个运行在本机、连接 **OKX Demo** 的自动现货交易 Agent。普通用户只需要四个操作：

- START
- PAUSE
- STOP
- FLATTEN ALL & STOP

START 是一次 session-level authorization。通过完整 Preflight 后，Agent 会自动观察实时行情，在
确认的 1m K 线运行确定性策略，并依次经过 Risk Manager、仓位计算、Trade Decision、OrderManager
和 OKX Agent Trade Kit MCP。运行中的合格交易不再要求逐笔人工审批。

    用户 -> Local Dashboard -> AutoTradingSessionController
                                |-> OKX Public WebSocket -> Local Market State
                                |                         -> Strategy -> Risk Manager
                                |                         -> Position Sizing -> Decision
                                |                         -> OrderManager
                                |-> Account / Orders / Fills reconciliation -> Risk Manager
                                                          |
                                                 OKX Agent Trade Kit MCP
                                                          |
                                                       OKX Demo

    Codex / AI -> 解释、分析、总结、配置帮助（不在交易 hot path）

项目不是大型交易平台，也不是 HFT。当前只支持 BTC-USDT、ETH-USDT、SOL-USDT 三个 Spot
交易对；不实现杠杆、Margin、永续、期货、合成做空、多交易所或 Live executor。系统不承诺盈利。

## 会话语义

START 会在一个服务器端原子操作中检查：

- 环境必须为 Demo，Live 必须保持锁定；
- OKX Agent Trade Kit Demo backend 可用且声明支持 attached TP/SL；
- Public WebSocket、ticker、book 和 1m/3m/5m buffers 全部新鲜且已重同步；
- 账户快照新鲜，Risk/Strategy/Execution backend 就绪；
- Kill Switch 未启用；
- 没有待核对的 SUBMISSION_UNKNOWN；
- 没有 POSITION_UNPROTECTED。

全部通过后，内部自动进入 RUNNING / AUTO enabled / Execution ARMED。PAUSED 状态再次点击
START 即 Resume，并重新执行 Preflight。进程重启永远回到 STOPPED / AUTO off / DISARMED。

PAUSE 会立即阻止新入场并取消 Agent pending entry，但保留现有仓位、SL、TP、行情监听、仓位
监控和 reconciliation。STOP 同样阻止新入场并取消 pending entry，同时结束本次自动会话；
它不会强制平仓，也不会移除保护单。

FLATTEN ALL & STOP 只关闭 SQLite 中持久化的 Agent-managed Spot positions。手机或其他客户端
创建的订单、钱包资产和外部持仓会显示为 EXTERNAL 并计入风险，但不会被自动接管、修改、取消
或卖出。每个 close intent 和 client order ID 都会持久化；重复点击不会产生重复卖单。
SUBMISSION_UNKNOWN 只会 reconciliation，禁止 blind retry。只有 managed exposure 确认归零后
才会报告 FLAT，否则保持 FLATTENING / FLATTEN_INCOMPLETE。

## 行情、执行与同步

自动会话的主要市场源是 OKX 官方 Public WebSocket：

- tickers、books5
- candle1m、candle3m、candle5m
- 本地 bounded rolling buffers、重复/乱序过滤、断线重同步和 freshness watchdog

完整策略仅在确认的 1m candle 触发；实时 ticker/book 只用于最新价格、spread、entry deviation、
slippage、freshness 和最终提交前复核。MCP market reads 仍用于 bootstrap、watchdog fallback、
diagnostics 和 research，不会改成高频 full polling。

账户、open orders、recent fills 和 managed positions 默认每 3 秒 single-flight 同步一次。所有交易
写操作继续使用：

    Trading Agent -> OrderManager -> OKX Agent Trade Kit MCP -> OKX Demo

项目没有重新实现 OKX private REST authentication 或原生交易栈。

新提交订单和撤单请求不等待普通 3 秒同步。它们使用单订单 scoped 的 bounded fast lane：
`immediate → 300ms → 1s → 2s`。撤单 ACK 只进入 `CANCEL_REQUESTED`；只有远端明确返回
cancelled 才进入 `CANCELLED`，若 race 中成交则建立托管仓位并立即复核 SL/TP。

Flatten 使用持久化有序 attempts。上一 attempt 只有在远端终态和成交事实都确认后，才允许用
新的 deterministic client ID 对剩余托管数量继续平仓。剩余数量始终来自去重后的持久化退出
成交；仓位完全关闭前不清理保护单。

## Safety Core

v0.3.0 hardened Core 的安全能力全部保留或加强：

- RiskManager final veto、position sizing、单仓/总敞口、每日亏损、连续亏损、最大仓位数；
- spread/slippage/deviation/freshness/duplicate/cooldown guards；
- Decimal tick/lot quantization、TradePlan TTL、最小 RR；
- 每个 Agent entry 必须同时存在正确、active、正确归属、tick-aware 价格一致且数量足够的 SL 和 TP；
- SUBMISSION_UNKNOWN、client-order-ID reconciliation、no blind retry；
- order/fill/protection reconciliation、Agent-managed 与 external wallet separation；
- persistent Kill Switch、startup safe state、backtest isolation、repository safety 和 CI；
- Demo only；Live LOCKED / NOT IMPLEMENTED。

任何 realtime、account、backend、risk 或 reconciliation critical failure 都会 fail closed，阻止新入场
并进入 DEGRADED；已有保护仍保留。

## 安装与运行

需要 Python 3.11+、Node.js 和 uv：

    uv sync --extra dev
    cd frontend
    npm ci
    npm run build
    cd ..
    uv run python -m trading_agent.web_server

打开 http://127.0.0.1:8000。服务只绑定 loopback，并使用单进程、单 worker 和一个权威
TradingOrchestrator。配置文件：

- config/trading_rules.yaml
- config/symbols.yaml
- config/environments.yaml

仓库不保存凭据；前端、API 响应和日志投影都会递归脱敏。

## Advanced / Developer

Scanner、manual TradePlan preview/approval、CLI approval、Backtest、Walk Forward、Research、raw
logs、audit 和 runtime internals 保留用于诊断与开发，但不进入主要用户流程。常用只读命令：

    uv run python -m trading_agent status
    uv run python -m trading_agent health
    uv run python -m trading_agent positions
    uv run python -m trading_agent orders
    uv run python -m trading_agent trades
    uv run python -m trading_agent recover
    uv run python -m trading_agent backtest BTC-USDT --days 7
    uv run python scripts/run_research_evaluation.py

scripts/verify_demo_lifecycle.py 默认只执行 PRECHECK，不会下单。任何真实 Demo lifecycle 都必须等待
用户另行明确授权；常规测试、CI、本轮实现均不提交真实 Demo order。

## 验证

    uv run ruff check .
    uv run pytest -q -m "not integration"
    cd frontend
    npm test -- --run
    npm run build

参阅 [架构](docs/architecture.md)、[API](docs/web-dashboard-api-v1.md)、
[风险管理](docs/risk-management.md)、[执行后端](docs/execution-backends.md) 和
[发布检查](docs/release-checklist.md)。
