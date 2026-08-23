# Rule Scalping Strategy v1

The frozen production registry name is `scalping_v1_baseline`. Production
construction rejects any unpromoted registry entry.

The 1m timeframe produces entries while 3m and 5m confirm that the higher-timeframe
bias is not bearish. Deterministic inputs are EMA9/21/50, VWAP, RSI14, MACD
12/26/9, ATR14, volume MA/ratio, and recent market structure.

The ten-point long score allocates two points to EMA trend, two to price above
VWAP, one to reasonable RSI, one to bullish MACD, two to sufficient volume, and
one to each non-bearish confirmation. The configured minimum is seven and
`signal_strength` is score/10. This normalized rule score is not a probability
of winning. A long stop uses both ATR and the recent swing low; the
target is derived from stop distance and minimum risk/reward.

Bearish spot output is `BEARISH`/`HOLD`, never open-short. No ML, discretionary
LLM prediction, guarantee of profit, or order submission exists in this layer.

`scalping_v1_quality_research` adds an independent research-only quality score
from trend, volume, volatility, spread and structure. It does not change the
baseline side, score, signal strength or production entry gate. Research config
requires BTC/ETH/SOL, multiple 7/30/90-day windows, rolling out-of-sample
evaluation, base/1.5x/2x costs and realized-range regime buckets. Automatic
promotion is disabled; without robust cross-symbol evidence the baseline stays
in production.
