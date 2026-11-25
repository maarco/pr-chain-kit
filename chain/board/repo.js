// Title above the nav on every board page: which repo this board works on, and its open issue and PR counts.
(() => {
  const nav = document.querySelector('.top');
  if (!nav) return;
  const el = document.createElement('h1');
  el.id = 'repotitle';
  el.style.cssText = 'margin:0 0 8px;font-size:17px;font-weight:500;letter-spacing:-.01em;display:flex;gap:10px;flex-wrap:wrap;align-items:baseline';
  nav.before(el);
  const count = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const draw = d => {
    const parts = [d.repo];
    if (Number.isInteger(d.issues)) parts.push(count(d.issues, 'Issue'));
    if (Number.isInteger(d.prs)) parts.push(count(d.prs, 'Pull Request'));
    el.replaceChildren(...parts.flatMap((text, i) => {
      const span = document.createElement('span');
      span.textContent = text;
      if (i) span.style.color = 'var(--muted)';
      if (!i) return [span];
      const bar = document.createElement('span');
      bar.textContent = '|';
      bar.style.color = 'var(--faint)';
      return [bar, span];
    }));
  };
  const load = () => fetch('/api/repo').then(r => r.json()).then(d => { if (d.repo) draw(d); }).catch(() => {});
  load();
  setInterval(load, 60000);
})();
