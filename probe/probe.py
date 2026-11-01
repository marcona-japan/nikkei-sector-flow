import re, traceback
from playwright.sync_api import sync_playwright
out=[]
reqs=[]
try:
    with sync_playwright() as p:
        b=p.chromium.launch()
        pg=b.new_page(locale="ja-JP", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        pg.on("request", lambda r: reqs.append(r.url))
        pg.goto("https://www.matsui.co.jp/market/stock/netstock-info/",wait_until="domcontentloaded",timeout=60000)
        pg.wait_for_timeout(12000)
        try: out.append("SECTION:\n"+pg.inner_text("#new_ranking_index")[:4000])
        except Exception as e: out.append(f"section err {e}")
        body=pg.inner_text("body")
        for m in list(re.finditer("評価損益率",body))[:10]:
            out.append("…"+body[max(0,m.start()-200):m.start()+300].replace("\n"," / "))
        b.close()
except Exception:
    out.append(traceback.format_exc())
out.append("REQS:\n"+"\n".join(u for u in reqs if "matsui" in u or "rtoaster" in u or "json" in u)[:5000])
open("probe/out.txt","w").write("\n\n".join(out)[:20000])
