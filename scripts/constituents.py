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
JPX_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
JPX_FALLBACK = [
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
]
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
    x = fetch_jpx_all()
    x = x.rename(columns={"コード": "code", "33業種コード": "s33_code", "33業種区分": "sector"})
    x["code"] = x["code"].str.strip().str.upper()
    return x[["code", "s33_code", "sector"]]


def fetch_jpx_all() -> pd.DataFrame:
    """JPX「東証上場銘柄一覧」の全列（コード・銘柄名・市場・商品区分・33業種など）"""
    urls = []
    try:  # 掲載ページからファイルのリンクを探す（拡張子やURLが変わっても追従）
        page = requests.get(JPX_PAGE, headers=UA, timeout=30).text
        for m in re.findall(r'href="([^"]*data_j\.xlsx?)"', page):
            urls.append(requests.compat.urljoin(JPX_PAGE, m))
    except Exception as e:  # noqa: BLE001
        print("JPX掲載ページの取得に失敗:", e, file=sys.stderr)
    last = None
    for url in urls + JPX_FALLBACK:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200 or r.content[:1] == b"<":
            last = f"{url} -> {r.status_code}"
            continue
        engine = "openpyxl" if r.content[:2] == b"PK" else "xlrd"
        x = pd.read_excel(io.BytesIO(r.content), dtype=str, engine=engine)
        break
    else:
        raise RuntimeError(f"JPX上場銘柄一覧を取得できません: {last}")
    return x


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


# ---------------------------------------------------------------- 東証グロース市場250指数
G250_CSV = ROOT / "data" / "constituents_g250.csv"
G250_STAMP = ROOT / "data" / "constituents_g250_updated.txt"
G250_PAGE = "https://www.jpx.co.jp/markets/indices/line-up/index.html"
G250_FALLBACK = "https://www.jpx.co.jp/markets/indices/line-up/files/mei2_31_mothers.pdf"


def fetch_g250_codes() -> list[str]:
    """JPX総研「東証グロース市場250指数 構成銘柄一覧」PDFの「指数構成銘柄」欄からコードを読む"""
    import pdfplumber
    urls = []
    try:
        page = requests.get(G250_PAGE, headers=UA, timeout=30).text
        urls += [requests.compat.urljoin(G250_PAGE, m) for m in re.findall(r'href="([^"]*mei2?_\d+_mothers\.pdf)"', page)]
    except Exception as e:  # noqa: BLE001
        print("JPX指数ページの取得に失敗:", e, file=sys.stderr)
    for url in dict.fromkeys(urls + [G250_FALLBACK]):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            continue
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        i = text.find("指数構成銘柄")
        body = text[i:] if i >= 0 else text
        codes = list(dict.fromkeys(re.findall(r"(?<![0-9A-Z])([1-9][0-9][0-9A-Z][0-9])(?![0-9A-Z])", body)))
        if 200 <= len(codes) <= 320:
            return codes
        print(f"{url}: コード数が想定外 {len(codes)}", file=sys.stderr)
    raise RuntimeError("グロース250の構成銘柄を取得できません")


def build_g250() -> pd.DataFrame:
    codes = fetch_g250_codes()
    x = fetch_jpx_all()
    x["code"] = x["コード"].astype(str).str.strip().str.upper()
    x = x.rename(columns={"銘柄名": "name", "33業種コード": "s33_code", "33業種区分": "sector"})
    df = pd.DataFrame({"code": codes}).merge(x[["code", "name", "s33_code", "sector"]], on="code", how="left")
    df = df.dropna(subset=["sector"])
    df = df[df["sector"] != "-"].sort_values(["s33_code", "code"]).reset_index(drop=True)
    df.to_csv(G250_CSV, index=False)
    G250_STAMP.write_text(datetime.now(timezone.utc).date().isoformat())
    print(f"constituents_g250.csv 更新: {len(df)}銘柄 / {df['sector'].nunique()}業種")
    return df


def load_universe(universe: str = "n225", max_age_days: int = 7) -> pd.DataFrame:
    if universe == "n225":
        return load(max_age_days)
    if G250_CSV.exists():
        cached = pd.read_csv(G250_CSV, dtype=str)
        try:
            last = datetime.fromisoformat(G250_STAMP.read_text().strip()).date()
        except Exception:  # noqa: BLE001
            last = datetime(2000, 1, 1).date()
        if datetime.now(timezone.utc).date() - last < timedelta(days=max_age_days):
            return cached
        try:
            return build_g250()
        except Exception as e:  # noqa: BLE001
            print(f"グロース250の更新に失敗、前回分を使用: {e}", file=sys.stderr)
            return cached
    return build_g250()


if __name__ == "__main__":
    build()
