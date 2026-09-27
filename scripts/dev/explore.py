"""データ元の調査用（一時的）：生データを dev_dump/ に保存"""
import re, requests, pathlib
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
out = pathlib.Path("dev_dump"); out.mkdir(exist_ok=True)
def save(name, url):
    try:
        r = requests.get(url, headers=UA, timeout=40)
        (out / name).write_bytes(r.content); print(name, r.status_code, len(r.content))
        return r
    except Exception as e:
        print(name, "ERR", e)
save("y_up.html", "https://finance.yahoo.co.jp/stocks/ranking/up?market=all")
save("y_down.html", "https://finance.yahoo.co.jp/stocks/ranking/down?market=all")
save("y_topix.html", "https://finance.yahoo.co.jp/quote/998405.T")
save("y_vi.html", "https://finance.yahoo.co.jp/quote/998407.O")
r = save("jpx_daily.html", "https://www.jpx.co.jp/markets/statistics-equities/daily/index.html")
for i, l in enumerate(re.findall(r'href="([^"]+est-set_\d+\.pdf)"', r.text)[:1]):
    save("jpx_estset.pdf", "https://www.jpx.co.jp" + l)
r = save("jpx_short.html", "https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html")
for l in re.findall(r'href="([^"]+\.(?:pdf|xls|xlsx|csv))"', r.text)[:3]:
    save("jpx_short_" + l.split("/")[-1], "https://www.jpx.co.jp" + l if l.startswith("/") else l)
save("nk_vi.html", "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225vi")
save("nk_vi_daily.csv", "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_vi_daily_jp.csv")
save("jpx_top.html", "https://www.jpx.co.jp/markets/statistics-equities/index.html")
