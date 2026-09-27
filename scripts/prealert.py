"""★★以上の指標の15分前リマインド（Discord）。

GitHub Actions の定時実行は数分〜十数分遅れるため、毎時 :05 と :35 に起動し、
担当枠（起動枠+25分〜+55分）に通知時刻が入るイベントまで runner 内で待ってから送る。
→ 起動が最大25分遅れても時刻どおりに届く。枠が重ならないので二重送信しない。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "data" / "events.json"
JST = timezone(timedelta(hours=9))
LEAD = timedelta(minutes=15)
PAIRS = {"JPY": "ドル円", "USD": "ドル円・ユーロドル・ポンドル", "EUR": "ユーロドル", "GBP": "ポンドル"}


def nominal_slot(now: datetime) -> datetime:
    """起動予定枠（:05 / :35）に丸める"""
    base = now.replace(second=0, microsecond=0)
    m = base.minute
    if m >= 35:
        return base.replace(minute=35)
    if m >= 5:
        return base.replace(minute=5)
    return (base - timedelta(hours=1)).replace(minute=35)


def events_at(min_imp: int) -> list[tuple[datetime, dict]]:
    out = []
    for e in json.loads(EVENTS.read_text())["events"]:
        if e.get("imp", 2) < min_imp or not e.get("time"):
            continue
        d = date.fromisoformat(e["date"])
        h, m = map(int, e["time"].split(":"))
        out.append((datetime(d.year, d.month, d.day, h, m, tzinfo=JST), e))
    return sorted(out, key=lambda x: x[0])


def message(t: datetime, group: list[dict]) -> tuple[str, dict]:
    names = " / ".join(e["title"] for e in group)
    content = f"⏰ 15分前　{t:%H:%M} {names}"
    pairs = []
    for e in group:
        for pr in PAIRS.get(e.get("ccy", ""), "").split("・"):
            if pr and pr not in pairs:
                pairs.append(pr)
    lines = [f"{'★' * e.get('imp', 2) + '☆' * (3 - e.get('imp', 2))} **{e['title']}** `{e.get('ccy', '')}`" for e in group]
    embed = {
        "title": f"⏰ {t:%H:%M} 発表まであと15分",
        "description": "\n".join(lines),
        "color": 0xE74C3C if any(e.get("imp", 2) >= 3 for e in group) else 0xF1C40F,
        "fields": [
            {"name": "影響", "value": "・".join(pairs) or "—", "inline": False},
            {"name": "確認", "value": "ポジション量・逆指値／発表直後のスプレッド拡大・急変に注意", "inline": False},
        ],
        "footer": {"text": "日程は data/events.json（公式発表予定ベース）"},
    }
    return content, embed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--min-imp", type=int, default=2)
    ap.add_argument("--now", help="テスト用 'YYYY-MM-DD HH:MM'（JST）。指定時は待たない")
    a = ap.parse_args()
    test = bool(a.now)
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=JST) if test else datetime.now(JST)
    slot = nominal_slot(now)
    lo, hi = slot + timedelta(minutes=25), slot + timedelta(minutes=55)
    print(f"now={now:%m/%d %H:%M} slot={slot:%H:%M} 担当: 通知{lo:%H:%M}〜{hi:%H:%M}")

    groups: dict[datetime, list[dict]] = {}
    for t, e in events_at(a.min_imp):
        if lo <= t - LEAD < hi:
            groups.setdefault(t, []).append(e)
    if not groups:
        print("対象なし")
        return
    for t in sorted(groups):
        fire = t - LEAD
        wait = (fire - datetime.now(JST)).total_seconds()
        if not test and wait > 0:
            print(f"{fire:%H:%M} まで {wait / 60:.1f}分待機")
            time.sleep(wait)
        content, embed = message(t, groups[t])
        print(content)
        if a.notify == "on":
            notify.send([embed], "指標リマインド", "market", content=content)


if __name__ == "__main__":
    main()
