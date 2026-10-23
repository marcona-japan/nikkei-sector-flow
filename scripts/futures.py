"""朝の先物・商品・暗号資産チェック（yfinance）。

株価指数先物（日経225先物=CME円建て、NYダウ・ナスダック100・S&P500）、VIX、
商品（WTI原油・金・銀）、ビットコインの現在値と前日比を1枚のカードにする。
"""
from __future__ import annotations

# (表示名, ティッカー, 単位, 小数桁)
ITEMS = [
    ("為替", [
        ("ドル円", "JPY=X", "円", 2),
        ("ユーロ円", "EURJPY=X", "円", 2),
        ("ドルインデックス", "DX-Y.NYB", "", 2),
    ]),
    ("米国債利回り", [
        ("米10年", "^TNX", "%", 3),
        ("米2年", "2YY=F", "%", 3),
    ]),
    ("株価指数先物", [
        ("日経225先物（CME円建て）", "NIY=F", "", 0),
        ("NYダウ先物", "YM=F", "", 0),
        ("ナスダック100先物", "NQ=F", "", 2),
        ("S&P500先物", "ES=F", "", 2),
        ("VIX（恐怖指数）", "^VIX", "", 2),
        ("SOX（米半導体株指数）", "^SOX", "", 2),
    ]),
    ("商品先物", [
        ("WTI原油", "CL=F", "ドル", 2),
        ("金", "GC=F", "ドル", 1),
        ("銀", "SI=F", "ドル", 3),
    ]),
    ("暗号資産", [
        ("ビットコイン（円）", "BTC-JPY", "円", 0),
        ("ビットコイン（ドル）", "BTC-USD", "ドル", 0),
    ]),
]


def quotes() -> dict[str, tuple[float, float]]:
    """ティッカー → (現在値, 前日終値)"""
    import yfinance as yf
    tick = [t for _, rows in ITEMS for _, t, _, _ in rows]
    df = yf.download(tick, period="7d", interval="1d", progress=False, auto_adjust=False, group_by="ticker", threads=True)
    out = {}
    for t in tick:
        try:
            s = df[t]["Close"].dropna()
            if len(s) >= 2:
                out[t] = (float(s.iloc[-1]), float(s.iloc[-2]))
        except Exception:  # noqa: BLE001
            continue
    return out


def embed() -> dict | None:
    q = quotes()
    if not q:
        return None
    fields = []
    for group, rows in ITEMS:
        ls = []
        for name, t, unit, dp in rows:
            if t not in q:
                continue
            px, pv = q[t]
            chg, pct = px - pv, (px / pv - 1) * 100 if pv else 0
            mark = "🔴" if chg > 0 else "🔵" if chg < 0 else "⚪"
            if unit == "%":  # 利回りは前日差をbp（0.01%）で
                ls.append(f"{mark} {name} **{px:.{dp}f}%**（{chg * 100:+.1f}bp）")
            else:
                ls.append(f"{mark} {name} **{px:,.{dp}f}{unit}**（{chg:+,.{dp}f} / {pct:+.2f}%）")
        if ls:
            fields.append({"name": group, "value": "\n".join(ls), "inline": False})
    return {"title": "🌅 朝のマーケット（為替・金利・先物・商品・BTC）", "color": 0x0B0B0B, "fields": fields,
            "footer": {"text": "Yahoo Finance（遅延あり）。前日比は前営業日の終値比。TOPIX先物はデータ元にないため未掲載。"}}


if __name__ == "__main__":
    import json
    print(json.dumps(embed(), ensure_ascii=False, indent=1))
