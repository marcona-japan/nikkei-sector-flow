"""監視銘柄の適時開示（TDnet）をチェックして Discord に通知する。

データ元: やのしん TDnet WebAPI（webapi.yanoshin.jp。TDnet公式は自動取得を禁止しているため）
監視銘柄: data/watchlist.csv
履歴: docs/data/tdnet.json（開示一覧ページ用、直近60日）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WATCH = ROOT / "data" / "watchlist.csv"
SEEN = ROOT / "data" / "tdnet_seen.json"
HIST = ROOT / "docs" / "data" / "tdnet.json"
JST = timezone(timedelta(hours=9))
API = "https://webapi.yanoshin.jp/webapi/tdnet/list/{d}.json?limit=3000"
PAGE_URL = "https://marcona-japan.github.io/nikkei-sector-flow/tdnet.html"

# (タグ, 絵文字, 色, キーワード)  上から順に最初に当たったものを採用
TAGS = [
    ("TOB・M&A", "🏷️", 0x8E44AD, r"公開買付|ＴＯＢ|TOB|株式交換|株式移転|合併|子会社化|買収|事業譲渡|経営統合|MBO|ＭＢＯ"),
    ("業績修正", "📈", 0xD03B3B, r"業績予想の修正|業績修正|上方修正|下方修正|予想値と実績値との差異|特別(利益|損失)"),
    ("決算", "📊", 0x2A78D6, r"決算短信|決算説明|四半期"),
    ("配当", "💴", 0xEDA100, r"配当|株主優待"),
    ("自社株買い", "🔁", 0x1BAF7A, r"自己株式の取得|自己株式取得|自己株式の消却"),
    ("株式分割", "✂️", 0x1BAF7A, r"株式分割|株式併合"),
    ("資金調達", "🏦", 0xEB6834, r"新株予約権|第三者割当|募集株式|公募|増資|社債|売出し"),
    ("提携・受注", "🤝", 0x008300, r"提携|受注|契約締結|共同開発|採択|認可|承認"),
    ("事業・設備", "🏭", 0x4A3AA7, r"設備投資|増産|生産能力|新工場|新製品|事業開始|事業計画|中期経営計画"),
    ("役員・人事", "👤", 0x7A7873, r"代表取締役|役員|人事|異動"),
]


def tag_of(title: str):
    for name, emoji, color, pat in TAGS:
        if re.search(pat, title):
            return name, emoji, color
    return "その他", "📄", 0x52514E


def load_watch() -> dict[str, str]:
    with WATCH.open(encoding="utf-8") as f:
        return {r["code"].strip().upper(): r["name"].strip() for r in csv.DictReader(f)}


def fetch_day(d: date) -> list[dict]:
    last = None
    for i in range(3):
        try:
            r = requests.get(API.format(d=d.strftime("%Y%m%d")), timeout=60,
                             headers={"User-Agent": "nikkei-sector-flow/1.0"})
            r.raise_for_status()
            js = r.json()
            items = js.get("items", js) if isinstance(js, dict) else js
            return [it.get("Tdnet", it) for it in items]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"TDnet APIから取得できません: {last}")


def normalize(it: dict, watch: dict[str, str]) -> dict | None:
    cc = str(it.get("company_code", "")).strip().upper()
    code = cc[:4]
    if code not in watch:
        return None
    title = str(it.get("title", "")).strip()
    tag, emoji, _ = tag_of(title)
    return {
        "id": str(it.get("id")),
        "time": str(it.get("pubdate", ""))[:16],
        "code": code,
        "name": watch[code],
        "title": title,
        "url": it.get("document_url") or "",
        "tag": tag,
        "emoji": emoji,
    }


def embed(x: dict) -> dict:
    _, _, color = tag_of(x["title"])
    return {
        "title": f"{x['emoji']} {x['code']} {x['name']}"[:256],
        "description": f"**{x['title']}**"[:4000],
        "url": x["url"] or None,
        "color": color,
        "footer": {"text": f"{x['tag']}｜{x['time']}"},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（省略時は今日と前日）")
    ap.add_argument("--notify", choices=["auto", "force", "off"], default="auto",
                    help="auto=未通知分のみ / force=対象日を全部送る / off=送らない")
    a = ap.parse_args()

    watch = load_watch()
    today = datetime.now(JST).date()
    days = [date.fromisoformat(a.date)] if a.date else [today - timedelta(days=1), today]

    found: list[dict] = []
    for d in days:
        for it in fetch_day(d):
            x = normalize(it, watch)
            if x:
                found.append(x)
    found.sort(key=lambda x: (x["time"], x["code"]))
    print(f"監視{len(watch)}銘柄のうち該当開示 {len(found)}件")
    for x in found:
        print(f"  {x['time']} {x['code']} {x['name']} [{x['tag']}] {x['title']}")

    # 履歴（ページ用）を更新
    hist = json.loads(HIST.read_text()) if HIST.exists() else {"items": []}
    by_id = {h["id"]: h for h in hist["items"]}
    for x in found:
        by_id[x["id"]] = x
    cutoff = (today - timedelta(days=60)).isoformat()
    items = sorted((h for h in by_id.values() if h["time"][:10] >= cutoff),
                   key=lambda h: h["time"], reverse=True)
    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps({"updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
                                "watch": len(watch), "items": items},
                               ensure_ascii=False, separators=(",", ":")))

    # 通知
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    seen_ids = {i for ids in seen.values() for i in ids}
    targets = found if a.notify == "force" else [x for x in found if x["id"] not in seen_ids]
    if a.notify == "off" or not targets:
        print("通知なし" if not targets else "通知オフ")
    else:
        head = {"title": f"📰 監視銘柄の適時開示 {len(targets)}件", "url": PAGE_URL, "color": 0x0B0B0B,
                "description": "　".join(sorted({f"{x['code']} {x['name']}" for x in targets}))[:4000]}
        if notify.send([head] + [embed(x) for x in targets], "TDnet開示チェック", "tdnet"):
            for x in targets:
                seen.setdefault(x["time"][:10], []).append(x["id"])
    keep = {(today - timedelta(days=i)).isoformat() for i in range(7)}
    seen = {k: sorted(set(v)) for k, v in seen.items() if k in keep}
    SEEN.write_text(json.dumps(seen, ensure_ascii=False))


if __name__ == "__main__":
    main()
