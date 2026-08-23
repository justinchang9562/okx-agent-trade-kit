from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.cost_models import FixedFeeModel, FixedSlippageModel, FixedSpreadModel
from backtest.performance import calculate_performance
from backtest.regimes import regime_performance
from backtest.simulator import SimulatedTrade
from backtest.walk_forward import windows
from data.historical import HistoricalDataService
from data.models import AccountSnapshot, Balance, Candle, Instrument, MarketSnapshot
from execution.base_backend import BaseBackend
from risk.daily_limits import DailyRiskState
from risk.execution_revalidation import build_executable_long_plan
from risk.exposure import ExposureSnapshot
from risk.risk_manager import RiskManager
from strategies.registry import PRODUCTION_STRATEGY_VERSION, build_strategy


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


class Backtester:
    def __init__(
        self,
        backend: BaseBackend,
        rules: dict,
        cache_root: Path | None = None,
        *,
        strategy_version: str | None = None,
        allow_research_strategy: bool = False,
    ) -> None:
        self.backend = backend
        self.rules = rules
        self.strategy = build_strategy(
            strategy_version or str(rules.get("strategy_version", PRODUCTION_STRATEGY_VERSION)),
            rules,
            production=not allow_research_strategy,
        )
        self.risk = RiskManager(rules)
        self.cache_root = cache_root or Path("data_cache")

    @staticmethod
    def _instrument(payload: dict[str, Any], symbol: str) -> Instrument:
        rows = _rows(payload)
        if not rows:
            raise RuntimeError("DATA_UNAVAILABLE:instrument")
        row = rows[0]
        return Instrument(
            symbol=symbol, base_currency=str(row["baseCcy"]), quote_currency=str(row["quoteCcy"]),
            min_size=float(row["minSz"]), lot_size=float(row["lotSz"]), tick_size=float(row["tickSz"]),
        )

    def _simulate(
        self, symbol: str, frames: dict[str, tuple[Candle, ...]], instrument: Instrument,
        evaluation_start_ms: int | None = None,
    ) -> tuple[list[SimulatedTrade], float, dict[str, int]]:
        config = self.rules["backtest"]
        initial_equity = float(config["initial_equity"])
        equity = initial_equity
        fee_model = FixedFeeModel(float(config["fee_pct"]))
        spread_model = FixedSpreadModel(float(config["spread_pct"]))
        slippage_model = FixedSlippageModel(float(config["slippage_pct"]))
        primary = frames["1m"]
        trades: list[SimulatedTrade] = []
        daily_pnl = 0.0
        consecutive_losses = 0
        last_trade_ms: int | None = None
        active_day: str | None = None
        skipped_after_execution_revalidation: dict[str, int] = {}
        index = 50
        if evaluation_start_ms is not None:
            index = max(index, next((i for i, item in enumerate(primary) if item.timestamp_ms >= evaluation_start_ms), len(primary)))
        while index < len(primary) - 1:
            candle = primary[index]
            close_time = candle.timestamp_ms + 60_000
            day = datetime.fromtimestamp(close_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if active_day != day:
                active_day, daily_pnl, consecutive_losses = day, 0.0, 0
            visible = {
                "1m": primary[: index + 1],
                "3m": tuple(item for item in frames["3m"] if item.timestamp_ms + 180_000 <= close_time),
                "5m": tuple(item for item in frames["5m"] if item.timestamp_ms + 300_000 <= close_time),
            }
            if len(visible["3m"]) < 50 or len(visible["5m"]) < 50:
                index += 1
                continue
            spread = spread_model.half_spread_pct(close_time)
            market = MarketSnapshot(
                symbol=symbol, timestamp_ms=close_time, price=candle.close,
                bid=candle.close * (1 - spread), ask=candle.close * (1 + spread),
                volume_24h=0.0, candles=visible, instrument=instrument,
            )
            signal = self.strategy.analyze(market)
            account = AccountSnapshot(
                timestamp_ms=close_time, equity_usdt=equity, available_usdt=equity,
                balances=(Balance("USDT", equity, equity),),
            )
            state = DailyRiskState(daily_pnl, consecutive_losses, last_trade_ms, 0)
            exposure = ExposureSnapshot(0.0, 0.0, 0.0, 0.0)
            risk = self.risk.evaluate(
                signal, market, account, state, exposure=exposure,
                evaluation_timestamp_ms=close_time,
            )
            if not risk.approved or signal.suggested_stop is None or signal.suggested_take_profit is None:
                index += 1
                continue
            # Signal is generated on candle close; execution begins at the next candle open.
            execution_bar = primary[index + 1]
            buy_slip = slippage_model.pct(execution_bar.timestamp_ms, "buy")
            entry_reference = execution_bar.open * (1 + spread)
            entry = entry_reference * (1 + buy_slip)
            execution_market = MarketSnapshot(
                symbol=symbol,
                timestamp_ms=execution_bar.timestamp_ms,
                price=execution_bar.open,
                bid=execution_bar.open * (1 - spread),
                ask=entry,
                volume_24h=0.0,
                candles=visible,
                instrument=instrument,
            )
            revalidation = build_executable_long_plan(
                signal,
                execution_market,
                account,
                state,
                self.rules,
                executable_entry=entry,
                exposure=exposure,
                risk_manager=self.risk,
                evaluation_timestamp_ms=execution_bar.timestamp_ms,
            )
            if not revalidation.approved:
                skipped_after_execution_revalidation[revalidation.reason] = (
                    skipped_after_execution_revalidation.get(revalidation.reason, 0) + 1
                )
                index += 1
                continue
            sizing = revalidation.sizing
            exit_price = primary[-1].close
            exit_reference = exit_price * (1 - spread)
            exit_index = len(primary) - 1
            outcome = "TIME_EXIT"
            for future_index in range(index + 1, len(primary)):
                future = primary[future_index]
                # If both touch in one candle, use the adverse outcome to avoid optimistic ordering.
                if future.low <= revalidation.stop:
                    exit_reference = revalidation.stop * (1 - spread)
                    exit_index = future_index
                    outcome = "STOP"
                    break
                if future.high >= revalidation.take_profit:
                    exit_reference = revalidation.take_profit * (1 - spread)
                    exit_index = future_index
                    outcome = "TAKE_PROFIT"
                    break
            sell_slip = slippage_model.pct(primary[exit_index].timestamp_ms, "sell")
            exit_price = exit_reference * (1 - sell_slip)
            gross = (exit_price - entry) * sizing.quantity
            fees = fee_model.cost(entry, exit_price, sizing.quantity)
            slippage_cost = (
                (entry - entry_reference) + (exit_reference - exit_price)
            ) * sizing.quantity
            pnl = gross - fees
            exit_time = primary[exit_index].timestamp_ms + 60_000
            trades.append(SimulatedTrade(
                entry_timestamp_ms=execution_bar.timestamp_ms,
                exit_timestamp_ms=exit_time, entry=entry, exit=exit_price,
                size=sizing.quantity, pnl=pnl, fees=fees,
                slippage_cost=slippage_cost, outcome=outcome,
            ))
            equity += pnl
            daily_pnl += pnl
            consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
            last_trade_ms = exit_time
            index = exit_index + 1
        return trades, initial_equity, skipped_after_execution_revalidation

    def run(self, symbol: str, days: int = 7) -> dict[str, Any]:
        service = HistoricalDataService(
            self.backend, self.cache_root, int(self.rules["backtest"].get("page_size", 100))
        )
        frames: dict[str, tuple[Candle, ...]] = {}
        integrity: dict[str, Any] = {}
        for timeframe in ("1m", "3m", "5m"):
            frames[timeframe], report = service.fetch(
                symbol, timeframe, days, bool(self.rules["backtest"].get("cache_enabled", True))
            )
            integrity[timeframe] = report.__dict__
        instrument = self._instrument(self.backend.get_instrument(symbol), symbol)
        trades, initial_equity, skipped = self._simulate(symbol, frames, instrument)
        config = self.rules["backtest"]
        return {
            "symbol": symbol, "source": "OKX_DEMO_MCP_HISTORICAL_OHLCV_PAGINATED",
            "days": days, "bars_1m": len(frames["1m"]), "data_integrity": integrity,
            "assumptions": {
                "fee_model": "FixedFeeModel", "fee_pct": float(config["fee_pct"]),
                "spread_model": "FixedSpreadModel", "spread_pct": float(config["spread_pct"]),
                "slippage_model": "FixedSlippageModel", "slippage_pct": float(config["slippage_pct"]),
                "execution_timing": "NEXT_BAR_OPEN", "same_bar_stop_target": "STOP_FIRST_CONSERVATIVE",
            },
            "risk_manager": "PRODUCTION_RISK_MANAGER_WITH_HISTORICAL_STATE",
            "skipped_after_execution_revalidation": sum(skipped.values()),
            "skip_reason_breakdown": skipped,
            "performance": calculate_performance(trades, initial_equity),
            "regime_performance": regime_performance(trades, frames["1m"], initial_equity),
            "trades": [trade.__dict__ for trade in trades],
        }

    def walk_forward(self, symbol: str, days: int = 7) -> dict[str, Any]:
        service = HistoricalDataService(
            self.backend, self.cache_root, int(self.rules["backtest"].get("page_size", 100))
        )
        frames = {timeframe: service.fetch(symbol, timeframe, days)[0] for timeframe in ("1m", "3m", "5m")}
        instrument = self._instrument(self.backend.get_instrument(symbol), symbol)
        setup = self.rules["backtest"]["walk_forward"]
        result = []
        for window in windows(
            len(frames["1m"]), int(setup["train_bars"]), int(setup["test_bars"]), int(setup["step_bars"])
        ):
            primary = frames["1m"][window.train_start:window.test_end]
            start_ms, end_ms = primary[0].timestamp_ms, primary[-1].timestamp_ms
            sliced = {"1m": primary}
            for timeframe in ("3m", "5m"):
                sliced[timeframe] = tuple(item for item in frames[timeframe] if start_ms <= item.timestamp_ms <= end_ms)
            evaluation_start = frames["1m"][window.test_start].timestamp_ms
            trades, initial, skipped = self._simulate(
                symbol, sliced, instrument, evaluation_start_ms=evaluation_start,
            )
            result.append({
                "window": window.__dict__, "train_role": "WARMUP_AND_FIXED_RULE_EVALUATION_NO_TUNING",
                "out_of_sample_start_ms": evaluation_start,
                "out_of_sample_end_ms": frames["1m"][window.test_end - 1].timestamp_ms,
                "performance": calculate_performance(trades, initial),
                "skipped_after_execution_revalidation": sum(skipped.values()),
                "skip_reason_breakdown": skipped,
            })
        return {"symbol": symbol, "days": days, "method": "ROLLING_WALK_FORWARD_NO_OPTIMIZER",
                "windows": result}
