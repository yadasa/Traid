# Forecasting

## Inputs

Kronos receives chronological completed candles with:

```text
open, high, low, close, volume, amount
```

The active forming candle is displayed but excluded from model input. Historical and future timestamps are supplied separately, using provider-aware schedules.

## Basic mode

Basic mode generates the normal Kronos path and displays:

- active forecast at full visual strength;
- previous forecast at 67%;
- older forecast at 33%;
- no additional historical paths.

A new path morphs from the currently rendered path over 333 ms. The UI supports both OHLC candlesticks and close-price lines.

## Advanced Forecast mode

Advanced mode must be toggled by the user. It performs repeated stochastic forecasts and calculates:

- median OHLCV path;
- p25/p75 and p10/p90 paths;
- bullish probability by candle;
- visual confidence decay;
- direction flips;
- projected-move magnitude changes;
- timing shifts;
- predicted-volatility changes;
- path similarity and stability;
- candle-level directional consensus;
- neighboring-timeframe consensus;
- cross-market context.

Advanced mode is slower because it performs multiple real model inferences. The number of paths is configurable from 3 to 25.

## Revision interpretation

A major revision means the new path differs materially from the previous path. It does **not** mean the new forecast is correct. Revision severity and confidence must be interpreted alongside historical accuracy for the same symbol, timeframe, and horizon.

## Forecast scoring

As target candles close, Traid stores:

- absolute and percentage close error;
- directional correctness relative to the forecast's last input close;
- whether actual close landed within predicted low/high;
- high and low errors;
- volume percentage error when actual volume is non-zero.

Scores are aggregated overall and by forecast horizon. This prevents a strong next-candle result from hiding poor long-horizon performance.

## Multi-timeframe context

Traid reads the most recent persisted forecast for the selected timeframe, one adjacent lower timeframe, and one adjacent higher timeframe. It reports direction and agreement but does not silently blend or overwrite the raw Kronos path.

## Known limitations

- A sampled percentile range is empirical, not a formally calibrated probability interval.
- Forecasts can be unstable around regime changes and news.
- Longer horizons generally carry more uncertainty.
- Different brokers can produce different CFD candles.
- Historical accuracy does not guarantee future accuracy.
