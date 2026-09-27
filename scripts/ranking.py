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
    if best:
        print("  列:", {k: (v if not isinstance(v, (dict, list)) else type(v).__name__) for k, v in best[0].items()})
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
        if CODE_RE.match(code):
            rows.append({"code": code, "name": clean(str(name)), "price": _num(price), "pct": _num(pct)})
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


def line(r: dict) -> str:
    p = "" if r["price"] is None else f" {r['price']:,.0f}円" if r["price"] >= 100 else f" {r['price']:,.1f}円"
    c = "" if r["pct"] is None else f" {r['pct']:+.2f}%"
    return f"`{r['code']}` {r['name'][:14]}{p}{c}"


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

    fields = []
    for title, key, _, n in PAGES:
        rows, total = fetch(key)
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

    embed = {"title": f"📋 日経マーケット速報 {now.month}/{now.day}({wd}) {label}",
             "color": 0x0B0B0B, "fields": fields,
             "footer": {"text": "Yahoo!ファイナンス ランキング（全市場）より"}}
    if a.notify == "on":
        notify.send([embed], "マーケット速報", "ranking")


if __name__ == "__main__":
    main()
