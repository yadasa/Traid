from __future__ import annotations

import argparse
import json

import pandas as pd

from .config import Settings
from .forecast import ForecastEngine, ForecastParameters


def serialize(frame: pd.DataFrame) -> list[dict[str, object]]:
    copy = frame.copy()
    copy["timestamp"] = pd.to_datetime(copy["timestamp"], utc=True).map(
        lambda value: value.isoformat()
    )
    return copy.to_dict(orient="records")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Traid live Kronos forecasting")
    subparsers = parser.add_subparsers(dest="command", required=True)

    forecast = subparsers.add_parser("forecast", help="Generate a market projection")
    forecast.add_argument("--symbol", required=True)
    forecast.add_argument("--timeframe", default="5m")
    forecast.add_argument("--lookback", type=int, default=400)
    forecast.add_argument("--pred-len", type=int, default=24)
    forecast.add_argument("--temperature", type=float, default=1.0)
    forecast.add_argument("--top-p", type=float, default=0.9)
    forecast.add_argument("--top-k", type=int, default=0)
    forecast.add_argument("--sample-count", type=int, default=5)
    forecast.add_argument("--output", help="Optional JSON output path")

    serve = subparsers.add_parser("serve", help="Run the FastAPI service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "serve":
        import uvicorn

        from .websocket_guard import install_websocket_disconnect_guard

        install_websocket_disconnect_guard()
        uvicorn.run(
            "traid_live.service:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return

    engine = ForecastEngine(Settings())
    history, projection = engine.forecast(
        ForecastParameters(
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback=args.lookback,
            pred_len=args.pred_len,
            temperature=args.temperature,
            top_p=args.top_p,
            top_k=args.top_k,
            sample_count=args.sample_count,
        )
    )
    payload = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "provider": engine.provider.name,
        "history": serialize(history),
        "projection": serialize(projection),
    }
    rendered = json.dumps(payload, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
