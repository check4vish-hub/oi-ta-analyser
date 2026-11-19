"""
Fetch live derivatives data from the public NSE India website.

NSE has no official free API. The website's own JSON endpoints work from a
normal home / office internet connection once the session has picked up the
cookies that nseindia.com sets on its HTML pages. They are often blocked from
cloud servers, so if fetching fails, download the option chain CSV from
https://www.nseindia.com/option-chain and pass it with --oi-csv instead.
"""
from __future__ import annotations

import time
from datetime import datetime

import requests

BASE_URL = "https://www.nseindia.com"

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/option-chain",
}


class NSEError(RuntimeError):
    pass


class NSEClient:
    def __init__(self, timeout: int = 15, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warmed_up = False

    # ------------------------------------------------------------------ utils
    def _warm_up(self) -> None:
        """Visit the HTML pages first so NSE sets its anti-bot cookies."""
        if self._warmed_up:
            return
        for page in ("/", "/option-chain"):
            try:
                self.session.get(BASE_URL + page, timeout=self.timeout)
            except requests.RequestException:
                pass
        self._warmed_up = True

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        self._warm_up()
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.get(BASE_URL + path, params=params, timeout=self.timeout)
                if r.status_code == 200 and r.text.strip():
                    data = r.json()
                    if data:  # NSE returns {} when cookies are missing
                        return data
                last_err = NSEError(f"HTTP {r.status_code} / empty response from {path}")
            except (requests.RequestException, ValueError) as e:
                last_err = e
            # refresh cookies and back off before retrying
            self._warmed_up = False
            time.sleep(1.5 * attempt)
            self._warm_up()
        raise NSEError(f"Could not fetch {path}: {last_err}")

    @staticmethod
    def is_index(symbol: str) -> bool:
        return symbol.upper() in INDEX_SYMBOLS

    # ------------------------------------------------------------- endpoints
    def get_expiries(self, symbol: str) -> list[str]:
        """Expiry dates (as 'DD-Mon-YYYY' strings), nearest first."""
        data = self._get_json("/api/option-chain-contract-info", {"symbol": symbol.upper()})
        expiries = data.get("expiryDates", [])
        return sorted(expiries, key=lambda d: datetime.strptime(d, "%d-%b-%Y"))

    def get_option_chain_raw(self, symbol: str, expiry: str | None = None) -> dict:
        """Raw option chain JSON for one expiry (nearest expiry by default).

        Tries NSE's current 'option-chain-v3' endpoint first and falls back to
        the older endpoints that some NSE deployments still serve.
        """
        symbol = symbol.upper()
        kind = "Indices" if self.is_index(symbol) else "Equity"
        try:
            if expiry is None:
                expiry = self.get_expiries(symbol)[0]
            return self._get_json(
                "/api/option-chain-v3", {"type": kind, "symbol": symbol, "expiry": expiry}
            )
        except (NSEError, IndexError):
            legacy = "/api/option-chain-indices" if kind == "Indices" else "/api/option-chain-equities"
            data = self._get_json(legacy, {"symbol": symbol})
            if expiry:
                data["_requested_expiry"] = expiry
            return data

    def get_futures_raw(self, symbol: str) -> dict:
        """Quote of all derivative contracts (used for futures OI build-up).

        NSE moved this to its 'NextApi' quote service in 2026; the older
        /api/quote-derivative address is kept as a fallback.
        """
        try:
            return self._get_json(
                "/api/NextApi/apiClient/GetQuoteApi",
                {"functionName": "getSymbolDerivativesData", "symbol": symbol.upper()},
            )
        except NSEError:
            return self._get_json("/api/quote-derivative", {"symbol": symbol.upper()})
