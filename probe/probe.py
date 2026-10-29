import re, requests, time
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
out=[]
for code in ["7203","8035","5803"]:
    h=requests.get(f"https://finance.yahoo.co.jp/quote/{code}.T",headers=UA,timeout=30).text
    u=h.replace('\\\\"','"').replace('\\"','"')
    out.append(f"== {code} {time.strftime('%H:%M:%S')}")
    for kw in ['"tradingValue":{','"volume":{','"price":"','"changePrice','"changePriceRate','"previousPrice','"priceDateTime','"updateDateTime','"isRealTime"','"savePrice','"closePrice']:
        for m in list(re.finditer(re.escape(kw),u))[:2]:
            out.append(f"[{kw}] {u[max(0,m.start()-80):m.start()+260]!r}")
open("probe/out.txt","w").write("\n".join(out)[:30000])
