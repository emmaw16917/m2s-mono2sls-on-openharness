// Aggregate results.jsonl: per function x config, median across reps of the
// per-run warm medians, plus the init range (config A only; init under a CPU
// quota is not representative of Lambda).
const fs = require('fs');
const rows = fs.readFileSync(process.argv[2] || 'results.jsonl', 'utf8').trim().split('\n').filter((l) => l.startsWith('{')).map(JSON.parse);
const g = {};
for (const r of rows) {
  const [fn, cfg] = r.fn.split('|');
  (g[fn] ||= {})[cfg] ||= [];
  g[fn][cfg].push(r);
}
const med = (a) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor((s.length - 1) / 2)]; };
const short = (f) => f.replace('Function', '');
console.log('| 函数 | init_ms（A，3 次） | 热调用中位数 A（不限 CPU，无延迟） | 热调用中位数 B（0.289 CPU + 5ms/调用） | B 的 p90（中位数） |');
console.log('|---|---|---|---|---|');
for (const fn of Object.keys(g)) {
  const A = g[fn].A || [], B = g[fn].B || [];
  const inits = A.map((r) => r.init_ms);
  console.log(`| ${short(fn)} | ${med(inits)}（${Math.min(...inits)}~${Math.max(...inits)}） | ${med(A.map((r) => r.warm_median_ms))}ms（${A.map((r) => r.warm_median_ms).join(' / ')}） | ${med(B.map((r) => r.warm_median_ms))}ms（${B.map((r) => r.warm_median_ms).join(' / ')}） | ${med(B.map((r) => r.warm_p90))}ms |`);
}
