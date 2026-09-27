"""週次の需給レポート（誰が買ったか・需給の歪み）を Discord に送る。毎週金曜の夕方。

- 投資部門別売買状況（東証プライム・金額）… JPX、木曜公表
- 対内証券投資（非居住者の日本株売買）… 財務省、木曜公表
- 信用取引残高（二市場・金額）と信用倍率 … JPX、火曜公表
- 裁定取引に係る現物ポジション … JPX
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
JPX = "https://www.jpx.co.jp"


def get(url):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r


def links(page, pat):
    return [JPX + l if l.startswith("/") else l for l in re.findall(pat, get(page).text)]


def n(v):
    try:
        f = float(str(v).replace(",", "").replace("▲", "-").strip())
        return None if f != f else f  # NaN除外
    except ValueError:
        return None


def safe(fn, label):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] 取得失敗: {e}")
        return None


def oku(v_thousand_yen):
    """千円 → 億円表記"""
    v = v_thousand_yen / 1e5
    return f"{v:+,.0f}億円"


def investor_type() -> dict:
    urls = links(f"{JPX}/markets/statistics-equities/investor-type/index.html",
                 r'href="([^"]+stock_val_1_\d{6}\.xls)"')
    url = max(urls, key=lambda u: re.search(r"(\d{6})\.xls", u).group(1))
    x = pd.read_excel(io.BytesIO(get(url).content), header=None)
    period = next((str(v) for v in x.iloc[:12, 7].tolist() if "～" in str(v)), "")
    title = next((str(v) for v in x.iloc[:5, 0].tolist() if "週" in str(v)), "")
    want = {"海外投資家": "海外投資家", "個　人": "個人", "信託銀行": "信託銀行（年金）",
            "事業法人": "事業法人（自社株買い）", "投資信託": "投資信託", "証券会社": "証券会社"}
    out = {}
    for i in range(len(x) - 1):
        lab = str(x.iat[i, 0]).strip()
        if lab in want:
            cur = next((n(x.iat[j, 10]) for j in (i, i + 1) if n(x.iat[j, 10]) is not None), None)
            prev = next((n(x.iat[j, 6]) for j in (i, i + 1) if n(x.iat[j, 6]) is not None), None)
            out[want[lab]] = (cur, prev)
    return {"period": period, "title": title, "rows": out}


def inward() -> dict:
    t = get("https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv").content.decode("cp932", "ignore")
    rows = [r for r in csv.reader(io.StringIO(t)) if r and r[0][:1].isdigit()]
    last, prev = rows[-1], rows[-2]
    return {"period": re.sub(r"\s+", " ", last[0]).replace("．", "/"),
            "stock": n(last[14]), "stock_prev": n(prev[14]), "total": n(last[22])}


def margin() -> dict:
    urls = links(f"{JPX}/markets/statistics-equities/margin/04.html", r'href="([^"]+mtseisan\d{10}\.xls)"')
    url = max(urls, key=lambda u: re.search(r"(\d{10})\.xls", u).group(1))
    ymd = re.search(r"mtseisan(\d{8})", url).group(1)
    x = pd.read_excel(io.BytesIO(get(url).content), header=None)
    val = None
    for i in range(len(x)):
        if any("二市場" in str(c) for c in x.iloc[i].tolist()[:2]):
            row = x.iloc[i + 1].tolist()
            nums = [n(v) for v in row if n(v) is not None]
            val = nums
            break
    ratio = None
    for i in range(len(x)):
        cells = [str(v) for v in x.iloc[i].tolist()]
        if any(c.strip() == "合計" for c in cells):
            ns = [n(c) for c in cells if n(c) is not None]
            if ns:
                ratio = ns[0]
                break
    return {"date": f"{ymd[:4]}/{int(ymd[4:6])}/{int(ymd[6:])}", "sell": val[8], "sell_chg": val[9],
            "buy": val[10], "buy_chg": val[11], "ratio": ratio}


def arbitrage() -> dict:
    urls = links(f"{JPX}/markets/statistics-equities/program/index.html", r'href="([^"]+/\d{6}\.xls)"')
    url = max(urls, key=lambda u: re.search(r"(\d{6})\.xls", u).group(1))
    x = pd.read_excel(io.BytesIO(get(url).content), header=None)
    asof = ""
    for i in range(len(x)):
        s = " ".join(str(v) for v in x.iloc[i].tolist() if str(v) != "nan")
        m = re.search(r"現物ポジション（(\d+月\d+日)現在）", s)
        if m:
            asof = m.group(1)
        if s.startswith("株") and asof and "数" in s and len([v for v in x.iloc[i].tolist() if n(v) is not None]) >= 6:
            ns = [n(v) for v in x.iloc[i].tolist() if n(v) is not None]
            nxt = [n(v) for v in x.iloc[i + 1].tolist() if n(v) is not None]
            return {"asof": asof, "buy": ns[5], "sell": ns[2], "buy_chg": nxt[5] if len(nxt) > 5 else None}
    raise RuntimeError("裁定残の行が見つかりません")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    a = ap.parse_args()
    now = datetime.now(JST)

    it = safe(investor_type, "投資部門別")
    iw = safe(inward, "対内証券投資")
    mg = safe(margin, "信用残")
    ar = safe(arbitrage, "裁定残")

    fields = []
    if it:
        ls = [f"{it['period']}（東証プライム・金額）"]
        for k, (cur, prev) in it["rows"].items():
            if cur is None:
                continue
            tag = "買い越し" if cur > 0 else "売り越し"
            p = f"（前週 {oku(prev)}）" if prev is not None else ""
            ls.append(f"{'🟥' if cur > 0 else '🟦'} {k} **{oku(cur)}** {tag}{p}")
        fields.append({"name": "👥 投資部門別売買", "value": "\n".join(ls), "inline": False})
    if iw:
        s = iw["stock"]
        v = (f"{iw['period']}\n非居住者の日本株 **{s:+,.0f}億円**（{'取得超' if s > 0 else '処分超'}）"
             + (f"　前週 {iw['stock_prev']:+,.0f}億円" if iw.get("stock_prev") is not None else ""))
        if it and "海外投資家" in it["rows"] and it["rows"]["海外投資家"][0] is not None:
            same = (it["rows"]["海外投資家"][0] > 0) == (s > 0)
            v += "\n投資部門別の海外投資家と方向が" + ("一致" if same else "不一致（集計対象・期間の違いに注意）")
        fields.append({"name": "🌏 対内証券投資（財務省）", "value": v, "inline": False})
    if mg:
        v = (f"{mg['date']}申込み現在（二市場・金額）\n買い残 **{mg['buy'] / 100:,.0f}億円**（{mg['buy_chg'] / 100:+,.0f}億円）"
             f"　売り残 **{mg['sell'] / 100:,.0f}億円**（{mg['sell_chg'] / 100:+,.0f}億円）")
        if mg.get("ratio"):
            v += f"\n信用倍率 **{mg['ratio']:.2f}倍**"
        v += "\n※評価損益率はJPXの公表対象外のため未掲載"
        fields.append({"name": "📒 信用取引残高", "value": v, "inline": False})
    if ar:
        v = (f"{ar['asof']}現在\n買いポジション **{ar['buy'] / 1e3:,.1f}百万株**"
             + (f"（前日比 {ar['buy_chg'] / 1e3:+,.1f}百万株）" if ar.get("buy_chg") is not None else "")
             + f"　売りポジション {ar['sell'] / 1e3:,.1f}百万株")
        fields.append({"name": "⚙️ 裁定取引の現物ポジション", "value": v, "inline": False})

    if not fields:
        print("取得できたデータがありません")
        return
    embed = {"title": f"🗓️ 週次 需給レポート {now.month}/{now.day}", "color": 0x4A3AA7, "fields": fields,
             "footer": {"text": "出所: JPX・財務省。数値は各公表時点のもの。投資判断はご自身で。"}}
    import json
    print(json.dumps(embed, ensure_ascii=False, indent=1))
    if a.notify == "on":
        notify.send([embed], "週次 需給レポート", "weekly")


if __name__ == "__main__":
    main()
