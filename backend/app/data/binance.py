from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass
class BinanceClient:
    base_url: str
    timeout_seconds: int = 30

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        response = requests.get(url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def fetch_top_volume_symbols(self, top_n: int = 10, quote_asset: str = "USDT") -> list[str]:
        tickers = self._get("/api/v3/ticker/24hr")
        eligible = []
        for row in tickers:
            symbol = row.get("symbol", "")
            if not symbol.endswith(quote_asset):
                continue
            if "UP" in symbol or "DOWN" in symbol or "BULL" in symbol or "BEAR" in symbol:
                continue
            quote_volume = float(row.get("quoteVolume", 0.0))
            eligible.append((symbol, quote_volume))
        eligible.sort(key=lambda item: item[1], reverse=True)
        return [symbol for symbol, _ in eligible[:top_n]]

    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> list[list[Any]]:
        rows: list[list[Any]] = []
        cursor = start_time_ms
        while cursor < end_time_ms:
            batch = self._get(
                "/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_time_ms,
                    "limit": limit,
                },
            )
            if not batch:
                break
            rows.extend(batch)
            last_open_time = int(batch[-1][0])
            cursor = last_open_time + INTERVAL_MS[interval]
            if len(batch) < limit:
                break
        return rows

    def fetch_lookback_klines(self, symbol: str, interval: str, days: int) -> list[list[Any]]:
        end_ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        return self.fetch_klines(symbol=symbol, interval=interval, start_time_ms=start_ts, end_time_ms=end_ts)
