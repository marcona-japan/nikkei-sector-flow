"""要人発言の速報（Discord）。

日銀総裁・副総裁、財務官（為替介入）、FRB議長などの発言は、予定表にない国会答弁や会議の場でも出るため、
Google ニュースのRSSを10分おきに見て、新しい記事の見出しを送る。
初回は既存の記事を既読にするだけで送らない。送信済みは data/news_seen.json。
"""
from __future__ import annotations

import argparse
import email.utils
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEEN = ROOT / "data" / "news_seen.json"
JST = timezone(timedelta(hours=9))
# (表示ラベル, 検索語, 見出しに含まれていてほしい語のどれか)
QUERIES = [
    ("🇯🇵 日銀", "植田総裁 OR 日銀総裁 OR 日銀副総裁", ("植田", "日銀総裁", "副総裁", "日銀")),
    ("💴 為替介入", "為替介入 OR 財務官 為替 OR 財務相 為替", ("介入", "財務官", "財務相", "円安", "為替")),
    ("🇺🇸 FRB", "パウエル議長 OR FRB議長", ("パウエル", "FRB", "FOMC")),
]
MAX_AGE = timedelta(minutes=90)


def norm(t: str) -> str:
    t = re.sub(r"\s+-\s+[^-]+$", "", t)  # 末尾の「 - 媒体名」
    return re.sub(r"[\s　「」『』（）()【】、。・:：!！?？]", "", t)[:60]


def fetch(q: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote(q)}+when:1d&hl=ja&gl=JP&ceid=JP:ja"
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST)
        except Exception:  # noqa: BLE001
            continue
        out.append({"title": title, "link": link, "src": src, "pub": pub})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--test", action="store_true", help="既読を無視して直近6時間の記事を送る（記録は残さない）")
    a = ap.parse_args()
    now = datetime.now(JST)
    global MAX_AGE
    if a.test:
        MAX_AGE = timedelta(hours=6)
    first = not SEEN.exists() and not a.test
    seen = {} if (first or a.test or not SEEN.exists()) else json.loads(SEEN.read_text())
    fields = []
    for label, q, must in QUERIES:
        try:
            items = fetch(q)
        except Exception as e:  # noqa: BLE001
            print(label, "取得失敗:", e)
            continue
        new = []
        for it in sorted(items, key=lambda x: x["pub"]):
            k = norm(it["title"])
            if not k or k in seen:
                continue
            seen[k] = now.isoformat(timespec="minutes")
            if first or now - it["pub"] > MAX_AGE or not any(m in it["title"] for m in must):
                continue
            new.append(it)
        if new:
            lines = [f"`{it['pub']:%H:%M}` [{it['title'][:90]}]({it['link']})" for it in new[-6:]]
            fields.append({"name": label, "value": "\n".join(lines)[:1024], "inline": False})
    # 3日より古い既読は捨てる
    cut = (now - timedelta(days=3)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cut}
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    if not a.test:
        SEEN.write_text(json.dumps(seen, ensure_ascii=False, separators=(",", ":")))
    if first:
        print("初回のため既読登録のみ")
        return
    if not fields:
        print("新しい記事なし")
        return
    emb = {"title": ("【テスト】" if a.test else "") + "📣 要人発言・介入関連の新着ニュース", "color": 0xE67E22, "fields": fields,
           "footer": {"text": "Googleニュースの見出し（10分おきに確認）。記事の配信時刻は媒体の表示によります。"}}
    print(json.dumps(emb, ensure_ascii=False)[:1500])
    if a.notify == "on":
        notify.send([emb], "要人発言", "market")


if __name__ == "__main__":
    main()
