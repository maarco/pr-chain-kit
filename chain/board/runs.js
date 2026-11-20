// One Timeline section for the /prs and /issues detail panels: agent runs (openable rows) and ledger/commit/comment events, oldest first. One shared copy: both pages load this file.
// Data: /api/<pr|issue>/<n>/runs (list + timeline) and /api/run/<id> (prompt, result, last message, prompt diff; fetched when a row opens).
// Needs the page's globals: P (prefs with .shut) and redraw().
(() => {
const E = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const css = document.createElement('style');
css.textContent = `
.ar{display:grid;grid-template-columns:22px 104px 34px 52px 92px minmax(0,1fr);gap:0 10px;padding:3px 8px;margin:0 -8px;border-radius:6px;cursor:pointer;font-size:11.5px}
.ar:hover,.ar.open{background:var(--card)}.ar>*{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.arb{margin:0 -8px 6px;padding:2px 8px 6px 40px;background:var(--card);border-radius:0 0 6px 6px;font-size:11.5px}
.arb .at2{color:var(--muted);cursor:pointer;margin:7px 0 0;width:max-content}.arb .at2:hover,.arb .at2.on{color:var(--fg)}
.arb pre{font:10.5px/1.5 var(--mono);color:var(--muted);background:var(--bg);border-radius:6px;padding:8px 10px;margin:4px 0 6px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:60vh;overflow:auto;cursor:text;scrollbar-width:thin;scrollbar-color:var(--raise) transparent}
.arb pre .ra{color:var(--hi)}.arb pre .rm{color:var(--bad)}.arb pre .rh{color:var(--faint)}
.tl{display:grid;grid-template-columns:104px 58px minmax(0,1fr);gap:0 10px;font-size:11.5px;padding:1px 0}.tl>:last-child{color:var(--mid);overflow-wrap:anywhere}
.tl .tk{font:10.5px var(--mono);color:var(--faint)}.tl .tk.comment{color:var(--warn)}.tl.ledger>:last-child{color:var(--muted)}.tl.sub{margin-left:32px}
@media(max-width:520px){.ar{grid-template-columns:20px 92px 30px 46px minmax(0,1fr)}.ar>:nth-child(5){display:none}.arb{padding-left:10px}.tl{grid-template-columns:92px minmax(0,1fr)}.tl.sub{margin-left:12px}.tl .tk{display:none}.dd{display:none}}`;
document.head.appendChild(css);

const vc = s => /^(PASS|FIXED|READY)/.test(s) ? 'ok' : /^(FAIL|STACK|BLOCK|ERROR)/.test(s) ? 'bad' : /^(NOT-SHOWN|DISPUTE|NO-|None)/.test(s) ? 'warn' : 'dim';
const hm = t => `<span class="dd">${String(t).slice(5, 10)} </span>${String(t).slice(11, 19)}`; // date hides on phones
const mins = s => s == null ? '' : s < 90 ? Math.round(s) + 's' : Math.round(s / 60) + 'm';
const KINDS = [['ledger', 'ledger'], ['commit', 'commits'], ['comment', 'comments']];
const R = { kind: '', n: 0, groups: null, tl: [], err: '', cache: {}, open: {}, tabs: {}, kinds: { ledger: 1, commit: 1, comment: 1 } };

R.load = async (kind, n, cb) => {
  if (R.kind !== kind || R.n !== n) { R.groups = null; R.tl = []; R.open = {}; R.tabs = {}; }
  R.kind = kind; R.n = n; R.err = ''; R.cache = {};
  try {
    const r = await (await fetch(`/api/${kind}/${n}/runs`)).json();
    if (R.kind !== kind || R.n !== n) return;
    if (r.error) R.err = r.error; else { R.groups = r.groups; R.tl = r.timeline; }
  } catch (e) { R.err = String(e); }
  cb();
  Object.keys(R.open).forEach(id => R.get(id, cb));
};
R.get = async (id, cb) => {
  if (R.cache[id]) return;
  try { R.cache[id] = await (await fetch('/api/run/' + id)).json(); } catch (e) { R.cache[id] = { error: String(e) }; }
  cb();
};

const row = (r, i) => `<div class="ar${R.open[r.id] ? ' open' : ''}" data-r="${E(r.id)}" title="Click To Open This Agent's Prompt And Result"><span class="f mono">${i}</span><span class="mono">${hm(r.start)}</span><span class="dim">${mins(r.secs)}</span><span>${E(r.role)}</span><span class="mono f">${E(r.head)}</span><span class="${vc(r.first)}">${R.kind === 'issue' && r.id[0] === 'p' ? `<span class="f">#${E(r.id.slice(1).split('/')[0])} </span>` : ''}${E(r.first || 'no result file')}</span></div>`;

const diffHtml = t => t.split('\n').map(l => `<span class="${l[0] === '+' && l[1] !== '+' ? 'ra' : l[0] === '-' && l[1] !== '-' ? 'rm' : l[0] === '@' ? 'rh' : ''}">${E(l)}</span>`).join('\n');

const body = r => {
  const c = R.cache[r.id];
  if (!c) return '<div class="arb dim">Reading…</div>';
  if (c.error) return `<div class="arb warn">${E(c.error)}</div>`;
  const o = R.tabs[r.id] || (R.tabs[r.id] = { result: true });
  const tabs = [['result', 'Result', c.result], ['out', "Agent's Last Message", c.out], ['brief', 'Prompt', c.brief], ['diff', 'Prompt Changes', c.diff]];
  return `<div class="arb"><div class="f mono">${E(r.model)} · ${E(r.name)} · prompt ${c.brief.length} characters${c.same ? ' · identical to the previous ' + E(r.role) + ' prompt' : ''}${c.extra.length ? ' · other files: ' + E(c.extra.join(', ')) : ''}</div>`
    + tabs.map(([k, l, t]) => t ? `<div class="at2${o[k] ? ' on' : ''}" data-t="${k}" data-r="${E(r.id)}">${l}</div>${o[k] ? `<pre>${k === 'diff' ? diffHtml(t) : E(t)}</pre>` : ''}` : '').join('') + '</div>';
};

R.html = () => {
  if (!R.n) return '';
  if (R.err) return `<h2>Timeline</h2><div class="warn">${E(R.err)}</div>`;
  if (!R.groups) return '<h2>Timeline <span>Reading…</span></h2>';
  const all = R.groups.flatMap(g => g.runs);
  if (!all.length && !R.tl.length) return '<h2>Timeline <span>nothing recorded</span></h2><div class="dim">No agent has run on this yet.</div>';
  const tot = all.reduce((a, r) => a + (r.secs || 0), 0);
  // every agent run is a row you can open; the events that happened from its start until the next run starts roll up under it
  const runs = all.map((r, i) => [r, i + 1]).sort((a, b) => a[0].start < b[0].start ? -1 : a[0].start > b[0].start ? 1 : 0);
  const ev = R.tl.filter(e => R.kinds[e[1]]).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
  const line = (e, sub) => `<div class="tl ${e[1]}${sub ? ' sub' : ''}"><span class="f mono">${hm(e[0])}</span><span class="tk ${e[1]}">${e[1]}</span><span>${E(e[2])}</span></div>`;
  let h = `<h2>Timeline <span>${all.length} agent runs · ${Math.round(tot / 60)} min of agent time · ${R.tl.length} events ${KINDS.map(([k, l]) => `<a class="link${R.kinds[k] ? ' on' : ''}" data-rk="${k}">${l}</a>`).join('')}</span></h2>`;
  let k = 0;
  while (k < ev.length && (!runs.length || ev[k][0] < runs[0][0].start)) h += line(ev[k++], false); // before the first run
  runs.forEach(([r, n], j) => {
    h += row(r, n) + (R.open[r.id] ? body(r) : '');
    const next = runs[j + 1] ? runs[j + 1][0].start : null;
    while (k < ev.length && (next === null || ev[k][0] < next)) h += line(ev[k++], true);
  });
  return h;
};

// true = the click was ours (the page handler stops there)
R.click = (t, cb) => {
  const a = t.closest('.ar');
  if (a) { const id = a.dataset.r; if (R.open[id]) delete R.open[id]; else { R.open[id] = 1; R.get(id, cb); } cb(); return true; }
  const q = t.closest('.at2');
  if (q) { const o = R.tabs[q.dataset.r] || (R.tabs[q.dataset.r] = {}); o[q.dataset.t] = !o[q.dataset.t]; cb(); return true; }
  if (t.dataset && t.dataset.rk) { R.kinds[t.dataset.rk] = R.kinds[t.dataset.rk] ? 0 : 1; cb(); return true; }
  return !!t.closest('.arb');
};
window.Runs = R;
})();
