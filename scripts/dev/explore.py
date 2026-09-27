"""データ元の調査用（一時的）"""
import io, json, re, requests
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "ja"}
def get(u):
    r = requests.get(u, headers=UA, timeout=30); print(f"\n=== {u} -> {r.status_code} {len(r.content)}B {r.headers.get('content-type')}"); return r
def state(html):
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    return json.loads(m.group(1)) if m else None
# 1) Yahoo ranking links & market params
r = get("https://finance.yahoo.co.jp/stocks/ranking/up?market=all")
print(sorted(set(re.findall(r'href="(/stocks/ranking/[^"]+)"', r.text)))[:80])
print(re.findall(r"(\d[\d,]*)件中", r.text)[:3])
for key in ["down", "yearHigh", "year-high", "yearLow", "year-low", "highPrice", "lowPrice"]:
    for mk in ["all", "tokyo1", "tokyoPrime", "prime"]:
        rr = requests.get(f"https://finance.yahoo.co.jp/stocks/ranking/{key}?market={mk}", headers=UA, timeout=30)
        c = re.findall(r"([\d,]+)件中", rr.text)[:1]
        print(key, mk, rr.status_code, c)
# 2) Yahoo quote pages
for q in ["998405.T", "998407.O", "USDJPY=FX"]:
    r = get(f"https://finance.yahoo.co.jp/quote/{q}")
    st = state(r.text)
    if st:
        s = json.dumps(st, ensure_ascii=False)
        i = s.find('"price"'); print(s[max(0, i-300):i+600])
# 3) JPX daily PDF (概算・精算表)
r = get("https://www.jpx.co.jp/markets/statistics-equities/daily/index.html")
links = re.findall(r'href="([^"]+est-set_\d+\.pdf)"', r.text); print(links[:3])
if links:
    import pdfplumber
    pr = get("https://www.jpx.co.jp" + links[0] if links[0].startswith("/") else links[0])
    with pdfplumber.open(io.BytesIO(pr.content)) as pdf:
        for p in pdf.pages[:2]:
            print("----- page -----"); print((p.extract_text() or "")[:3000])
# 4) 空売り
r = get("https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html")
print(sorted(set(re.findall(r'href="([^"]+\.(?:pdf|xls|xlsx|csv))"', r.text)))[:10])
# 5) 日経VI
for u in ["https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_vi_daily_jp.csv",
          "https://indexes.nikkei.co.jp/nkave/historical/nikkei_225_vi_daily_jp.csv",
          "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225vi"]:
    r = get(u); print(r.text[:600] if r.ok else "")
    if "profile" in u: print(re.findall(r"(\d{2}\.\d{2})", r.text)[:10])
