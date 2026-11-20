#!/usr/bin/env python3
"""
One-time setup for ICICI Direct Breeze (gives the tracker instant intraday history).

Before running this you need a Breeze "app" (free):
  1. Go to https://api.icicidirect.com/apiuser/home and log in with your ICICI Direct ID.
  2. Register a new app. For the redirect URL you can enter  http://127.0.0.1
  3. Note the API Key and Secret Key it shows.

Then run:  python breeze_setup.py   (or double-click "Setup Breeze.bat")

Your key and secret are saved only on this computer, in data/breeze_config.json.
That folder is excluded from GitHub. Never share the secret with anyone.
"""
from __future__ import annotations

import subprocess
import sys

from stock_analyser import breeze_source as bz


def read_clipboard() -> str:
    """Text currently on the Windows/Mac clipboard ('' if it can't be read)."""
    cmds = ([["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]] if sys.platform == "win32"
            else [["pbpaste"]] if sys.platform == "darwin" else [["xclip", "-o", "-selection", "clipboard"]])
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
            if out.strip():
                return out.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:  # fallback: Tk's clipboard (comes with standard Python)
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        txt = r.clipboard_get()
        r.destroy()
        return txt.strip()
    except Exception:
        return ""


def masked(v: str) -> str:
    return f"{v[:2]}{'*' * max(len(v) - 4, 0)}{v[-2:]}  ({len(v)} characters)" if len(v) > 4 else "*" * len(v)


def ask_secret() -> str:
    """Get the secret key from the clipboard (Windows can't paste into hidden input)."""
    while True:
        input("\nOn the ICICI 'View Apps' page, click the copy icon next to the Secret Key,\n"
              "then come back here and press Enter… ")
        secret = read_clipboard()
        if not secret or " " in secret or "\n" in secret:
            print("Couldn't read a key from the clipboard. Click the copy icon again and retry.")
            alt = input("…or type  T  to type it in yourself (it will be visible): ").strip().lower()
            if alt == "t":
                return input("Secret Key: ").strip()
            continue
        ok = input(f"Read from clipboard: {masked(secret)}. Use this? [Y/n] ").strip().lower()
        if ok in ("", "y", "yes"):
            return secret


def main() -> int:
    print(__doc__)
    cfg = bz.load_config()
    if cfg.get("api_key"):
        keep = input(f"A Breeze key is already saved (…{cfg['api_key'][-4:]}). Keep it? [Y/n] ").strip().lower()
        if keep == "n":
            cfg.pop("api_key", None)
            cfg.pop("api_secret", None)
    if not cfg.get("api_key"):
        key = input("Paste your Breeze API Key (right-click to paste), then press Enter: ").strip()
        secret = ask_secret()
        if not key or not secret:
            print("Both the API key and the secret are needed.")
            return 1
        cfg.update({"api_key": key, "api_secret": secret})
        cfg.pop("session_token", None)
        cfg.pop("session_date", None)
        bz.save_config(cfg)
        print(f"Saved to {bz.CONFIG}")

    try:
        client = bz.connect(interactive=True)
    except bz.BreezeError as e:
        print(f"[!] {e}")
        return 1
    print("\nLogged in. Testing with NIFTY futures…")
    try:
        by_day = bz.history(client, "NIFTY", days=2, minutes=15)
        if by_day:
            d = max(by_day)
            r = by_day[d][-1]
            print(f"  OK – {len(by_day)} day(s) of data. Latest bar {r['time']}: price {r['price']}, OI {r['oi']:,.0f}")
        else:
            print("  Logged in, but no candles came back yet (holiday or before market open?).")
    except Exception as e:
        print(f"  Login worked but the data test failed: {str(e)[:200]}")
        return 1
    print("\nAll set. Double-click 'Run Tracker.bat' to use Breeze data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
