"""日曜の「週末相場」チェック（Discord）。

株式・商品市場が閉まっている週末に、24時間取引されている無期限先物（MEXC の指数・商品連動コントラクト）の
価格を取り、金曜の終値（Yahoo Finance の先物）と比べる。いわゆるサンデーダウの代わりの目安。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

MEXC = "https://contract.mexc.com/api/v1/contract/ticker"
# (表示名, MEXCシンボル, 金曜終値を取るYahooティッカー, 単位, 小数桁)
ITEMS = [
    ("株価指数", [
        ("ダウ（週末）", "US30_USDT", "YM=F", "", 0),
        ("ナスダック100（週末）", "NAS100_USDT", "NQ=F", "", 0),
        ("S&P500（週末）", "SPX500_USDT", "ES=F", "", 1),
        ("日経225（週末）", "JP225_USDT", "NIY=F", "", 0),
    ]),
    ("商品", [
        ("金（ゴールド）", "XAU_USDT", "GC=F", "ドル", 1),
        ("銀（シルバー）", "SILVER_USDT", "SI=F", "ドル", 3),
        ("WTI原油", "USOIL_USDT", "CL=F", "ドル", 2),
        ("ブレント原油", "UKOIL_USDT", "BZ=F", "ドル", 2),
    ]),
]


def mexc_prices() -> dict[str, float]:
    d = requests.get(MEXC, timeout=30).json().get("data", [])
    return {x["symbol"]: float(x["lastPrice"]) for x in d if x.get("lastPrice")}


def friday_close(tickers: list[str]) -> dict[str, float]:
    import yfinance as yf
    df = yf.download(tickers, period="10d", interval="1d", progress=False, auto_adjust=False, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            s = df[t]["Close"].dropna()
            out[t] = float(s.iloc[-1])
        except Exception:  # noqa: BLE001
            pass
    return out


def btc() -> tuple[float, float] | None:
    """BTC/円の現在値と24時間前比（%）"""
    try:
        import yfinance as yf
        s = yf.Ticker("BTC-JPY").history(period="3d", interval="1h")["Close"].dropna()
        return float(s.iloc[-1]), (float(s.iloc[-1]) / float(s.iloc[-25]) - 1) * 100
    except Exception:  # noqa: BLE001
        return None


def build() -> dict:
    px = mexc_prices()
    fc = friday_close([y for _, rows in ITEMS for _, _, y, _, _ in rows])
    fields = []
    for group, rows in ITEMS:
        ls = []
        for name, sym, yt, unit, dp in rows:
            if sym not in px:
                continue
            p = px[sym]
            if yt in fc and fc[yt]:
                chg, pct = p - fc[yt], (p / fc[yt] - 1) * 100
                mark = "🔴" if chg > 0 else "🔵" if chg < 0 else "⚪"
                ls.append(f"{mark} {name} **{p:,.{dp}f}{unit}**（金曜終値比 {chg:+,.{dp}f} / **{pct:+.2f}%**）")
            else:
                ls.append(f"⚪ {name} **{p:,.{dp}f}{unit}**")
        if ls:
            fields.append({"name": group, "value": "\n".join(ls), "inline": False})
    b = btc()
    if b:
        mark = "🔴" if b[1] > 0 else "🔵" if b[1] < 0 else "⚪"
        fields.append({"name": "暗号資産", "value": f"{mark} ビットコイン **{b[0]:,.0f}円**（24時間比 **{b[1]:+.2f}%**）", "inline": False})
    return {"title": "🗓️ 週末相場（日曜チェック）", "color": 0x4A3AA7, "fields": fields,
            "description": "週末も取引されている指数・商品連動の先物から、月曜の寄り付きの方向感をチェック。",
            "footer": {"text": "週末価格: MEXCの無期限先物（IG証券のサンデーダウとは少し差があります）／金曜終値: Yahoo Financeの先物"}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    a = ap.parse_args()
    emb = build()
    print(json.dumps(emb, ensure_ascii=False, indent=1))
    if a.notify == "on" and emb["fields"]:
        notify.send([emb], "週末相場", "market")


if __name__ == "__main__":
    main()
