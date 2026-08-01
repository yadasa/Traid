# Backtesting and replay

Traid includes a chart-compatible replay endpoint for evaluating decision workflow and risk assumptions against historical candles.

## Current replay

The current fast replay uses only candles available before each replay step. It derives a recent-drift directional baseline, advances through the selected horizon, and reports:

- ending equity and return;
- win rate;
- maximum drawdown;
- mean trade return;
- timestamped equity records.

This is deliberately lightweight and suitable for UI exploration. It is not represented as a full Kronos strategy backtest.

## No-leak rule

At replay cursor `t`, calculations may consume only rows before `t`. Realized rows beginning at `t` are used solely for scoring. Any future full Kronos walk-forward runner must preserve the same boundary.

## Full walk-forward extension

A production research runner should:

1. load a fixed historical dataset and broker specification;
2. generate a real Kronos forecast at each chosen cursor;
3. apply a separately defined entry/exit strategy;
4. include broker spread, commission, swap, latency, and slippage assumptions;
5. preserve all forecast versions and strategy decisions;
6. evaluate out-of-sample periods and market regimes;
7. report drawdown and tail losses, not only return.

The custom live spread/slippage rejection gate is not part of Traid, but realistic execution costs still belong in research/backtests.

## Interpretation

A replay result is sensitive to timeframe, starting point, horizon, costs, and strategy rules. Avoid optimizing repeatedly on the same test period. Use walk-forward validation and untouched holdout periods.
