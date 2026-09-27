"""日経マーケットの主要ランキング（ストップ高・ストップ安・出来高上位・売買代金上位）を Discord に送る。

データ元: Yahoo!ファイナンス ランキングページ
前場引け後（11:40）と大引け後（15:40）に実行。休場日は送らない。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "ja,en;q=0.8"}
PAGES = [
    ("ストップ高", "stopHigh", 0xD03B3B, 20),
    ("ストップ安", "stopLow", 0x2A78D6, 20),
    ("出来高上位", "volume", 0x52514E, 20),
    ("売買代金上位", "tradingValueHigh", 0xEDA100, 20),
]
URL = "https://finance.yahoo.co.jp/stocks/ranking/{key}?market=all"
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")


def is_holiday(d: date) -> bool:
    if d.weekday() >= 5 or (d.month, d.day) in {(1, 1), (1, 2), (1, 3), (12, 31)}:
        return True
    try:
        import jpholiday
        return jpholiday.is_holiday(d)
    except ImportError:
        return False


def _num(s) -> float | None:
    if s is None:
        return None
    t = re.sub(r"[^\d.\-+]", "", str(s))
    try:
        return float(t) if t not in ("", "-", "+", ".") else None
    except ValueError:
        return None


def clean(name: str) -> str:
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\(株\)|（株）|株式会社", "", n)
    return n.strip()


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def parse_state(html: str) -> list[dict]:
    """ページに埋め込まれた __PRELOADED_STATE__ からランキング行を探す"""
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not m:
        return []
    try:
        st = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    best: list[dict] = []
    for d in _walk(st):
        for v in d.values():
            if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
                keys = set(v[0].keys())
                code_k = next((k for k in keys if k.lower() in ("stockcode", "code", "symbol")), None)
                if code_k and any(k.lower() in ("stockname", "name") for k in keys) and len(v) > len(best):
                    best = v
    rows = []
    for r in best:
        k = {x.lower(): x for x in r}
        code = str(r.get(k.get("stockcode") or k.get("code") or k.get("symbol"), "")).split(".")[0].upper()
        name = r.get(k.get("stockname") or k.get("name"), "")
        pk = next((k[x] for x in k if x in ("saveprice", "price", "currentprice")), None) or \
            next((k[x] for x in k if "price" in x and "change" not in x and "rate" not in x), None)
        ck = next((k[x] for x in k if ("rate" in x or "ratio" in x or "percent" in x) and "change" in x), None) or \
            next((k[x] for x in k if x.endswith("rate") or "percent" in x), None)
        price, pct = (r.get(pk) if pk else None), (r.get(ck) if ck else None)
        if isinstance(price, dict):
            price = price.get("price") or price.get("value")
        if isinstance(pct, dict):
            pct = pct.get("rate") or pct.get("value")
        vol = tv = None
        rr = r.get("rankingResult")
        if isinstance(rr, dict):  # 種類ごとの値は rankingResult.{stopPrice|volume|tradingValue} に入る
            for sub in rr.values():
                if isinstance(sub, dict):
                    pct = sub.get("changePriceRate", pct)
                    vol = sub.get("volume", vol)
                    tv = sub.get("tradingValue", tv)
        if CODE_RE.match(code):
            mk = next((r[k[x]] for x in k if "market" in x and isinstance(r[k[x]], str)), "")
            rows.append({"code": code, "name": clean(str(name)), "price": _num(price), "pct": _num(pct),
                         "vol": _num(vol), "tv": _num(tv), "date": str(r.get("date", "")), "market": mk})
    return rows


def parse_table(html: str) -> list[dict]:
    """HTMLの表から読む（予備）"""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return []
    for t in tables:
        rows = []
        for _, r in t.iterrows():
            cells = [str(c) for c in r.tolist()]
            text = " ".join(cells)
            m = re.search(r"\b([0-9][0-9A-Z]{3})\b", text)
            if not m:
                continue
            code = m.group(1)
            # 銘柄名セルは「名前 コード 市場」のように連結されていることが多い
            name_cell = next((c for c in cells if code in c), "")
            name = re.split(r"\s*" + code, name_cell)[0].strip() or name_cell
            pct = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*%", text)
            nums = [n for n in (_num(c) for c in cells) if n is not None]
            price = next((n for n in nums if n and n > 1 and str(int(n)) != code), None)
            rows.append({"code": code, "name": clean(name)[:20], "price": price,
                         "pct": float(pct.group(1)) if pct else None})
        if len(rows) >= 3:
            return rows
    return []


def fetch(key: str) -> tuple[list[dict], int | None]:
    r = requests.get(URL.format(key=key), headers=UA, timeout=30)
    r.raise_for_status()
    html = r.text
    rows = parse_state(html) or parse_table(html)
    total = None
    m = re.search(r"([\d,]+)\s*件中", html)
    if m:
        total = int(m.group(1).replace(",", ""))
    if not rows:
        print(f"[{key}] 行を読めませんでした。HTML先頭:", html[:500].replace("\n", " "))
        if "該当" in html and ("ありません" in html or "0件" in html):
            return [], 0
    return rows, total


# ---------------------------------------------------------------- IPO（当月分）
IPO_URL = "https://www.jpx.co.jp/listing/stocks/new/index.html"


def _txt(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


@lru_cache(maxsize=1)
def ipo_list() -> list[dict]:
    """JPX 新規上場会社情報ページから (上場日, 会社名, コード, 市場, 公開価格/仮条件, テクニカル上場か)"""
    # JPXは文字コードをヘッダーで返さないため、UTF-8として明示的に読む（文字化け対策）
    html = requests.get(IPO_URL, headers=UA, timeout=30).content.decode("utf-8", "ignore")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out = []
    for i, r in enumerate(rows[:-1]):
        d = re.search(r"(\d{4})/(\d{2})/(\d{2})", r)
        code = re.search(r'<span id="([0-9][0-9A-Z]{3})"', r)
        if not (d and code and 'rowspan="2"' in r):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        cells = [_txt(t) for t in tds]
        name = clean(cells[1].replace("代表者インタビュー", "").replace("（株）", "").replace("(株)", ""))
        tech = "*" in name
        nxt = [_txt(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", rows[i + 1], re.S)]
        # 1行目: 上場日/会社名/コード/概要/確認書/仮条件/公募/売買単位　2行目: 市場/Iの部/CG/公開価格/売出/決算短信
        cond = re.sub(r"（注\d+）", "", cells[5]).strip() if len(cells) > 5 else ""
        price = re.sub(r"（注\d+）", "", nxt[3]).strip() if len(nxt) > 3 else ""
        out.append({"date": date(int(d.group(1)), int(d.group(2)), int(d.group(3))),
                    "name": name.replace("*", "").strip(), "code": code.group(1), "market": nxt[0] if nxt else "",
                    "price": price if price not in ("", "-") else "", "cond": cond if cond not in ("", "-") else "",
                    "tech": tech})
    return out


def ipo_fields(today: date) -> list[dict]:
    try:
        ipos = [x for x in ipo_list() if x["date"].year == today.year and x["date"].month == today.month]
    except Exception as e:  # noqa: BLE001
        print("IPO取得失敗:", e)
        return []
    listed = [x for x in ipos if x["date"] <= today]
    coming = [x for x in ipos if x["date"] > today]
    fields = []
    if listed:
        quotes = {}
        try:
            import yfinance as yf
            df = yf.download([f"{x['code']}.T" for x in listed], period="2mo", interval="1d",
                             progress=False, auto_adjust=False, group_by="ticker")
            for x in listed:
                t = f"{x['code']}.T"
                sub = df[t] if len(listed) > 1 else df
                sub = sub.dropna(subset=["Close"])
                sub = sub[sub.index.date >= x["date"]]
                if len(sub):
                    quotes[x["code"]] = {"open1": float(sub["Open"].iloc[0]), "close": float(sub["Close"].iloc[-1]),
                                         "prev": float(sub["Close"].iloc[-2]) if len(sub) > 1 else None}
        except Exception as e:  # noqa: BLE001
            print("IPO株価取得失敗:", e)
        ls = []
        for x in sorted(listed, key=lambda x: x["date"]):
            q = quotes.get(x["code"])
            ipo_p = _num(x["price"])
            head = f"{x['date'].month}/{x['date'].day} `{x['code']}` {x['name'][:12]}（{x['market'][:2]}）"
            if q and ipo_p:
                chg = f" 前日比{(q['close'] / q['prev'] - 1) * 100:+.1f}%" if q.get("prev") else ""
                ls.append(f"{head}\n　公開{ipo_p:,.0f} → 初値{q['open1']:,.0f}（{(q['open1'] / ipo_p - 1) * 100:+.0f}%）"
                          f" → 現在{q['close']:,.0f}（公開比{(q['close'] / ipo_p - 1) * 100:+.0f}%）{chg}")
            elif x["tech"]:
                cur = f" 現在{q['close']:,.0f}" if q else ""
                ls.append(f"{head} テクニカル上場{cur}")
            else:
                ls.append(f"{head} 公開価格{x['price'] or '—'}")
        value = "\n".join(ls)
        while len(value) > 1024:
            ls = ls[:-1]
            value = "\n".join(ls + ["…"])
        fields.append({"name": f"🆕 今月上場したIPO（{len(listed)}社）", "value": value, "inline": False})
    if coming:
        ls = []
        for x in sorted(coming, key=lambda x: x["date"]):
            if x["tech"]:
                p = "テクニカル上場（公募なし）"
            elif x["price"]:
                p = f"公開価格 {x['price']}円"
            elif x["cond"]:
                p = f"仮条件 {x['cond']}円"
            else:
                p = "仮条件未定"
            tech = ""
            ls.append(f"{x['date'].month}/{x['date'].day}({'月火水木金土日'[x['date'].weekday()]}) `{x['code']}` "
                      f"{x['name'][:14]}（{x['market']}）{tech} {p}")
        fields.append({"name": f"📅 今月のIPO予定（{len(coming)}社）", "value": "\n".join(ls)[:1024], "inline": False})
    elif ipos:
        fields.append({"name": "📅 今月のIPO予定", "value": "今月の上場予定は以上です", "inline": False})
    return fields


# ---------------------------------------------------------------- 市場区分の色分け
MARKET_MARK = [("グロース", "🟥"), ("スタンダード", "🟨"), ("プライム", "🟦"), ("ETF", "⬜"), ("ETN", "⬜"),
               ("REIT", "🟪"), ("インフラ", "🟪"), ("PRO", "⬛")]
LEGEND = "🟦プライム 🟨スタンダード 🟥グロース ⬜ETF・ETN 🟪REIT等"


def mark(market: str) -> str:
    return next((e for k, e in MARKET_MARK if k in (market or "")), "")


def market_map() -> dict[str, str]:
    """銘柄コード → 市場区分（JPX上場銘柄一覧＋今月のIPO）"""
    out: dict[str, str] = {}
    try:
        from constituents import fetch_jpx_all
        x = fetch_jpx_all()
        for c, m in zip(x["コード"].astype(str), x["市場・商品区分"].astype(str)):
            out[c.strip().upper()] = m
    except Exception as e:  # noqa: BLE001
        print("市場区分の取得失敗:", e)
    try:  # 一覧の更新は月1回のため、新規上場銘柄はIPO情報で補う
        for x in ipo_list():
            out.setdefault(x["code"], x["market"])
    except Exception as e:  # noqa: BLE001
        print("IPO市場区分の取得失敗:", e)
    return out


MARKETS: dict[str, str] = {}


def line(r: dict) -> str:
    p = "" if r["price"] is None else f" {r['price']:,.0f}円" if r["price"] >= 100 else f" {r['price']:,.1f}円"
    c = "" if r.get("pct") is None else f" ({r['pct']:+.2f}%)"
    x = ""
    if r.get("tv"):
        x = f" {r['tv'] / 1e8:,.0f}億円"
    elif r.get("vol"):
        x = f" {r['vol'] / 1e4:,.0f}万株"
    m = mark(r.get("market") or MARKETS.get(r["code"], ""))
    return f"{m}`{r['code']}` {r['name'][:12]}{p}{c}{x}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", choices=["am", "pm", "auto"], default="auto")
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--ignore-holiday", action="store_true")
    a = ap.parse_args()

    now = datetime.now(JST)
    if is_holiday(now.date()) and not a.ignore_holiday:
        print(f"{now.date()} は休場日のため送信しません")
        return
    sess = a.session if a.session != "auto" else ("am" if now.hour < 13 else "pm")
    label = "前場引け" if sess == "am" else "大引け"
    wd = "月火水木金土日"[now.weekday()]

    MARKETS.update(market_map())
    print(f"市場区分: {len(MARKETS)}銘柄")
    fields = []
    today_md = now.strftime("%m/%d")
    for title, key, _, n in PAGES:
        rows, total = fetch(key)
        if rows and rows[0].get("date") and rows[0]["date"] != today_md and not a.ignore_holiday:
            print(f"ランキングの日付が {rows[0]['date']}（今日ではない）ため送信しません")
            return
        cnt = total if total is not None else len(rows)
        print(f"{title}: {cnt}件", *[line(r) for r in rows[:n]], sep="\n  ")
        if not rows:
            value = "該当なし"
        else:
            ls = [f"{i + 1}. {line(r)}" for i, r in enumerate(rows[:n])]
            if cnt and cnt > n:
                ls.append(f"ほか{cnt - n}社")
            value = "\n".join(ls)
            while len(value) > 1024:  # Discordの項目上限
                ls = ls[:-2] + [ls[-1]] if len(ls) > 2 else ls[:1]
                value = "\n".join(ls)
        head = f"■{title}" + (f" {cnt}社" if title.startswith("ストップ") else "")
        fields.append({"name": head, "value": value, "inline": False})

    fields += ipo_fields(now.date())
    embed = {"title": f"📋 日経マーケット速報 {now.month}/{now.day}({wd}) {label}",
             "color": 0x0B0B0B, "fields": fields,
             "footer": {"text": LEGEND + "\nYahoo!ファイナンス ランキング（全市場）より"}}
    if a.notify == "on":
        notify.send([embed], "マーケット速報", "ranking")


if __name__ == "__main__":
    main()
