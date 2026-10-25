import json, re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja,en"}
out=[]
def tryget(name,url,f):
    try:
        r=requests.get(url,headers=UA,timeout=30)
        out.append(f"== {name} {r.status_code} len={len(r.text)}")
        out.append(f(r))
    except Exception as e:
        out.append(f"== {name} ERR {e}")
def mexc(r):
    d=r.json().get("data",[])
    keys=("US30","NAS","SPX","SP500","DOW","XAU","XAG","GOLD","SILVER","OIL","WTI","BRENT","NIKKEI","JP225","USDJPY")
    return "\n".join(f"{x.get('symbol')} last={x.get('lastPrice')} rise={x.get('riseFallRate')}" for x in d if any(k in x.get('symbol','') for k in keys))[:3000]
tryget("mexc","https://contract.mexc.com/api/v1/contract/ticker",mexc)
def ig(r):
    t=r.text
    m=re.findall(r'(?i)(weekend[^<"]{0,60})',t)[:10]
    nums=re.findall(r'"(?:bid|offer|price|netChange|percentageChange)"\s*:\s*"?([-\d.]+)',t)[:20]
    return f"titles={m}\nnums={nums}\nsnip={t[:500]}"
tryget("ig_ws","https://www.ig.com/uk/indices/markets-indices/weekend-wall-street",ig)
tryget("ig_wt","https://www.ig.com/uk/weekend-trading",ig)
tryget("ig_jp","https://www.ig.com/jp/indices/markets-indices/weekend-wall-street",ig)
def okx(r):
    d=r.json().get("data",[]); return "\n".join(f"{x['instId']} {x['last']} open24={x['open24h']}" for x in d if any(k in x['instId'] for k in ("XAU","XAG","PAXG","SPX","NDX","US30","CL")))[:2000]
tryget("okx_swap","https://www.okx.com/api/v5/market/tickers?instType=SWAP",okx)
def byb(r):
    d=r.json().get("result",{}).get("list",[]); return "\n".join(f"{x['symbol']} {x['lastPrice']} prev24={x.get('prevPrice24h')}" for x in d if any(k in x['symbol'] for k in ("XAU","XAG","PAXG","SPX","NAS","US30","OIL","DOW")))[:2000]
tryget("bybit","https://api.bybit.com/v5/market/tickers?category=linear",byb)
def hl(r): return r.text[:200]
try:
    r=requests.post("https://api.hyperliquid.xyz/info",json={"type":"metaAndAssetCtxs"},timeout=30)
    names=[u["name"] for u in r.json()[0]["universe"]]
    out.append("== hyperliquid "+str(r.status_code)+" "+",".join(n for n in names if any(k in n for k in ("PAXG","SPX","XAU","GOLD","SILVER","OIL","US30","NAS"))))
except Exception as e: out.append(f"== hyperliquid ERR {e}")
try:
    r=requests.post("https://api.hyperliquid.xyz/info",json={"type":"perpDexs"},timeout=30); out.append("== hl perpDexs "+r.text[:1500])
except Exception as e: out.append(f"== hl dex ERR {e}")
open("probe/out.txt","w").write("\n".join(map(str,out)))
