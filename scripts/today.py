"""朝のFX指標アラート: 当日7:00〜翌朝7:00（日本時間）に予定された指標・中銀イベントを Discord に送る。

ドル円・ユーロドル・ポンドルに影響する日米欧英の指標を data/events.json から抽出する。
平日朝に実行（日本の祝日も送る。FXは動いているため）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "data" / "events.json"
JST = timezone(timedelta(hours=9))
PAIRS = {"JPY": "ドル円", "USD": "ドル円・ユーロドル・ポンドル", "EUR": "ユーロドル", "GBP": "ポンドル"}


def window_events(today: date) -> list[tuple[datetime | None, dict]]:
    start = datetime(today.year, today.month, today.day, 7, 0, tzinfo=JST)
    end = start + timedelta(days=1)
    out = []
    for e in json.loads(EVENTS.read_text())["events"]:
        d = date.fromisoformat(e["date"])
        if e.get("time"):
            h, m = map(int, e["time"].split(":"))
            t = datetime(d.year, d.month, d.day, h, m, tzinfo=JST)
            if start <= t < end:
                out.append((t, e))
        elif d == today:
            out.append((None, e))
    out.sort(key=lambda x: (x[0] is not None, x[0] or start))
    return out


def build(today: date) -> dict:
    rows = window_events(today)
    wd = "月火水木金土日"[today.weekday()]
    lines, top = [], []
    for t, e in rows:
        imp = e.get("imp", 2)
        star = "★" * imp + "☆" * (3 - imp)
        if t is None:
            when = "時刻未定"
        elif t.date() != today:
            when = f"翌{t:%H:%M}"
        else:
            when = f"{t:%H:%M}"
        title = f"**{e['title']}**" if imp >= 3 else e["title"]
        lines.append(f"`{when:>6}` {star} {title} `{e.get('ccy', '')}`")
        if imp >= 3:
            top.append(e)
    if not lines:
        desc = "本日（〜翌朝7:00）の予定指標はありません。"
        color = 0x2ECC71
    else:
        desc = "\n".join(lines)
        color = 0xE74C3C if top else 0xF1C40F
    fields = []
    if top:
        pairs = []
        for e in top:
            for pr in PAIRS.get(e.get("ccy", ""), "").split("・"):
                if pr and pr not in pairs:
                    pairs.append(pr)
        fields.append({"name": "⚠️ 注意",
                       "value": "★★★の前後はスプレッド拡大・急変に注意。影響: " + "・".join(pairs),
                       "inline": False})
    return {"title": f"📅 本日の指標 {today.month}/{today.day}({wd})　7:00〜翌7:00（日本時間）",
            "description": desc[:4000], "color": color, "fields": fields,
            "footer": {"text": "日程は各公式発表予定より。変更の可能性あり"}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--date", help="YYYY-MM-DD（テスト用）")
    a = ap.parse_args()
    today = date.fromisoformat(a.date) if a.date else datetime.now(JST).date()
    embed = build(today)
    print(json.dumps(embed, ensure_ascii=False, indent=1))
    if a.notify == "on":
        notify.send([embed], "FX指標アラート", "market")


if __name__ == "__main__":
    main()
