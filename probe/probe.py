import re
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch()
    pg=b.new_page(locale="ja-JP")
    pg.goto("https://www.matsui.co.jp/market/stock/netstock-info/",wait_until="networkidle",timeout=60000)
    pg.wait_for_timeout(5000)
    t=pg.inner_text("#new_ranking_index")
    b.close()
open("probe/out.txt","w").write(t[:8000])
