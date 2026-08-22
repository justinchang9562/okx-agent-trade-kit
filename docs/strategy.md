# Rule Scalping Strategy v1

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
