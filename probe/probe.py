import json, re, time, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
out=[]
def state(html):
    m=re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>",html,re.S)
    return json.loads(m.group(1)) if m else {}
def walk(o,path=""):
    if isinstance(o,dict):
        for k,v in o.items(): yield from walk(v,f"{path}.{k}")
    elif isinstance(o,list):
        for i,v in enumerate(o[:3]): yield from walk(v,f"{path}[{i}]")
    else: yield path,o
s=requests.Session(); s.headers.update(UA)
t0=time.time(); r=s.get("https://finance.yahoo.co.jp/quote/7203.T",timeout=30); out.append(f"status {r.status_code} {len(r.text)} {time.time()-t0:.2f}s")
st=state(r.text)
for p,v in walk(st):
    pl=p.lower()
    if any(k in pl for k in ("price","volume","tradingvalue","time","date","change","realtime","delay")) and len(str(v))<80:
        out.append(f"{p} = {v}")
out=out[:250]
# timing for 30 pages
codes=["6758","9984","8035","6857","7011","8306","9432","4063","6501","6098","7974","8058","8001","4502","6367","6954","6981","7267","8316","9433","4519","6273","6594","6702","6762","7741","8031","8411","8766","9983"]
t0=time.time(); ok=0; codes_st=[]
for c in codes:
    rr=s.get(f"https://finance.yahoo.co.jp/quote/{c}.T",timeout=30); ok+= rr.status_code==200; codes_st.append(rr.status_code)
out.append(f"30 pages: ok={ok} {time.time()-t0:.1f}s statuses={codes_st}")
# try multi-quote style endpoints
for u in ["https://finance.yahoo.co.jp/quote/7203.T,6758.T","https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&page=2"]:
    try:
        rr=s.get(u,timeout=30); out.append(f"{u} -> {rr.status_code} {len(rr.text)}")
    except Exception as e: out.append(f"{u} ERR {e}")
open("probe/out.txt","w").write("\n".join(map(str,out)))
