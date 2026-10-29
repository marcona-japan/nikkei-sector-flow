"""常駐スケジューラー（GitHub Actions の定時実行の代わり）。

GitHub Actions の schedule は混雑時に数時間遅れたり実行されなかったりするため、
1本のジョブを約5時間動かし続け、その中で各通知を日本時間の決まった時刻に実行する。
5時間たったら次のジョブを workflow_dispatch で起動して引き継ぐ（同時に2本が動いても、
起動から5時間以内の別ジョブがあれば後から来た方は何もせず終わる）。

- リポジトリに書き込む処理（適時開示・セクター集計・マーケット概況）は順番に実行して、その都度 commit/push。
- 時刻まで内部で待つ処理（ランキング・指標15分前・決済リマインドなど）はバックグラウンドで実行。
- 実行済みの記録は data/scheduler_done.json（引き継ぎ・再起動時の二重送信防止）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DONE = ROOT / "data" / "scheduler_done.json"
JST = timezone(timedelta(hours=9))
RUN_HOURS = float(os.environ.get("RUN_HOURS", "5"))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
PY = sys.executable


def wd(d: date) -> int:  # 0=月 … 6=日
    return d.weekday()


def hm_list(*items) -> list[str]:
    return [x for x in items]


def every(start: str, end: str, step: int) -> list[str]:
    h, m = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    t, out = h * 60 + m, []
    while t <= eh * 60 + em:
        out.append(f"{t // 60:02d}:{t % 60:02d}")
        t += step
    return out


def tasks_for(d: date) -> list[dict]:
    """その日の実行予定。kind: sync=順番に実行してcommit / bg=バックグラウンド"""
    w = wd(d)
    weekday = w < 5
    T = []

    def add(name, times, cmd, kind="bg", catch=20, writer=False):
        for t in times:
            T.append({"key": f"{name}@{t}", "name": name, "time": t, "cmd": cmd, "kind": kind,
                      "catch": catch, "writer": writer})

    if weekday:
        # 適時開示（:02/:07/:32/:37、15:00〜16:55は5分おき）
        td = sorted(set([x for h in range(8, 21) for x in (f"{h:02d}:02", f"{h:02d}:07", f"{h:02d}:32", f"{h:02d}:37")]
                        + every("15:00", "16:55", 5)))
        add("tdnet", td, ["scripts/tdnet.py", "--notify", "auto"], "sync", catch=4, writer=True)
        # セクター資金移動（日経225・グロース250）
        sec = ["09:50"] + every("10:20", "15:50", 30) + ["16:00", "16:40"]
        add("sector", sec, ["@sector"], "sync", catch=25, writer=True)
        # 場中速報のリアルタイム版（日経225で試行）。区切りの1〜2分後に記録・送信
        rt = ["09:31", "10:01", "10:31", "11:01", "11:31", "12:31", "13:01", "13:31", "14:01", "14:31", "15:01", "15:32"]
        add("rt", rt, ["scripts/rt.py", "--universe", "n225", "--notify", "auto"], "sync", catch=8, writer=True)
        add("ranking", ["11:40", "15:40"], ["scripts/ranking.py", "--notify", "on"], catch=40)
        add("market", ["18:15"], ["scripts/market.py", "--notify", "on"], "sync", catch=90, writer=True)
    if w == 4:
        add("weekly", ["18:30"], ["scripts/weekly.py", "--notify", "on"], catch=120)
    if w <= 5:
        add("today", ["07:30"], ["scripts/today.py", "--notify", "on"], catch=60)
    if w in (0, 2, 3, 4):
        add("closing", ["21:30"], ["scripts/closing.py", "--notify", "on"], catch=25)
    if w == 1:
        add("closing", ["23:30"], ["scripts/closing.py", "--notify", "on"], catch=25)
    if w == 6:
        add("weekend", ["18:00"], ["scripts/weekend.py", "--notify", "on"], catch=120)
    # 指標15分前リマインド（毎日 :05 / :35。中で時刻まで待つ）
    add("prealert", [x for h in range(24) for x in (f"{h:02d}:05", f"{h:02d}:35")],
        ["scripts/prealert.py", "--notify", "on"], catch=15)
    return T


def load_done() -> dict:
    try:
        return json.loads(DONE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_done(done: dict, today: date) -> None:
    keep = {(today - timedelta(days=i)).isoformat() for i in range(3)}
    done = {k: v for k, v in done.items() if k in keep}
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))


def sh(*args, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=ROOT, text=True, capture_output=True, check=check)


def git_sync(msg: str) -> None:
    """data/・docs/data/ の変更を commit して push（他のジョブの更新は rebase で取り込む）"""
    sh("git", "add", "-A", "data", "docs/data")
    if sh("git", "diff", "--cached", "--quiet").returncode == 0:
        sh("git", "pull", "--rebase", "-q")
        return
    sh("git", "commit", "-q", "-m", msg)
    for _ in range(3):
        p = sh("git", "pull", "--rebase", "-q")
        if p.returncode != 0:
            sh("git", "rebase", "--abort")
            sh("git", "reset", "--soft", "origin/main")
            continue
        if sh("git", "push", "-q").returncode == 0:
            return
        time.sleep(5)
    print("push に失敗:", msg)


def run_sync(task: dict) -> None:
    now = datetime.now(JST)
    sh("git", "pull", "--rebase", "-q")  # 最新のスクリプトで実行する
    if task["cmd"] == ["@sector"]:
        for extra in ([], ["--universe", "g250"]):
            p = sh(PY, "scripts/run.py", "--notify", "auto", *extra)
            print(p.stdout[-1500:], p.stderr[-800:])
    else:
        p = sh(PY, *task["cmd"])
        print(p.stdout[-1500:], p.stderr[-800:])
    git_sync(f"{task['name']}: {now:%Y-%m-%d %H:%M}")


def other_active_run() -> bool:
    """起動から RUN_HOURS 時間以内の別のスケジューラーが動いていれば True"""
    if not (REPO and TOKEN):
        return False
    import requests
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/actions/workflows/scheduler.yml/runs",
                         params={"status": "in_progress", "per_page": 20},
                         headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        print("実行中ジョブの確認に失敗:", e)
        return False
    me = None
    for x in r.get("workflow_runs", []):
        if str(x["id"]) == RUN_ID:
            me = x
    for x in r.get("workflow_runs", []):
        if str(x["id"]) == RUN_ID:
            continue
        started = datetime.fromisoformat(x["run_started_at"].replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - started).total_seconds() / 3600
        if age_h < RUN_HOURS - 0.05 and (me is None or x["id"] < me["id"]):
            print(f"別のスケジューラー（run {x['id']}、起動{age_h:.1f}時間前）が動いているため終了")
            return True
    return False


def dispatch_next() -> None:
    if not (REPO and TOKEN):
        return
    import requests
    r = requests.post(f"https://api.github.com/repos/{REPO}/actions/workflows/scheduler.yml/dispatches",
                      json={"ref": "main"}, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    print("次のスケジューラーを起動:", r.status_code)


def main() -> None:
    if other_active_run():
        return
    start = time.time()
    bg: list[subprocess.Popen] = []
    print(f"スケジューラー開始 {datetime.now(JST):%m/%d %H:%M} JST（{RUN_HOURS}時間）")
    while time.time() - start < RUN_HOURS * 3600:
        now = datetime.now(JST)
        done = load_done()
        changed = False
        due = []
        for d in (now.date() - timedelta(days=1), now.date()):
            for t in tasks_for(d):
                h, m = map(int, t["time"].split(":"))
                at = datetime(d.year, d.month, d.day, h, m, tzinfo=JST)
                key = t["key"]
                if key in done.get(d.isoformat(), []):
                    continue
                if at <= now <= at + timedelta(minutes=t["catch"]):
                    due.append((at, d, t))
        # 同じ処理が複数たまっていたら最新の1回だけ（例: 適時開示）
        latest = {}
        for at, d, t in sorted(due, key=lambda x: x[0]):
            latest[t["name"]] = (at, d, t)
        skipped = [(d, t) for at, d, t in due if latest[t["name"]][2] is not t]
        for d, t in skipped:
            done.setdefault(d.isoformat(), []).append(t["key"])
            changed = True
        for at, d, t in sorted(latest.values(), key=lambda x: x[0]):
            done.setdefault(d.isoformat(), []).append(t["key"])
            save_done(done, now.date())
            print(f"[{datetime.now(JST):%H:%M:%S}] 実行: {t['key']}")
            if t["kind"] == "sync":
                run_sync(t)
            else:
                sh("git", "pull", "--rebase", "-q")
                bg.append(subprocess.Popen([PY, *t["cmd"]], cwd=ROOT))
                git_sync(f"scheduler: {t['key']}")
            changed = False
        if changed:
            save_done(done, now.date())
            git_sync("scheduler: skip")
        bg = [p for p in bg if p.poll() is None]
        time.sleep(30 - (time.time() % 30) + 1)
    # 引き継ぎ: 次のジョブを起動してから、待機中のバックグラウンド処理の終了を待つ
    dispatch_next()
    for p in bg:
        try:
            p.wait(timeout=55 * 60)
        except subprocess.TimeoutExpired:
            p.kill()
    print("スケジューラー終了")


if __name__ == "__main__":
    main()
