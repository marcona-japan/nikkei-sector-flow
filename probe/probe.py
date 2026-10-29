import re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
h=requests.get("https://finance.yahoo.co.jp/quote/7203.T",headers=UA,timeout=30).text
u=h.replace('\\\\"','"').replace('\\"','"')
out=[]
for m in list(re.finditer(r'"price":"', u))[:8]:
    out.append(repr(u[max(0,m.start()-200):m.start()+250]))
for kw in ['"priceBoard','"mainStocksPriceBoard','"priceDateTime"','"priceTime"','"changePrice":"']:
    for m in list(re.finditer(re.escape(kw),u))[:4]:
        out.append(f"[{kw}] "+repr(u[max(0,m.start()-150):m.start()+200]))
open("probe/out.txt","w").write("\n\n".join(out)[:20000])
