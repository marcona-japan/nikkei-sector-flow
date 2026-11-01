import re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
out=[]
for u in ["https://www.matsui.co.jp/market/stock/netstock-info/","https://www.matsui.co.jp/market/stock/netstock-info/latest.html","https://www.matsui.co.jp/market/stock/netstock-info/rate/"]:
    try:
        r=requests.get(u,headers=UA,timeout=30); r.encoding=r.apparent_encoding; h=r.text
        out.append(f"== {u} {r.status_code} {len(h)} final={r.url}")
        out.append("links: "+" ".join(sorted(set(re.findall(r'href="([^"]+\.(?:xlsx?|csv|json))"',h)))))
        out.append("scripts/json: "+" ".join(sorted(set(re.findall(r'["\']([^"\']+\.(?:json|js)(?:\?[^"\']*)?)["\']',h)))[:30]))
        t=re.sub(r"<[^>]+>"," ",h); t=re.sub(r"\s+"," ",t)
        for m in list(re.finditer("評価損益率",t))[:8]:
            out.append("…"+t[max(0,m.start()-120):m.start()+200])
    except Exception as e: out.append(f"{u} ERR {e}")
open("probe/out.txt","w").write("\n".join(out)[:15000])
