"""場中速報のリアルタイム版（Yahoo!ファイナンス日本版の株価ページから）。

30分の区切りごとに、各銘柄の「当日の売買代金の累計」と現在値を記録し、前回の区切りとの差から
その30分の業種別売買代金シェアを出す。Yahoo Finance（米国版）の5分足が約20分遅れなのに対し、
区切りの数分後に届けられる。送信済みの記録は run.py の場中速報と共通（同じ枠は二重に送らない）。

記録: data/rt/{日付}_{対象}.json  {"snaps": [{"t": "HH:MM", "q": {code: [現在値, 前日終値, 売買代金(円)]}}]}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import constituents  # noqa: E402
import run  # noqa: E402

ROOT = run.ROOT
JST = run.JST
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}


def num(s: str) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


DIAG: list[str] = []


def quote(sess: requests.Session, code: str) -> tuple[str, list] | None:
    try:
        r = sess.get(f"https://finance.yahoo.co.jp/quote/{code}.T", timeout=20)
        h = r.text
        if r.status_code != 200:
            DIAG.append(f"{code}: HTTP {r.status_code}")
            return None
    except Exception as e:  # noqa: BLE001
        DIAG.append(f"{code}: {e}")
        return None
    u = h.replace('\\\\"', '"').replace('\\"', '"')
    px = re.search(r'"priceBoard":\{.*?"price":\{"value":"([\d,.]+)"', u)
    pv = re.search(r'"previousPrice":\{"name":"前日終値","value":"([\d,.]+)"', u)
    tv = re.search(r'"tradingValue":\{"name":"売買代金","value":"([\d,.]+)"', u)
    if not (px and pv):
        DIAG.append(f"{code}: 読み取り失敗 px={bool(px)} pv={bool(pv)} tv={bool(tv)} len={len(h)}")
        return None
    return code, [num(px.group(1)), num(pv.group(1)), (num(tv.group(1)) or 0) * 1000 if tv else 0.0]


def snapshot(codes: list[str]) -> dict:
    sess = requests.Session()
    sess.headers.update(UA)
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = [r for r in ex.map(lambda c: quote(sess, c), codes) if r]
    return dict(res)


def slot_shares(cur: dict, prev: dict | None, sec_of: dict, name_of: dict) -> dict:
    """2つの記録の売買代金の差から、業種別シェアと業種ごとの売買代金上位銘柄"""
    diff = {}
    for c, (_, _, tv) in cur.items():
        p = prev.get(c, [0, 0, 0])[2] if prev else 0.0
        v = max(tv - p, 0.0)
        if v > 0 and c in sec_of:
            diff[c] = v
    tot = sum(diff.values())
    if not tot:
        return {"total": 0, "shares": {}, "tops": {}}
    by = {}
    for c, v in diff.items():
        by.setdefault(sec_of[c], []).append((v, c))
    return {"total": round(tot),
            "shares": {s: round(sum(v for v, _ in xs) / tot * 100, 3) for s, xs in by.items()},
            "tops": {s: [[c, name_of.get(c, c)] for v, c in sorted(xs, reverse=True)[:5]] for s, xs in by.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["n225", "g250"], default="n225")
    ap.add_argument("--notify", choices=["auto", "force", "off"], default="auto")
    a = ap.parse_args()
    run.configure(a.universe)
    now = datetime.now(JST)
    today = now.date().isoformat()
    cons = constituents.load_universe(a.universe)
    sec_of = dict(zip(cons["code"], cons["sector"]))
    name_of = dict(zip(cons["code"], cons["name"]))

    q = snapshot(cons["code"].tolist())
    print(f"{now:%H:%M} 取得 {len(q)}/{len(cons)} 銘柄")
    diag = ROOT / "data" / "rt" / f"status_{a.universe}.txt"
    diag.parent.mkdir(parents=True, exist_ok=True)
    diag.write_text(f"{now:%Y-%m-%d %H:%M} 取得 {len(q)}/{len(cons)}\n" + "\n".join(DIAG[:20]))
    if len(q) < len(cons) * 0.8:
        print("取得できた銘柄が少ないため、今回は記録・通知しません")
        return
    if not any(v[2] > 0 for v in q.values()):
        print("売買代金が0（休場日か寄り付き前）")
        return
    path = ROOT / "data" / "rt" / f"{today}_{a.universe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = json.loads(path.read_text()) if path.exists() else {"snaps": []}
    rec["snaps"].append({"t": f"{now:%H:%M}", "q": q})
    path.write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    # 古い記録は7日で削除
    for f in path.parent.glob(f"*_{a.universe}.json"):
        if f.name[:10] < (now - timedelta(days=7)).date().isoformat():
            f.unlink()

    def hm(s):
        h, m = map(int, s.split(":"))
        return h * 60 + m

    snaps = [(hm(s["t"]), s["q"]) for s in rec["snaps"]]

    def snap_at(boundary: str):
        """区切り時刻以降・10分以内の最初の記録（昼休みは11:30の区切りを12:30まで使う）"""
        b = hm(boundary)
        lim = b + (60 if boundary == "11:30" else 10)
        for t, qq in snaps:
            if b <= t <= lim:
                return qq
        return None

    # 直近に終わった枠
    slots = run.SLOTS
    nm = now.hour * 60 + now.minute
    k = max((i for i, (_, e) in enumerate(slots) if hm(e) <= nm), default=None)
    if k is None:
        return
    st_k, en_k = slots[k]
    end_q = snap_at(en_k)
    start_q = {} if k == 0 else snap_at(slots[k - 1][1] if slots[k - 1][1] != "11:30" or st_k != "12:30" else "11:30")
    if end_q is None or start_q is None:
        print(f"{st_k}–{en_k} の前後の記録がそろわないため送りません（次の遅れ版に任せる）")
        return
    cur = slot_shares(end_q, start_q or None, sec_of, name_of)
    prev_sh = None
    if k >= 1:
        p_end = start_q
        p_start = {} if k == 1 else snap_at(slots[k - 2][1])
        if p_end is not None and p_start is not None:
            prev_sh = slot_shares(p_end, p_start or None, sec_of, name_of)

    base = {}
    try:
        lj = json.loads((run.OUT / "latest.json").read_text())
        base = {s["name"]: s["base"] for s in lj["sectors"]}
    except Exception:  # noqa: BLE001
        pass
    labels = [f"{s}–{e}" for s, e in slots]
    d_slots = [{"label": labels[i], "state": "pending", "total": 0, "shares": {}, "tops": {}} for i in range(len(slots))]
    d_slots[k] = {"label": labels[k], "state": "done", **cur}
    if k >= 1:
        if prev_sh:
            d_slots[k - 1] = {"label": labels[k - 1], "state": "done", **prev_sh}
        else:
            d_slots[k - 1] = {"label": "20日平均", "state": "done", "total": 0, "shares": base, "tops": {}}
    stocks = {c: [name_of.get(c, c), px, round(px - pv, 1), round((px / pv - 1) * 100, 2)]
              for c, (px, pv, _) in q.items() if px and pv}
    d = {"date": today, "slots": d_slots, "sectors": [{"name": n, "base": b} for n, b in base.items()], "stocks": stocks}

    st = json.loads(run.WSTATE.read_text()) if run.WSTATE.exists() else {}
    sent = st.get(today, [])
    key = labels[k]
    if a.notify == "off" or (a.notify == "auto" and key in sent):
        print("送信済み/通知オフ:", key)
        return
    embs = run.slot_embeds(d, k)
    for e in embs:
        if "description" in e:
            e["description"] = e["description"].replace("約20分遅れ", "ほぼリアルタイム")
    if embs:
        embs[-1].setdefault("footer", {})["text"] = f"{run.LABEL}構成銘柄・Yahoo!ファイナンス（リアルタイム）の売買代金から算出。投資判断はご自身で。"
    print(json.dumps(embs, ensure_ascii=False)[:1500])
    if embs and run.send_embeds(embs):
        sent.append(key)
        st[today] = sorted(set(sent))
        run.WSTATE.write_text(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
