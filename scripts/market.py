"""毎日の引け後マーケットレポート（資金の動き チェックリストの日次指標）を Discord に送る。

- 指数: 日経平均・TOPIX・NT倍率（Yahoo!ファイナンス）、日経VI（日経公式CSV）
- 量: 日経225構成の売買代金（sector-flowの集計）、市場別売買代金（JPX 概算・精算表PDF、1営業日遅れで公表）
- 広がり: 値上がり／値下がり銘柄数（Yahoo!ファイナンス、全市場）と騰落レシオ（25日、履歴が貯まってから）
- 偏り: 空売り比率と業種別空売り比率（JPX）
- 背景: ドル円・米10年金利・日本10年金利・米国株・原油（yfinance／財務省）
- 予定: data/events.json とSQ日

履歴は docs/data/market.json に保存（騰落レシオ・前日比の計算用）。
"""
from __future__ import annotations

import argparse
import calendar
import io
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "docs" / "data" / "market.json"
EVENTS = ROOT / "data" / "events.json"
JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
PAGE_URL = "https://marcona-japan.github.io/nikkei-sector-flow/"


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=40, **kw)
    r.raise_for_status()
    return r


def num(s):
    if s is None:
        return None
    t = re.sub(r"[^\d.\-]", "", str(s))
    try:
        return float(t)
    except ValueError:
        return None


def yahoo_state(html: str) -> dict:
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    return json.loads(m.group(1)) if m else {}


def safe(fn, label):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  一部のデータ元が落ちてもレポートは出す
        print(f"[{label}] 取得失敗: {e}")
        return None


# ---------------------------------------------------------------- 各データ
def index_quote(code: str) -> dict:
    st = yahoo_state(get(f"https://finance.yahoo.co.jp/quote/{code}").text)
    p = st["mainDomesticIndexPriceBoard"]["indexPrices"]
    return {"price": num(p["price"]), "chg": num(p["changePrice"]), "pct": num(p["changePriceRate"])}


def nikkei_vi() -> dict:
    txt = get("https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_vi_daily_jp.csv").content.decode("cp932", "ignore")
    rows = [r.replace('"', "").split(",") for r in txt.splitlines() if re.match(r'"?\d{4}/', r)]
    last, prev = rows[-1], rows[-2]
    return {"date": last[0].replace("/", "-"), "close": float(last[1]), "prev": float(prev[1])}


def breadth() -> dict:
    out = {}
    for key, name in (("up", "up"), ("down", "down")):
        html = get(f"https://finance.yahoo.co.jp/stocks/ranking/{key}?market=all").text
        text = re.sub(r"<[^>]+>", "", html)
        m = re.search(r"([\d,]+)\s*件中", text)
        out[name] = int(m.group(1).replace(",", "")) if m else None
        d = re.search(r"更新日時：(\d{4})/(\d{2})/(\d{2})", text)
        if d:
            out["date"] = f"{d.group(1)}-{d.group(2)}-{d.group(3)}"
    return out


def jpx_links(page: str, pat: str) -> list[str]:
    html = get(page).text
    return ["https://www.jpx.co.jp" + l if l.startswith("/") else l for l in re.findall(pat, html)]


