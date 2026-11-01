import re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
s=requests.Session(); s.headers.update(UA)
h=s.get("https://www.matsui.co.jp/market/stock/netstock-info/",timeout=30).content.decode("utf-8","ignore")
out=[]
for m in re.finditer(r"new_ranking_index|ranking_index|netstock|\.json|ajax|fetch\(|XMLHttp",h):
    out.append(f"[{m.group(0)}] "+re.sub(r"\s+"," ",h[max(0,m.start()-300):m.start()+400]))
srcs=re.findall(r'<script[^>]+src="([^"]+)"',h); out.append("SRCS: "+" ".join(srcs))
for src in srcs:
    if src.startswith("/") and "netstock" in src or "market" in src:
        u="https://www.matsui.co.jp"+src if src.startswith("/") else src
        try:
            js=s.get(u,timeout=30).text; out.append(f"== {u}\n{js[:3000]}")
        except Exception as e: out.append(f"{u} ERR {e}")
open("probe/out.txt","w").write("\n\n".join(out)[:25000])
