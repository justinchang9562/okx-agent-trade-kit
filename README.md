# OKX Agent Trade Kit — Core v0.2.1

这是一个确定性的分钟级现货短线交易系统；当前运行路径使用已配置的 **OKX 模拟交易 MCP**。它遵循路径 B：Codex 仅作为控制与开发界面，交易决策由本地 Python 流水线完成。

```text
用户 -> Codex -> 交易代理 -> 市场数据 -> 指标 -> 策略
     -> 风控 -> 仓位规模 -> 决策 -> 交易计划 -> 审批
     -> 订单管理器 -> OKX 适配器 -> 模拟 MCP / CLI / 原生 API -> OKX
```

系统基于规则且可解释；它不承诺盈利，也不是高频交易（HFT）。策略不能下单，风控可以否决任何交易，现货看跌信号不会转化为合成做空，实盘交易处于锁定状态。

## 当前后端状态

- MCP：主后端；启动现有的 `okx-mcp-demo-trade` stdio 包装器。支持模拟市场、账户、现货查询及受保护的下单方法。
- CLI：可检测到已安装的公开模拟市场路径；不会用于私有执行，因此状态显示为 `PARTIALLY_WORKING`。
- 原生 REST/WebSocket：仅作为扩展边界，状态为 `NOT_CONFIGURED`。
- 实盘：`LOCKED`；未启用任何实盘执行器实现。

仓库不存储任何凭据。MCP 包装器仍从现有的本地安全配置中读取独立的模拟盘凭据。

## 安装

需要 Python 3.11+。使用已安装的 `uv`：

```bash
uv sync --extra dev
cd frontend && npm ci && npm run build && cd ..
```

核心配置位于 `config/trading_rules.yaml`、`config/symbols.yaml` 和 `config/environments.yaml`。默认值为模拟盘、现货、以 1 分钟周期入场并由 3/5 分钟周期确认、显式审批、单笔风险 0.5%、最大持仓名义价值 10%、单日 3% 熔断，以及连续三次亏损后暂停。这些是工程默认设置，并非投资建议。

## 命令

```bash
uv run python -m trading_agent status
uv run python -m trading_agent health
uv run python -m trading_agent scan
uv run python -m trading_agent analyze BTC-USDT
uv run python -m trading_agent dry-run BTC-USDT
uv run python -m trading_agent positions
uv run python -m trading_agent orders
uv run python -m trading_agent pending
uv run python -m trading_agent approve PLAN_ID
uv run python -m trading_agent approve PLAN_ID --confirm "CONFIRM DEMO ORDER"
uv run python -m trading_agent reject PLAN_ID
uv run python -m trading_agent trades
uv run python -m trading_agent recover
uv run python -m trading_agent backtest BTC-USDT
uv run python -m trading_agent backtest BTC-USDT --days 30
uv run python -m trading_agent walk-forward BTC-USDT --days 7
uv run pytest
```

## 本地网页仪表板 v2.1

完成上述一次性安装后，可通过一条本地命令同时启动 API 与已构建的仪表板：

```bash
uv run python -m trading_agent.web_server
```

打开 `http://127.0.0.1:8000`。支持的服务器仅绑定回环地址，并以单进程/单 worker 运行，使用唯一权威的 `TradingOrchestrator`。若缺少前端构建产物，该命令会先行构建；修改前端源码后，请使用 `uv run python -m trading_agent.web_server --rebuild-frontend`。

页面提供健康状态、账户/敞口、Scanner、Signals、TradePlans、Orders、Positions、Trades、Backtest/Walk-Forward、已脱敏日志、设置及带审计记录的控制界面。每次后端重启后，模拟执行均以 `DISARMED` 状态启动。ARM、代理启动、AUTO 和审批均要求已认证且新鲜的 WebSocket；断开或连接过期时会以关闭失败（fail closed）的方式处理。审批只接受已持久化的 `plan_id`，并调用现有完整 Core 重新验证路径。AUTO DEMO 必须输入明确短语，默认禁用，且重启后绝不恢复。Kill Switch 会停止新入场/AUTO 并解除武装，但不会移除现有的 TP/SL 保护。

实盘仍为 `LIVE_NOT_CONFIGURED / LOCKED`；W8 尚未实现。请参阅[网页仪表板兼容性](docs/web-dashboard-compatibility.md)和 [API/WebSocket v1](docs/web-dashboard-api-v1.md)。

`dry-run` 会获取最新模拟市场数据与账户状态，计算完整计划，但绝不会调用订单提交。`analyze` 会持久化一个带可配置 TTL 的可执行计划。唯一的执行入口是 `approve PLAN_ID`：未提供精确确认语时，仅生成最新的执行预览；提供精确确认语后，系统会重新获取市场/账户状态，并在 `OrderManager` 能够提交订单之前，再次执行陈旧数据、价差、敞口、单日亏损、连续亏损、重复、仓位规模、TTL、价格偏离和提交前滑点等保护检查。最终的委托价、止损和止盈将按照 OKX `tickSz` 做 Decimal 精度量化；最终的风险回报比、数量和风险金额会基于这些可执行价格重新计算。`STOPPED` 运行时状态会在编排层和最终执行器边界阻止一切新入场，但不移除已有保护订单。

回测使用分页、完整性检查过的真实 OKX 历史 OHLCV，配合被忽略的本地缓存、生产级指标/策略/风控/仓位规模、下一根 K 线开盘执行、可注入的手续费/价差/滑点模型，以及同一根 K 线内止损与目标价的保守排序。支持获取 7、30 和 90 天的数据。基础 walk-forward 路径报告滚动的样本外窗口，不进行参数调优。

## 数据、监控与安全

SQLite `trading_agent.db` 存储计划、持久化订单状态转换、受管理仓位、实际/未知成交手续费、信号和交易。钱包资产不属于 Agent 管理仓位。`max_open_positions` 统计 Agent 管理的活动仓位及预留入场生命周期；预留入场的名义价值也会计入预估敞口。钱包敞口由 `max_total_exposure_pct` 单独限制；具有实质数量但无法定价的资产会以 `EXPOSURE_UNKNOWN` 阻止新入场，而不是按零值估算。结构化事件会写入 `logs/trading_agent.log`；这两个运行时文件均未纳入版本控制。

流水线会验证时间戳、数据时效性、缺失数据、价格、OHLC 完整性、K 线数量、价差、止损、目标价、风险回报比、仓位精度、最小数量、账户敞口、受管理仓位、单日亏损、连续亏损、冷却时间、重复计划、审批、模拟盘身份和实盘门禁。提交结果不确定时会被持久化，并按客户端订单 ID 对账，而非盲目重试。已成交入场但未验证保护订单的仓位将变为 `POSITION_UNPROTECTED`。系统会区分明确的本地或交易所拒绝，与可能已到达 OKX 的失败。生命周期状态转换使用原子 CAS；SQLite 仓库和 MCP stdio 通道以有限等待时间串行化共享访问。健康检查会将系统能力与当前交易资格分开报告。发生故障时会产生 HOLD/REJECT，且不会提交订单。

请参阅[架构](docs/architecture.md)、[策略](docs/strategy.md)、[风控措施](docs/risk-management.md)、[执行后端](docs/execution-backends.md)、[命令](docs/commands.md)和[从模拟盘到实盘](docs/demo-to-live.md)。
