"""日経225構成銘柄と東証33業種の対応表を作る。

- 構成銘柄: 日経インデックス公式ページ
- 33業種: JPX「東証上場銘柄一覧」(data_j.xls)
結果は data/constituents.csv に保存（週1回更新、失敗時は前回分を使う）。
"""
from __future__ import annotations

import io
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "constituents.csv"
STAMP = ROOT / "data" / "constituents_updated.txt"  # git checkoutでmtimeが変わるため日付を別保存
NIKKEI_URL = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
UA = {"User-Agent": "Mozilla/5.0 (compatible; nikkei-sector-flow/1.0)"}
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")


def fetch_nikkei() -> pd.DataFrame:
    html = requests.get(NIKKEI_URL, headers=UA, timeout=30).text
    rows = []
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        code_col = next((c for c in cols if "コード" in c or c.lower() == "code"), None)
        name_col = next((c for c in cols if "銘柄名" in c or c.lower() == "name"), None)
        if not code_col or not name_col:
            continue
        for _, r in t.iterrows():
            code = str(r[code_col]).strip().upper()
            if code.endswith(".0"):
                code = code[:-2]
            if CODE_RE.match(code):
                rows.append((code, str(r[name_col]).strip()))
    df = pd.DataFrame(rows, columns=["code", "name"]).drop_duplicates("code")
    if not 200 <= len(df) <= 230:
        raise RuntimeError(f"日経225の銘柄数が想定外: {len(df)}")
    return df


def fetch_jpx() -> pd.DataFrame:
    raw = requests.get(JPX_URL, headers=UA, timeout=60).content
    x = pd.read_excel(io.BytesIO(raw), dtype=str)
    x = x.rename(columns={"コード": "code", "33業種コード": "s33_code", "33業種区分": "sector"})
    x["code"] = x["code"].str.strip().str.upper()
    return x[["code", "s33_code", "sector"]]


def build() -> pd.DataFrame:
    nk = fetch_nikkei()
    jpx = fetch_jpx()
    df = nk.merge(jpx, on="code", how="left")
    missing = df[df["sector"].isna() | (df["sector"] == "-")]
    if len(missing):
        print("33業種が見つからない銘柄:", missing.to_dict("records"), file=sys.stderr)
        df = df.drop(missing.index)
    df = df.sort_values(["s33_code", "code"]).reset_index(drop=True)
    CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV, index=False)
    STAMP.write_text(datetime.now(timezone.utc).date().isoformat())
    print(f"constituents.csv 更新: {len(df)}銘柄 / {df['sector'].nunique()}業種")
    return df


def load(max_age_days: int = 7) -> pd.DataFrame:
    if CSV.exists():
        cached = pd.read_csv(CSV, dtype=str)
        try:
            last = datetime.fromisoformat(STAMP.read_text().strip()).date()
        except Exception:  # noqa: BLE001
            last = datetime(2000, 1, 1).date()
        if datetime.now(timezone.utc).date() - last < timedelta(days=max_age_days):
            return cached
        try:
            return build()
        except Exception as e:  # noqa: BLE001
            print(f"構成銘柄の更新に失敗、前回分を使用: {e}", file=sys.stderr)
            return cached
    return build()


if __name__ == "__main__":
    build()
