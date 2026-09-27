"""データ元の調査用（一時的）：生データを dev_dump/ に保存"""
import re, requests, pathlib
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
out = pathlib.Path("dev_dump"); out.mkdir(exist_ok=True)
def save(name, url):
    try:
        r = requests.get(url, headers=UA, timeout=40)
        (out / name).write_bytes(r.content); print(name, r.status_code, len(r.content), url)
        return r
    except Exception as e:
        print(name, "ERR", e)
pages = {
 "inv": "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html",
 "margin": "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html",
 "arb": "https://www.jpx.co.jp/markets/statistics-derivatives/arbitrage/index.html",
 "arb2": "https://www.jpx.co.jp/markets/statistics-equities/arbitrage/index.html",
}
for k, u in pages.items():
    r = save(f"w_{k}.html", u)
    if r is not None and r.ok:
        links = re.findall(r'href="([^"]+\.(?:pdf|xls|xlsx|csv))"', r.text)
        print(k, links[:12])
        for i, l in enumerate(links[:4]):
            save(f"w_{k}_{i}_" + l.split("/")[-1], "https://www.jpx.co.jp" + l if l.startswith("/") else l)
save("w_mof_week.csv", "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv")
save("w_mof_page.html", "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.htm")
