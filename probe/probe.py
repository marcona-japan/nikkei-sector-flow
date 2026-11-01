import re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
out=[]
s=requests.Session(); s.headers.update(UA)
h=s.get("https://www.matsui.co.jp/market/stock/netstock-info/",timeout=30).content.decode("utf-8","ignore")
i=h.find("投資指標(松井証券店内)</h1>"); 
for kw in ["買い方","売り方","評価損益率","data-src","data-url","api","/netstock","xlsx","iframe"]:
    for m in list(re.finditer(re.escape(kw),h))[:6]:
        out.append(f"[{kw}] "+re.sub(r"\s+"," ",h[max(0,m.start()-200):m.start()+250]))
js=s.get("https://www.matsui.co.jp/assets/www/v2/js/main.js",timeout=30).text
for kw in ["netstock","rate","json","api/"]:
    for m in list(re.finditer(kw,js))[:6]:
        out.append(f"[js {kw}] "+js[max(0,m.start()-150):m.start()+200])
open("probe/out.txt","w").write("\n\n".join(out)[:20000])
