"""日経225 × 東証33業種 の資金移動（売買代金シェア）を集計する。

1日を6区間（前場 寄り/中盤/引け、後場 寄り/中盤/大引け）に分け、
区間ごとの業種別売買代金シェアを docs/data/*.json に書き出す。
場中は30分枠ごとに資金流入・流出1位の業種と主な銘柄の株価を、引け後は日次レポートを Discord に送る。

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
STATE = ROOT / "data" / "notified.txt"
WSTATE = ROOT / "data" / "notified_windows.json"  # 区間ごとの場中速報の送信済み記録
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
# 場中速報用の30分刻み（最後の枠は引けの板寄せを含むよう16:00まで）
SLOTS = ([(f"{h:02d}:{m:02d}", f"{h + (m + 30) // 60:02d}:{(m + 30) % 60:02d}") for h in (9, 10, 11) for m in (0, 30)][:5]
         + [(f"{h:02d}:{m:02d}", f"{h + (m + 30) // 60:02d}:{(m + 30) % 60:02d}") for h in (12, 13, 14, 15) for m in (0, 30)][1:7])


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

    # 5分足には引けの板寄せ（クロージング・オークション）が入らないことがあるため、
    # 引け後は日足の売買代金との差額を「大引け」区間に上乗せする
    after_close = now.date() > target or (now.hour * 60 + now.minute) >= hm("15:30") + DELAY_MIN
    closing_added = 0.0
    if after_close:
        td = daily[daily["date"] == target].copy()
        if not td.empty:
            dv = (td["close"] * td["volume"]).groupby(td["code"]).sum()
            iv = it.groupby("code")["value"].sum()
            gap = (dv - iv.reindex(dv.index).fillna(0)).clip(lower=0)
            gap = gap[gap > 0]
            if len(gap):
                last = td.set_index("code")["close"]
                add = pd.DataFrame({
                    "ts": pd.Timestamp(f"{target} 15:30", tz=JST), "code": gap.index,
                    "close": last.reindex(gap.index).values, "value": gap.values,
                })
                add["sector"] = add["code"].map(sec_of)
                add["min"] = hm("15:30")
                it = pd.concat([it, add.dropna(subset=["sector"])], ignore_index=True)
                closing_added = float(gap.sum())

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
            top = ws.groupby("code")["value"].sum().sort_values(ascending=False).head(5)
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

    # 30分刻みの業種別シェアと主な銘柄（場中速報用）
    slots = []
    for st_, en_ in SLOTS:
        en_eff = "16:00" if en_ == "15:30" else en_
        w = it[(it["min"] >= hm(st_)) & (it["min"] < hm(en_eff))]
        tot = float(w["value"].sum())
        state = "done" if now_min >= hm(en_) + DELAY_MIN and tot > 0 else ("partial" if tot > 0 else "pending")
        sh, tp = {}, {}
        if tot > 0:
            g = w.groupby(["sector", "code"])["value"].sum()
            for sec in sectors:
                if sec in g.index.get_level_values(0):
                    gs = g[sec].sort_values(ascending=False)
                    sh[sec] = round(float(gs.sum()) / tot * 100, 3)
                    tp[sec] = [[c, name_of.get(c, c)] for c in gs.head(5).index]
        slots.append({"label": f"{st_}–{en_}", "state": state, "total": round(tot), "shares": sh, "tops": tp})

    # 銘柄ごとの現在値・前日差・前日比（場中速報用）
    stocks = {}
    for c, px in last_px.items():
        pc = prev_close.get(c)
        if pd.isna(px) or pc is None or pd.isna(pc) or not pc:
            continue
        stocks[c] = [name_of.get(c, c), round(float(px), 1), round(float(px - pc), 1),
                     round(float((px / pc - 1) * 100), 2)]

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
        "closing_added": round(closing_added),
        "stocks": stocks,
        "slots": slots,
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


def report(d: dict) -> dict:
    """通知用に要点をまとめる"""
    dt = datetime.fromisoformat(d["date"])
    wd = "月火水木金土日"[dt.weekday()]
    secs = [s for s in d["sectors"] if s["day_share"] is not None]
    for s in secs:
        s["_dev"] = s["day_share"] - s["base"]
    up = [s for s in sorted(secs, key=lambda s: -s["_dev"]) if s["_dev"] >= 0.05][:5]
    dn = [s for s in sorted(secs, key=lambda s: s["_dev"]) if s["_dev"] <= -0.05][:5]

    def row(i, s):
        r = "" if s["ret"] is None else f"　株価{s['ret']:+.1f}%"
        return f"{i}. **{s['name']}** {s['day_share']:.1f}%（{s['_dev']:+.1f}pt）{r}"

    tot = f"売買代金(概算) **{yen(d['day_total'])}**"
    if d.get("base_total"):
        tot += f"（20日平均比 {(d['day_total'] / d['base_total'] - 1) * 100:+.0f}%）"

    def half(idx):
        t = sum(d["windows"][i]["total"] for i in idx)
        return {s["name"]: sum((s["values"][i] or 0) for i in idx) / t * 100 if t else 0
                for s in d["sectors"]}
    am, pm = half([0, 1, 2]), half([3, 4, 5])
    mv = sorted(((pm[k] - am[k], k) for k in am), reverse=True)
    return {
        "title": f"📊 日経225 資金移動（東証33業種）{dt.month}/{dt.day}({wd})",
        "total": tot,
        "in": "\n".join(row(i + 1, s) for i, s in enumerate(up)) or "なし",
        "out": "\n".join(row(i + 1, s) for i, s in enumerate(dn)) or "なし",
        "half": ("向かった先: " + "、".join(f"{k}({v:+.1f}pt)" for v, k in mv[:3] if v > 0) + "\n"
                 "抜けた先: " + "、".join(f"{k}({v:+.1f}pt)" for v, k in mv[::-1][:3] if v < 0)),
    }


def report_text(r: dict) -> str:
    t = [r["title"], r["total"], "", "▲ 資金流入（20日平均シェア比）", r["in"],
         "", "▼ 資金流出", r["out"], "", "⏱ 前場→後場", r["half"]]
    if PAGE_URL:
        t += ["", f"🔗 6区間の流れ: {PAGE_URL}"]
    return "\n".join(t).replace("**", "")


def send_discord(r: dict) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL 未設定のため送信スキップ")
        return False
    embed = {
        "title": r["title"],
        "description": r["total"],
        "color": 0xD03B3B,
        "fields": [
            {"name": "▲ 資金流入（20日平均シェア比）", "value": r["in"][:1024], "inline": False},
            {"name": "▼ 資金流出", "value": r["out"][:1024], "inline": False},
            {"name": "⏱ 前場→後場", "value": r["half"][:1024], "inline": False},
        ],
        "footer": {"text": "売買代金はYahoo Financeの5分足・日足から概算。投資判断はご自身で。"},
    }
    if PAGE_URL:
        embed["url"] = PAGE_URL
        embed["fields"].append({"name": "🔗 6区間の流れ", "value": PAGE_URL, "inline": False})
    res = requests.post(url, json={"username": "セクター資金移動", "embeds": [embed]}, timeout=30)
    print("Discord:", res.status_code, res.text[:200])
    res.raise_for_status()
    return True


def slot_embeds(d: dict, k: int, n_sec: int = 1, n_stock: int = 5) -> list[dict]:
    """30分枠kで売買代金シェアが増えた業種（流入）・減った業種（流出）の上位と、その主な銘柄の株価。
    比較は直前の枠（最初の枠は20日平均）"""
    sl = d["slots"][k]
    prev = d["slots"][k - 1] if k else None
    base = {s["name"]: s["base"] for s in d["sectors"]}
    secs = set(sl["shares"]) | (set(prev["shares"]) if prev else set())
    rows = []
    for sec in secs:
        cur = sl["shares"].get(sec, 0.0)
        pv = prev["shares"].get(sec, 0.0) if prev else base.get(sec)
        if pv is None:
            continue
        rows.append((cur - pv, cur, sec))
    ups = [r for r in sorted(rows, key=lambda r: -r[0]) if r[0] > 0][:n_sec]
    dns = [r for r in sorted(rows, key=lambda r: r[0]) if r[0] < 0][:n_sec]
    stocks = d.get("stocks", {})
    tops_prev = prev["tops"] if prev else {}

    def stock_lines(sec, tops):
        lines = []
        for code, name in tops.get(sec, [])[:n_stock]:
            st = stocks.get(code)
            if not st:
                continue
            _, px, chg, pct = st
            mark = "🔺" if chg > 0 else "🔽" if chg < 0 else "➖"
            pxs = f"{px:,.0f}" if px >= 100 else f"{px:,.1f}"
            chs = f"{chg:+,.0f}" if abs(chg) >= 10 else f"{chg:+,.1f}"
            lines.append(f"{mark} `{code}` {name[:10]} **{pxs}円**（{chs}円 / {pct:+.2f}%）")
        return "\n".join(lines)[:1024] or "銘柄データなし"

    dt = datetime.fromisoformat(d["date"])
    wd = "月火水木金土日"[dt.weekday()]
    prev_label = prev["label"] if prev else "20日平均"
    out = []
    if ups:
        out.append({"title": f"🟥 {sl['label']} 資金流入 1位　{dt.month}/{dt.day}({wd})",
                    "description": f"{prev_label} → {sl['label']} で売買代金シェアが増えた業種と、その枠で売買代金の多い銘柄。株価は現在値・前日差・前日比（約20分遅れ）",
                    "color": 0xD03B3B,
                    "fields": [{"name": f"{i}. {sec}　+{dl:.2f}pt（シェア {cur:.1f}%）",
                                "value": stock_lines(sec, sl["tops"]), "inline": False}
                               for i, (dl, cur, sec) in enumerate(ups, 1)]})
    if dns:
        out.append({"title": f"🟦 {sl['label']} 資金流出 1位",
                    "description": f"シェアが減った業種と、主な銘柄（前の枠で売買代金が多かった銘柄を優先）",
                    "color": 0x2A78D6,
                    "fields": [{"name": f"{i}. {sec}　{dl:.2f}pt（シェア {cur:.1f}%）",
                                "value": stock_lines(sec, tops_prev if tops_prev.get(sec) else sl["tops"]),
                                "inline": False}
                               for i, (dl, cur, sec) in enumerate(dns, 1)],
                    "footer": {"text": "日経225構成銘柄・Yahoo Financeの5分足から概算。投資判断はご自身で。"}})
    if PAGE_URL and out:
        out[0]["url"] = PAGE_URL
    return out


def send_embeds(embeds: list[dict]) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL 未設定のため送信スキップ")
        return False
    res = requests.post(url, json={"username": "セクター資金移動", "embeds": embeds}, timeout=30)
    print("Discord:", res.status_code, res.text[:200])
    res.raise_for_status()
    return True


def notify_slot(d: dict, mode: str) -> None:
    """新しく確定した30分枠があれば場中速報を送る。複数たまっていたら最新の1枠だけ"""
    done = [i for i, x in enumerate(d.get("slots", [])) if x["state"] == "done"]
    if not done:
        return
    k = done[-1]
    st = json.loads(WSTATE.read_text()) if WSTATE.exists() else {}
    sent = st.get(d["date"], [])
    key = d["slots"][k]["label"]
    if mode == "off" or (mode == "auto" and key in sent):
        return
    embs = slot_embeds(d, k)
    if embs and send_embeds(embs):
        sent.append(key)
        cut = (datetime.fromisoformat(d["date"]) - timedelta(days=7)).date().isoformat()
        st = {k2: v for k2, v in st.items() if k2 >= cut}
        st[d["date"]] = sorted(set(sent))
        WSTATE.parent.mkdir(parents=True, exist_ok=True)
        WSTATE.write_text(json.dumps(st, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="集計日 YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--notify", choices=["auto", "force", "off"], default="auto",
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

    try:
        notify_slot(data, a.notify)
    except Exception as e:  # noqa: BLE001  場中速報の失敗で日次処理を止めない
        print("場中速報の送信失敗:", e)

    r = report(data)
    print("----- 通知本文 -----\n" + report_text(r) + "\n--------------------")
    sent = STATE.read_text().strip() if STATE.exists() else ""
    if a.notify == "force" or (a.notify == "auto" and data["final"] and sent != data["date"]):
        if send_discord(r):
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(data["date"])

if __name__ == "__main__":
    main()
