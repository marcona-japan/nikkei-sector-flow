import json, re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
r=requests.get("https://finance.yahoo.co.jp/quote/7203.T",headers=UA,timeout=30); h=r.text
out=[]
i=h.find("__PRELOADED_STATE__"); out.append(f"state idx {i}: {h[i:i+200]!r}")
for kw in ["売買代金","出来高","リアルタイム","tradingValue","volume\"","priceDateTime","updateDateTime","realTime"]:
    for m in list(re.finditer(re.escape(kw),h))[:3]:
        out.append(f"[{kw}] {re.sub(r'<[^>]+>',' ',h[max(0,m.start()-150):m.start()+200])!r}")
m=re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>",h,re.S)
if m:
    st=json.loads(m.group(1)); out.append("keys: "+",".join(st.keys()))
    def walk(o,p=""):
        if isinstance(o,dict):
            for k,v in o.items(): yield from walk(v,p+"."+k)
        elif not isinstance(o,list): yield p,o
    for p,v in walk(st):
        if re.search(r"(?i)price|volume|trading|time|change",p) and len(str(v))<60: out.append(f"{p}={v}")
open("probe/out.txt","w").write("\n".join(out)[:20000])
