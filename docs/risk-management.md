# Risk management

Risk Manager is fail-closed and has final veto authority. It checks Demo
environment, signal type/score/signal strength, fresh data, account availability,
spread, stop, target, risk/reward, duplicates, daily loss, consecutive losses,
managed open positions, total wallet/account exposure, and cooldown.

`max_open_positions` never counts a non-zero wallet balance. It counts rows
created by this Agent that are still `PARTIALLY_FILLED`, `FILLED`, or
`POSITION_UNPROTECTED`. Existing spot inventory is instead valued independently
against `max_total_exposure_pct`; configured assets that cannot be priced are
reported as unpriced rather than assigned an invented price.

Position sizing risks `equity * risk_per_trade` across the entry-stop distance,
then caps notional by `max_position_pct` and available USDT. Quantity is floored
to OKX's reported lot size and rejected below OKX's reported minimum size.

The daily 3% limit activates `DAILY_KILL_SWITCH_ACTIVE`; three consecutive losses
and the configured exposure cap separately stop new trades. The system never
martingales, automatically averages down, revenge trades, removes stops, or
increases risk to recover a loss. Spread and slippage percentages in config use
percentage points (for example `0.10` means 0.10%).
