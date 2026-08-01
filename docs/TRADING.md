# Trading and risk

## Default posture

Trading is disabled by default and starts in paper mode. Paper requests run broker `order_check` validation but do not call `order_send`.

Live mode requires:

- `TRAID_TRADING_ENABLED=true`;
- `TRAID_TRADING_MODE=live`;
- MT5 AutoTrading enabled;
- authenticated administrator access;
- explicit `confirm_live=true` for entry/close requests;
- risk engine approval;
- MT5 broker preflight approval.

## Position sizing

Risk-percent sizing uses:

- account equity;
- requested percentage risk;
- entry-to-stop distance;
- symbol tick size and tick value;
- broker minimum/maximum/step volume;
- Traid maximum lot setting.

The calculated size is rounded down to a broker-supported step. The order ticket previews lots, estimated loss at Stop Loss, and risk/reward.

## Hard controls

Persistent controls include:

- maximum daily loss;
- maximum weekly drawdown;
- maximum simultaneous open risk;
- maximum consecutive losses;
- maximum lot size;
- maximum positions globally and per symbol;
- required Stop Loss;
- emergency trading disable/resume;
- optional close-all.

The previously discussed custom spread/slippage/market-condition gate is intentionally omitted. Traid does not block based on a custom spread threshold, volatility spike, rollover window, quote comparison, or forecast state. MT5 still applies broker-side validation and execution behavior.

## Orders

Supported execution actions:

- market buy/sell;
- buy/sell limit;
- buy/sell stop;
- OCO pairs;
- cancel pending order;
- partial or full close;
- modify Stop Loss/Take Profit;
- move to break-even;
- close all Traid-managed positions.

Traid manages only positions/orders with its configured magic number.

## Trailing Stop methods

- **Fixed:** constant price-unit distance.
- **Percent:** percentage of current Bid/Ask.
- **ATR:** configurable multiple of recent true range.
- **Candle:** recent low for buys or high for sells.

Trailing state is persisted. The most recently accepted Stop Loss remains at the broker if Traid disconnects, but application-managed movement pauses until service resumes.

## Forecast boundary

Forecasts are attached to journal records for later evaluation. Forecast changes never automatically:

- open a position;
- close a position;
- reverse direction;
- resize volume;
- move SL/TP;
- change trailing behavior.

Any future automation should be implemented as an explicit strategy layer with independent tests and risk approval.
