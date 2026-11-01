import re, requests
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"ja"}
h=requests.get("https://www.matsui.co.jp/market/stock/netstock-info/",headers=UA,timeout=30).content.decode("utf-8","ignore")
i=h.find("グロース投資指標</a>")
seg=h[i:i+15000]
out=[]
for m in re.finditer(r"<script.*?</script>",seg,flags=re.S):
    out.append(m.group(0)[:3000])
out.append("ATTRS: "+" ".join(re.findall(r'(?:data-[a-z-]+|id|class)="[^"]*"',seg)[:60]))
open("probe/out.txt","w").write("\n\n----\n".join(out)[:15000])
