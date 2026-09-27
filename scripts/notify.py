"""Discord Webhook 送信の共通処理。

チャンネルを分けたいときは用途別のSecretを登録する（未登録なら DISCORD_WEBHOOK_URL を使う）:
  DISCORD_WEBHOOK_TDNET / DISCORD_WEBHOOK_RANKING
"""
from __future__ import annotations

import os
import time

import requests


def webhook(kind: str | None = None) -> str | None:
    if kind:
        v = os.environ.get(f"DISCORD_WEBHOOK_{kind.upper()}")
        if v:
            return v
    return os.environ.get("DISCORD_WEBHOOK_URL") or None


def send(embeds: list[dict], username: str, kind: str | None = None) -> bool:
    url = webhook(kind)
    if not url:
        print("Discord Webhook 未設定のため送信スキップ")
        return False
    # Discordは1メッセージ最大10 embed
    for i in range(0, len(embeds), 10):
        for attempt in range(3):
            r = requests.post(url, json={"username": username, "embeds": embeds[i:i + 10]}, timeout=30)
            if r.status_code == 429:  # レート制限
                time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
                continue
            print("Discord:", r.status_code, r.text[:200])
            r.raise_for_status()
            break
        time.sleep(1)
    return True
