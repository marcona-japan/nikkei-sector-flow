import re, requests, pathlib
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
out = pathlib.Path("dev_dump"); out.mkdir(exist_ok=True)
log = []
def save(name, url):
    try:
        r = requests.get(url, headers=UA, timeout=40)
        (out / name).write_bytes(r.content); log.append(f"{name} {r.status_code} {len(r.content)} {url}")
        return r
    except Exception as e:
        log.append(f"{name} ERR {e}")
urls = {f"m{i:02d}": f"https://www.jpx.co.jp/markets/statistics-equities/margin/{i:02d}.html" for i in range(1, 8)}
urls.update({"prog": "https://www.jpx.co.jp/markets/statistics-equities/program/index.html",
             "prog2": "https://www.jpx.co.jp/markets/derivatives/statistics/index.html",
             "arb3": "https://www.jpx.co.jp/markets/statistics-derivatives/sq/index.html"})
for k, u in urls.items():
    r = save(f"x_{k}.html", u)
    if r is not None and r.ok:
        t = re.search(r"<title>(.*?)</title>", r.text, re.S)
        links = re.findall(r'href="([^"]+\.(?:pdf|xls|xlsx|csv))"', r.text)
        log.append(f"  {k}: {t.group(1).strip() if t else ''} :: {links[:6]}")
        for i, l in enumerate(links[:2]):
            save(f"x_{k}_{i}_" + l.split("/")[-1], "https://www.jpx.co.jp" + l if l.startswith("/") else l)
(out / "log.txt").write_text("\n".join(log))
