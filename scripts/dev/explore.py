import requests, pathlib
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
out = pathlib.Path("dev_dump"); out.mkdir(exist_ok=True)
for n, u in {"ipo.html": "https://www.jpx.co.jp/listing/stocks/new/index.html",
             "y_627A.html": "https://finance.yahoo.co.jp/quote/627A.T"}.items():
    r = requests.get(u, headers=UA, timeout=40); (out / n).write_bytes(r.content); print(n, r.status_code)