def short_selling() -> dict:
    import pdfplumber
    links = jpx_links("https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html",
                      r'href="([^"]+/\d{6}-m\.pdf)"')
    m_url = links[0]
    ymd = re.search(r"(\d{6})-m\.pdf", m_url).group(1)
    with pdfplumber.open(io.BytesIO(get(m_url).content)) as pdf:
        t = pdf.pages[0].extract_text()
    row = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+([\d,]+)\s+([\d.]+)%\s+([\d,]+)\s+([\d.]+)%\s+([\d,]+)\s+([\d.]+)%\s+([\d,]+)", t)
    ratio = float(row.group(7)) + float(row.group(9))
    res = {"date": f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", "ratio": round(ratio, 1)}
    # 業種別
    g_url = m_url.replace("-m.pdf", "-g.pdf")
    with pdfplumber.open(io.BytesIO(get(g_url).content)) as pdf:
        t = "\n".join((p.extract_text() or "") for p in pdf.pages)
    secs = []
    for m in re.finditer(r"^(\S+?)\s+[\d,]+\s+[\d.]+%\s+[\d,]+\s+([\d.]+)%\s+[\d,]+\s+([\d.]+)%\s+([\d,]+)", t, re.M):
        secs.append({"name": m.group(1), "ratio": round(float(m.group(2)) + float(m.group(3)), 1),
                     "value": int(m.group(4).replace(",", ""))})
    res["sectors"] = secs
    return res


def market_value() -> dict:
    """JPX 概算・精算表PDF の「株券等売買代金概算」から立会内の市場別売買代金（円）"""
    import pdfplumber
    links = jpx_links("https://www.jpx.co.jp/markets/statistics-equities/daily/index.html",
                      r'href="([^"]+est-set_\d{8}\.pdf)"')
    url = links[0]
    ymd = re.search(r"est-set_(\d{8})\.pdf", url).group(1)
    with pdfplumber.open(io.BytesIO(get(url).content)) as pdf:
        page = next(p.extract_text() for p in pdf.pages
                    if re.search(r"売\s*買\s*代\s*金\s*概\s*算", p.extract_text() or ""))

    def first(pattern):
        m = re.search(pattern, page, re.M)
        return int(m.group(1).replace(",", "")) if m else None

    prime = first(r"^計\s+([\d,]{9,})")
    standard = first(r"^スタンダード.*?Stock\s+([\d,]{9,})")
    total = first(r"合\s*計\s+([\d,]{9,})")
    growth = total - prime - standard if None not in (total, prime, standard) else None
    return {"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "prime": prime, "standard": standard, "growth": growth}


def jgb10() -> dict:
    txt = get("https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv").content.decode("cp932", "ignore")
    lines = [l.split(",") for l in txt.splitlines() if l and l[0] in "RSH" and "." in l]
    head = next(l.split(",") for l in txt.splitlines() if "10年" in l)
    i = head.index("10年")
    last, prev = lines[-1], lines[-2]
    return {"date": last[0], "close": float(last[i]), "prev": float(prev[i])}


def overseas() -> dict:
    import yfinance as yf
    tick = {"ドル円": "JPY=X", "米10年": "^TNX", "NYダウ": "^DJI", "S&P500": "^GSPC",
            "ナスダック": "^IXIC", "SOX": "^SOX", "WTI原油": "CL=F"}
    df = yf.download(list(tick.values()), period="10d", interval="1d", progress=False, auto_adjust=False)["Close"]
    out = {}
    for name, t in tick.items():
        s = df[t].dropna()
        if len(s) >= 2:
            out[name] = {"close": float(s.iloc[-1]), "prev": float(s.iloc[-2]), "date": str(s.index[-1].date())}
    return out


def sq_dates(y: int) -> list[date]:
    res = []
    for m in range(1, 13):
        c = calendar.Calendar().monthdatescalendar(y, m)
        fr = [d for wk in c for d in wk if d.month == m and d.weekday() == 4]
        res.append(fr[1])
    return res


def upcoming(today: date, days: int = 7) -> list[str]:
    ev = json.loads(EVENTS.read_text())["events"] if EVENTS.exists() else []
    items = [(date.fromisoformat(e["date"]), e["title"]) for e in ev]
    for y in (today.year, today.year + 1):
        for d in sq_dates(y):
            items.append((d, "メジャーSQ" if d.month in (3, 6, 9, 12) else "SQ"))
    end = today + timedelta(days=days)
    out = []
    for d, t in sorted(items):
        if today < d <= end:
            out.append(f"{d.month}/{d.day}({'月火水木金土日'[d.weekday()]}) {t}")
    return out


def next_major(today: date) -> list[str]:
    """種類ごとに次の1件と、あと何日か"""
    ev = json.loads(EVENTS.read_text())["events"] if EVENTS.exists() else []
    items = [(date.fromisoformat(e["date"]), e["title"]) for e in ev]
    for y in (today.year, today.year + 1):
        for d in sq_dates(y):
            if d.month in (3, 6, 9, 12):
                items.append((d, "メジャーSQ"))
    kinds = [("FOMC", "FOMC"), ("日銀会合", "日銀会合"), ("米CPI", "米CPI"), ("全国CPI", "全国CPI"),
             ("東京都区部CPI", "東京都区部CPI"), ("メジャーSQ", "メジャーSQ")]
    out = []
    for key, label in kinds:
        nxt = next(((d, t) for d, t in sorted(items) if d > today and t.startswith(key)), None)
        if nxt:
            d, t = nxt
            detail = t[len(key):].strip()
            out.append((d, f"{label}: {d.month}/{d.day}({'月火水木金土日'[d.weekday()]}) あと{(d - today).days}日 {detail}"))
    return [t for _, t in sorted(out)]


# ---------------------------------------------------------------- レポート
def pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def fmt_yen(v):
    if v is None:
        return "—"
    return f"{v / 1e12:.2f}兆円" if v >= 1e12 else f"{v / 1e8:,.0f}億円"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--ignore-holiday", action="store_true")
    a = ap.parse_args()

    now = datetime.now(JST)
    today = now.date()
    hist = json.loads(HIST.read_text()) if HIST.exists() else {"days": {}}
    days = hist["days"]

    br = safe(breadth, "騰落")
    if not a.ignore_holiday and (not br or br.get("date") != today.isoformat()):
        print(f"本日({today})の取引データがないため送信しません（休場日の可能性）: {br}")
        return
    if br and br.get("date"):
        today = date.fromisoformat(br["date"])  # 手動実行時も直近の取引日の記録として扱う

    nk = safe(lambda: index_quote("998407.O"), "日経平均")
    tp = safe(lambda: index_quote("998405.T"), "TOPIX")
    vi = safe(nikkei_vi, "日経VI")
    ss = safe(short_selling, "空売り")
    mv = safe(market_value, "市場別売買代金")
    jg = safe(jgb10, "日本10年")
    ov = safe(overseas, "海外") or {}
    sector = safe(lambda: json.loads((ROOT / "docs" / "data" / f"{today}.json").read_text()), "業種別")

    rec = days.get(today.isoformat(), {})
    if br:
        rec.update(up=br["up"], down=br["down"])
    if nk and tp:
        rec.update(nk=nk["price"], tp=tp["price"], nt=round(nk["price"] / tp["price"], 3))
    if ss and ss["date"] == today.isoformat():
        rec["short"] = ss["ratio"]
    if sector:
        rec["v225"] = sector["day_total"]
    days[today.isoformat()] = rec
    if mv:  # 市場別売買代金は公表日の翌営業日に取れるので、その日付の記録に入れる
        days.setdefault(mv["date"], {}).update(prime=mv["prime"], standard=mv["standard"], growth=mv["growth"])

    keys = sorted(days)
    prev_key = next((k for k in reversed(keys) if k < today.isoformat()), None)
    prev = days.get(prev_key, {}) if prev_key else {}

    fields = []
    # 指数
    ls = []
    if nk:
        ls.append(f"日経平均 **{nk['price']:,.0f}**（{nk['chg']:+,.0f} / {nk['pct']:+.2f}%）")
    if tp:
        ls.append(f"TOPIX **{tp['price']:,.2f}**（{tp['chg']:+,.2f} / {tp['pct']:+.2f}%）")
    if vi:
        ls.append(f"日経VI **{vi['close']:.2f}**（{vi['close'] - vi['prev']:+.2f}）")
    fields.append({"name": "📈 指数", "value": "\n".join(ls) or "—", "inline": False})

    # NT倍率
    if "nt" in rec:
        nts = [days[k]["nt"] for k in keys if k <= today.isoformat() and days[k].get("nt")]
        v = f"**{rec['nt']:.2f}倍**"
        if prev.get("nt"):
            dlt = rec["nt"] - prev["nt"]
            v += f"（前日比 {dlt:+.2f}）→ " + ("日経平均の方が強い＝値がさハイテク主導" if dlt > 0.005
                                              else "TOPIXの方が強い＝銀行・内需など幅広い買い" if dlt < -0.005 else "ほぼ横ばい")
        else:
            v += "（前日比は明日から表示）"
        if len(nts) >= 5:
            v += f"\n5日前 {nts[-5]:.2f} → 今日 {nts[-1]:.2f}"
        v += "\n見方: 上昇＝ハイテク主導、低下＝銀行・内需など"
        fields.append({"name": "⚖️ NT倍率（日経平均÷TOPIX）", "value": v, "inline": False})

    # 量
    ls = []
    if sector:
        v, pv = sector["day_total"], prev.get("v225")
        ls.append(f"日経225採用 **{fmt_yen(v)}**" + (f"（前日比 {pct(v, pv):+.1f}%）" if pv else "")
                  + (f"　20日平均比 {pct(v, sector['base_total']):+.0f}%" if sector.get("base_total") else ""))
    if mv:
        md = date.fromisoformat(mv["date"])
        pk = next((k for k in reversed(keys) if k < mv["date"] and days[k].get("prime")), None)
        p = days.get(pk, {}) if pk else {}
        tot = sum(x for x in (mv["prime"], mv["standard"], mv["growth"]) if x)
        ls.append(f"市場別（{md.month}/{md.day}・立会内）")
        for k, n in (("prime", "プライム"), ("standard", "スタンダード"), ("growth", "グロース")):
            chg = f" {pct(mv[k], p.get(k)):+.1f}%" if p.get(k) else ""
            ls.append(f"　{n} {fmt_yen(mv[k])}{chg}（シェア{mv[k] / tot * 100:.1f}%）")
    fields.append({"name": "💰 売買代金", "value": "\n".join(ls) or "—", "inline": False})

    # 広がり
    ls = []
    if br:
        ls.append(f"値上がり **{br['up']:,}** / 値下がり **{br['down']:,}**（全市場）")
    ud = [(days[k]["up"], days[k]["down"]) for k in keys if k <= today.isoformat() and days[k].get("up")][-25:]
    if len(ud) >= 25:
        ratio = sum(u for u, _ in ud) / max(1, sum(d for _, d in ud)) * 100
        mark = "（過熱圏）" if ratio >= 120 else "（売られすぎ圏）" if ratio <= 70 else ""
        ls.append(f"騰落レシオ(25日) **{ratio:.0f}%**{mark}")
    else:
        ls.append(f"騰落レシオ(25日)：データ蓄積中（{len(ud)}/25日）")
    fields.append({"name": "🌊 広がり", "value": "\n".join(ls), "inline": False})

    # 偏り
    if ss:
        sd = date.fromisoformat(ss["date"])
        pv = prev.get("short")
        top = sorted([s for s in ss["sectors"] if s["value"] >= 20000 and "その他（" not in s["name"]
                      and "合計" not in s["name"]], key=lambda s: -s["ratio"])[:3]
        v = (f"空売り比率 **{ss['ratio']:.1f}%**" + (f"（前日 {pv:.1f}%）" if pv else "")
             + ("　⚠️40%超" if ss["ratio"] >= 40 else "") + (f"　※{sd.month}/{sd.day}分" if sd != today else ""))
        if top:
            v += "\n高い業種: " + "、".join(f"{s['name']} {s['ratio']:.1f}%" for s in top)
        fields.append({"name": "🐻 偏り（空売り）", "value": v, "inline": False})

    # 背景
    ls = []
    if "ドル円" in ov:
        o = ov["ドル円"]; ls.append(f"ドル円 {o['close']:.2f}（{o['close'] - o['prev']:+.2f}）")
    rate = []
    if jg:
        rate.append(f"日本10年 {jg['close']:.3f}%（{(jg['close'] - jg['prev']) * 100:+.1f}bp）")
    if "米10年" in ov:
        o = ov["米10年"]; rate.append(f"米10年 {o['close']:.3f}%（{(o['close'] - o['prev']) * 100:+.1f}bp）")
    if rate:
        ls.append("　".join(rate))
    us = [f"{n} {pct(ov[n]['close'], ov[n]['prev']):+.2f}%" for n in ("NYダウ", "ナスダック", "SOX") if n in ov]
    if us:
        ls.append("前日の米国株: " + "　".join(us))
    if "WTI原油" in ov:
        o = ov["WTI原油"]; ls.append(f"WTI原油 {o['close']:.2f}ドル（{pct(o['close'], o['prev']):+.1f}%）")
    fields.append({"name": "🌐 背景", "value": "\n".join(ls) or "—", "inline": False})

    ev = upcoming(today)
    fields.append({"name": "📅 今後1週間の予定", "value": "\n".join(ev) or "大きな予定なし", "inline": False})
    fields.append({"name": "⏳ 次の主要イベント", "value": "\n".join(next_major(today)) or "—", "inline": False})

    HIST.parent.mkdir(parents=True, exist_ok=True)
    keep = sorted(days)[-400:]
    HIST.write_text(json.dumps({"updated": now.strftime("%Y-%m-%d %H:%M"), "days": {k: days[k] for k in keep}},
                               ensure_ascii=False, separators=(",", ":")))

    wd = "月火水木金土日"[today.weekday()]
    embed = {"title": f"🧭 マーケット概況 {today.month}/{today.day}({wd})", "url": PAGE_URL,
             "color": 0x2A78D6, "fields": fields,
             "footer": {"text": "出所: Yahoo!ファイナンス・JPX・日経・財務省（概算）。投資判断はご自身で。"}}
    print(json.dumps(embed, ensure_ascii=False, indent=1))
    if a.notify == "on":
        notify.send([embed], "マーケット概況", "market")


if __name__ == "__main__":
    main()
