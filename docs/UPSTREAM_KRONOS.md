# Upstream Kronos

Traid is built on the open-source [Kronos](https://github.com/shiyu-coder/Kronos) foundation-model project by its upstream authors.

Kronos is a decoder-only model family designed for financial candlestick sequences. Its tokenizer quantizes multidimensional OHLCV/amount values into hierarchical tokens, and its autoregressive model generates future candle-token sequences that are decoded back into numerical values.

Default Traid configuration:

```text
Model: NeoQuasar/Kronos-small
Tokenizer: NeoQuasar/Kronos-Tokenizer-base
Context: 512 candles
```

Available upstream models include Kronos-mini, Kronos-small, Kronos-base, and a non-open Kronos-large checkpoint. Consult the upstream repository and model cards for current details, licenses, papers, fine-tuning examples, and citations.

Traid preserves the repository's license file. The forecasting model is only one component of this application; Traid adds live provider adapters, persistence, evaluation, uncertainty sampling, responsive visualization, risk controls, journaling, replay, authentication, and MT5 execution.
