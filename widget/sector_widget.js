// セクター資金移動ウィジェット（iPhone「Scriptable」アプリ用）
// 最新の時間帯で、20日平均より売買代金シェアが増えた業種（流入）・減った業種（流出）を表示
const BASE = "https://marcona-japan.github.io/nikkei-sector-flow/";
const req = new Request(BASE + "data/latest.json?t=" + Date.now());
const d = await req.loadJSON();

// 直近で集計済みの時間帯（なければ当日合計）
let w = -1;
d.windows.forEach((x, i) => { if (x.state === "done") w = i; });
const label = w >= 0 ? d.windows[w].label : "当日";
const rows = d.sectors
  .map(s => ({ name: s.name, dl: (w >= 0 ? s.shares[w] : s.day_share) - s.base, ret: s.ret }))
  .filter(r => !isNaN(r.dl))
  .sort((a, b) => b.dl - a.dl);

const fam = config.widgetFamily || "medium";
const n = fam === "small" ? 3 : fam === "large" ? 8 : 4;
const RED = new Color("#ff5a5a"), BLUE = new Color("#4da3ff"), TXT = Color.white(), SUB = new Color("#9aa0ab");

const wg = new ListWidget();
wg.backgroundColor = new Color("#1e222d");
wg.url = BASE;
wg.setPadding(12, 14, 12, 14);

const md = d.date.slice(5).replace("-", "/");
const head = wg.addText(`資金移動 ${md} ${label}${d.final ? "" : "（途中）"}`);
head.font = Font.boldSystemFont(fam === "small" ? 12 : 14);
head.textColor = TXT;
wg.addSpacer(6);

function col(stack, title, list, color) {
  const c = stack.addStack();
  c.layoutVertically();
  const t = c.addText(title);
  t.font = Font.boldSystemFont(12);
  t.textColor = color;
  c.addSpacer(3);
  for (const r of list) {
    const line = c.addStack();
    const a = line.addText(r.name);
    a.font = Font.systemFont(fam === "small" ? 12 : 14);
    a.textColor = TXT;
    a.lineLimit = 1;
    line.addSpacer();
    const b = line.addText((r.dl >= 0 ? "+" : "") + r.dl.toFixed(1));
    b.font = Font.boldMonospacedSystemFont(fam === "small" ? 12 : 14);
    b.textColor = color;
    c.addSpacer(2);
  }
}

if (fam === "small") {
  const s = wg.addStack(); s.layoutVertically();
  col(s, "▲ 流入", rows.slice(0, n), RED);
  s.addSpacer(4);
  col(s, "▼ 流出", rows.slice(-n).reverse(), BLUE);
} else {
  const s = wg.addStack();
  col(s, "▲ 流入（pt）", rows.slice(0, n), RED);
  s.addSpacer(16);
  col(s, "▼ 流出（pt）", rows.slice(-n).reverse(), BLUE);
}

wg.addSpacer();
const f = wg.addText("20日平均シェア比 ・ 更新 " + d.updated.slice(11));
f.font = Font.systemFont(10);
f.textColor = SUB;

wg.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
if (config.runsInWidget) Script.setWidget(wg); else await wg.presentMedium();
Script.complete();
