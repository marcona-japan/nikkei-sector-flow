"""FX ポジションの決済時刻リマインド（Discord）。

ルール: 平日は 22:00 までに全決済。休み前の火曜だけ 24:00 まで。
決済時刻の15分前と、決済時刻ちょうどに送る。
GitHub Actions の起動遅れに備えて30分前に起動し、runner 内で時刻まで待つ。
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


def cutoff_for(d: date) -> datetime | None:
    """その日の決済時刻。土日はなし。火曜は24:00（翌0:00）、ほかは22:00"""
    if d.weekday() >= 5:
        return None
    base = datetime(d.year, d.month, d.day, tzinfo=JST)
    return base + (timedelta(hours=24) if d.weekday() == 1 else timedelta(hours=22))


def later_events(start: datetime, hours: int = 10) -> list[str]:
    """決済時刻のあと（翌朝まで）にある★★以上の指標＝持ち越すと当たる材料"""
    out = []
    for e in json.loads(EVENTS.read_text())["events"]:
        if e.get("imp", 2) < 2 or not e.get("time"):
            continue
        d = date.fromisoformat(e["date"])
        h, m = map(int, e["time"].split(":"))
        t = datetime(d.year, d.month, d.day, h, m, tzinfo=JST)
        if start <= t < start + timedelta(hours=hours):
            star = "★" * e.get("imp", 2) + "☆" * (3 - e.get("imp", 2))
            out.append(f"{t:%H:%M} {star} {e['title']} `{e.get('ccy', '')}`")
    return out


def message(cut: datetime, final: bool) -> dict:
    hm = "24:00" if cut.hour == 0 else f"{cut:%H:%M}"
    ev = later_events(cut)
    if final:
        title = f"🛑 {hm} 決済時刻です — ポジションは全部閉じる"
        desc = "オーバーナイトは負けやすい。今日はここで終了。"
        color = 0xD03B3B
    else:
        title = f"⏰ {hm} の決済まで あと15分"
        desc = "新規エントリーは控えて、保有ポジションの手じまいを準備。"
        color = 0xF1C40F
    emb = {"title": title, "description": desc, "color": color}
    if ev:
        emb["fields"] = [{"name": "⚠️ このあと（翌朝まで）の重要指標", "value": "\n".join(ev)[:1024], "inline": False}]
    emb["footer"] = {"text": "ルール: 平日22:00まで・休み前の火曜は24:00まで"}
    return emb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", choices=["on", "off"], default="on")
    ap.add_argument("--test", action="store_true", help="待たずに2通とも今すぐ送る")
    a = ap.parse_args()

    now = datetime.now(JST)
    # 起動は決済の約30分前。日付の境目（火曜24:00）は前日扱いで探す
    cut = next((c for c in (cutoff_for(now.date()), cutoff_for(now.date() - timedelta(days=1)))
                if c and timedelta(0) <= c - now <= timedelta(minutes=70)), None)
    if a.test:
        cut = cutoff_for(now.date()) or cutoff_for(now.date() + timedelta(days=(7 - now.weekday())))
    if not cut:
        print(f"{now:%m/%d %H:%M} は決済時刻の前ではないため何もしません")
        return
    for final, at in ((False, cut - timedelta(minutes=15)), (True, cut)):
        if not a.test:
            wait = (at - datetime.now(JST)).total_seconds()
            if wait < -300:  # 5分以上過ぎていたら送らない（遅延で古い通知を出さない）
                print("時刻を過ぎたためスキップ:", at)
                continue
            if wait > 0:
                time.sleep(wait)
        emb = message(cut, final)
        if a.test:
            emb["title"] = "【テスト】" + emb["title"]
        print(json.dumps(emb, ensure_ascii=False))
        if a.notify == "on":
            notify.send([emb], "決済リマインド", "market")


if __name__ == "__main__":
    main()
