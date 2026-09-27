"""日経225 × 東証33業種 の資金移動（売買代金シェア）を集計する。

1日を6区間（前場 寄り/中盤/引け、後場 寄り/中盤/大引け）に分け、
区間ごとの業種別売買代金シェアを docs/data/*.json に書き出す。
引け後の実行では LINE に日次レポートを送る。

売買代金は Yahoo Finance の5分足から「代表値(高+安+終)/3 × 出来高」で概算。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import constituents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
STATE = ROOT / "data" / "line_sent.txt"
JST = timezone(timedelta(hours=9))
DELAY_MIN = 20  # Yahooの東証データ遅延
BASE_DAYS = 20  # 比較基準＝過去20営業日の平均シェア
PAGE_URL = os.environ.get("PAGE_URL", "")

# (key, 表示名, 開始, 終了)  ※終了は含まない。大引けは引けの板寄せを含むよう16:00まで
WINDOWS = [
    ("am_open", "前場 寄り", "09:00", "09:30"),
    ("am_mid", "前場 中盤", "09:30", "11:00"),
    ("am_close", "前場 引け", "11:00", "11:30"),
    ("pm_open", "後場 寄り", "12:30", "13:00"),
    ("pm_mid", "後場 中盤", "13:00", "15:00"),
    ("pm_close", "大引け", "15:00", "16:00"),
]
WIN_END_LABEL = {"pm_close": "15:30"}


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------- データ取得
def yf_download(tickers: list[str], **kw) -> pd.DataFrame:
    import yfinance as yf

    last = None
    for i in range(3):
        try:
            df = yf.download(tickers, progress=False, auto_adjust=False, threads=True,
                             group_by="column", **kw)
            if not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"Yahoo Financeから取得できませんでした: {last}")


def to_long(df: pd.DataFrame) -> pd.DataFrame:
    """yfinanceのワイド形式 → (ts, ticker, open..volume) の縦持ち"""
    s = df.stack(level=1, future_stack=True).reset_index()
    s.columns = ["ts", "ticker"] + [str(c).lower().replace(" ", "_") for c in s.columns[2:]]
    return s.dropna(subset=["close", "volume"])


def fetch(codes: list[str]):
    tickers = [f"{c}.T" for c in codes]
    daily = to_long(yf_download(tickers, period="3mo", interval="1d"))
    intra = to_long(yf_download(tickers, period="5d", interval="5m"))
    for d in (daily, intra):
        d["code"] = d["ticker"].str.replace(".T", "", regex=False)
    intra["ts"] = pd.to_datetime(intra["ts"], utc=True).dt.tz_convert(JST)
    daily["date"] = pd.to_datetime(daily["ts"]).dt.date
    return daily, intra


# ---------------------------------------------------------------- 集計
def compute(cons: pd.DataFrame, daily: pd.DataFrame, intra: pd.DataFrame,
            target: date, now: datetime) -> dict | None:
    cons = cons.copy()
    sec_of = dict(zip(cons["code"], cons["sector"]))
    name_of = dict(zip(cons["code"], cons["name"]))
    sectors = list(dict.fromkeys(cons.sort_values("s33_code")["sector"]))

    it = intra[intra["ts"].dt.date == target].copy()
    if it.empty:
        return None
    it["sector"] = it["code"].map(sec_of)
    it = it.dropna(subset=["sector"])
    it["value"] = (it["high"] + it["low"] + it["close"]) / 3 * it["volume"]
    it["min"] = it["ts"].dt.hour * 60 + it["ts"].dt.minute

    # 基準: 過去20営業日の業種別シェア平均
    dd = daily[daily["date"] < target].copy()
    dd["sector"] = dd["code"].map(sec_of)
    dd = dd.dropna(subset=["sector"])
    dd["value"] = dd["close"] * dd["volume"]
    days = sorted(dd["date"].unique())[-BASE_DAYS:]
    dd = dd[dd["date"].isin(days)]
    by_day = dd.groupby(["date", "sector"])["value"].sum().unstack(fill_value=0)
    base_share = (by_day.div(by_day.sum(axis=1), axis=0) * 100).mean()
    base_total = by_day.sum(axis=1).mean() if len(by_day) else np.nan

    prev_close = (daily[daily["date"] < target].sort_values("date")
                  .groupby("code")["close"].last())

    now_min = now.hour * 60 + now.minute if now.date() == target else 24 * 60
    windows, per_win = [], []
    for key, label, s, e in WINDOWS:
        w = it[(it["min"] >= hm(s)) & (it["min"] < hm(e))]
        end_min = hm(WIN_END_LABEL.get(key, e))
        state = "done" if now_min >= end_min + DELAY_MIN else ("partial" if len(w) else "pending")
        if state != "pending" and w.empty:
            state = "pending"
        tot = float(w["value"].sum())
        windows.append({"key": key, "label": label, "range": f"{s}–{WIN_END_LABEL.get(key, e)}",
                        "state": state, "total": round(tot)})
        per_win.append(w)

    # 株価: 最新値 vs 前日終値
    last_px = it.sort_values("ts").groupby("code")["close"].last()
    ret = ((last_px / prev_close.reindex(last_px.index)) - 1) * 100

    day_tot = float(it["value"].sum())
    out_sectors = []
    for sec in sectors:
        codes = [c for c in cons["code"] if sec_of[c] == sec]
        shares, values, tops = [], [], []
        for w, win in zip(per_win, windows):
            if win["state"] == "pending":
                shares.append(None), values.append(None), tops.append([])
                continue
            ws = w[w["sector"] == sec]
            v = float(ws["value"].sum())
            shares.append(round(v / win["total"] * 100, 3) if win["total"] else None)
            values.append(round(v))
            top = ws.groupby("code")["value"].sum().sort_values(ascending=False).head(3)
            tops.append([[c, name_of.get(c, c), round(float(x))] for c, x in top.items()])
        sv = float(it.loc[it["sector"] == sec, "value"].sum())
        r = ret.reindex(codes).dropna()
        out_sectors.append({
            "name": sec,
            "n": len(codes),
            "base": round(float(base_share.get(sec, 0.0)), 3),
            "shares": shares,
            "values": values,
            "tops": tops,
            "day_share": round(sv / day_tot * 100, 3) if day_tot else None,
            "day_value": round(sv),
            "ret": round(float(r.mean()), 2) if len(r) else None,
        })

    final = all(w["state"] == "done" for w in windows)
    return {
        "date": target.isoformat(),
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "final": final,
        "base_days": len(days),
        "base_total": None if np.isnan(base_total) else round(float(base_total)),
        "day_total": round(day_tot),
        "windows": windows,
        "sectors": out_sectors,
        "note": "売買代金はYahoo Financeの5分足から概算（約20分遅れ）。基準は過去20営業日の業種別シェア平均。",
    }


# ---------------------------------------------------------------- 出力
def write(data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (OUT / f"{data['date']}.json").write_text(body)
    (OUT / "latest.json").write_text(body)
    idx_p = OUT / "index.json"
    idx = json.loads(idx_p.read_text()) if idx_p.exists() else []
    if data["date"] not in idx:
        idx.append(data["date"])
    idx = sorted(idx)[-250:]
    idx_p.write_text(json.dumps(idx))


def yen(v: float) -> str:
    if v >= 1e12:
        return f"{v / 1e12:.2f}兆円"
    return f"{v / 1e8:,.0f}億円"


def line_text(d: dict) -> str:
    dt = datetime.fromisoformat(d["date"])
    wd = "月火水木金土日"[dt.weekday()]
    secs = [s for s in d["sectors"] if s["day_share"] is not None]
    for s in secs:
        s["_dev"] = s["day_share"] - s["base"]
    up = sorted(secs, key=lambda s: -s["_dev"])[:5]
    dn = sorted(secs, key=lambda s: s["_dev"])[:5]

    def row(i, s):
        r = "" if s["ret"] is None else f" 株価{s['ret']:+.1f}%"
        return f"{i}. {s['name']} {s['day_share']:.1f}%（{s['_dev']:+.1f}pt）{r}"

    lines = [f"📊 日経225 資金移動（東証33業種）{dt.month}/{dt.day}({wd})"]
    tot = f"売買代金(概算) {yen(d['day_total'])}"
    if d.get("base_total"):
        tot += f"（20日平均比 {(d['day_total'] / d['base_total'] - 1) * 100:+.0f}%）"
    lines += [tot, "", "▲ 資金流入（20日平均シェア比）"]
    lines += [row(i + 1, s) for i, s in enumerate(up)]
    lines += ["", "▼ 資金流出"]
    lines += [row(i + 1, s) for i, s in enumerate(dn)]

    # 前場 → 後場 の移動
    def half(idx):
        tot = sum(d["windows"][i]["total"] for i in idx)
        return {s["name"]: sum((s["values"][i] or 0) for i in idx) / tot * 100 if tot else 0
                for s in d["sectors"]}
    am, pm = half([0, 1, 2]), half([3, 4, 5])
    mv = sorted(((pm[k] - am[k], k) for k in am), reverse=True)
    lines += ["", "⏱ 前場→後場",
              "向かった先: " + "、".join(f"{k}({v:+.1f}pt)" for v, k in mv[:3]),
              "抜けた先: " + "、".join(f"{k}({v:+.1f}pt)" for v, k in mv[::-1][:3])]
    if PAGE_URL:
        lines += ["", f"🔗 6区間の流れ: {PAGE_URL}"]
    return "\n".join(lines)


def send_line(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN 未設定のため送信スキップ")
        return
    r = requests.post("https://api.line.me/v2/bot/message/broadcast",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"messages": [{"type": "text", "text": text[:4900]}]}, timeout=30)
    print("LINE:", r.status_code, r.text[:200])
    r.raise_for_status()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="集計日 YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--line", choices=["auto", "force", "off"], default="auto",
                    help="auto=引け後に1日1回 / force=必ず送る / off=送らない")
    a = ap.parse_args()

    now = datetime.now(JST)
    target = date.fromisoformat(a.date) if a.date else now.date()
    cons = constituents.load()
    daily, intra = fetch(cons["code"].tolist())
    data = compute(cons, daily, intra, target, now)
    if data is None:
        print(f"{target} の場中データがありません（休場日の可能性）。終了します。")
        return
    write(data)
    print(f"書き出し: {target} final={data['final']} 合計{yen(data['day_total'])}")

    text = line_text(data)
    print("----- LINE本文 -----\n" + text + "\n--------------------")
    sent = STATE.read_text().strip() if STATE.exists() else ""
    if a.line == "force" or (a.line == "auto" and data["final"] and sent != data["date"]):
        send_line(text)
        if os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(data["date"])


if __name__ == "__main__":
    main()
