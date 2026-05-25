/* ============================================================
   Run inspector — datasets with released per-iteration source.
   Every program is clickable to its real Python; the Diff button
   shows the line-level change against its parent program.
   ============================================================ */
import * as d3 from "d3";
import { MSTAR } from "./data";

export const Inspector = (function () {
  // only datasets that carry real per-node code
  const TASKS = MSTAR.TASKS.filter(t => t.runKey);
  let active = TASKS[0].key, run = null, selectedId = null, mode = 'code';
  let nodeById = {};

  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  function init() {
    buildTabs();
    select(active);
  }

  function buildTabs() {
    const el = document.getElementById('insp-tabs');
    el.innerHTML = '';
    TASKS.forEach(t => {
      const b = document.createElement('button');
      b.className = 'insp-tab' + (t.key === active ? ' active' : '');
      b.dataset.key = t.key;
      b.innerHTML = `<span class="tab-dot" style="background:${t.hex}"></span>${t.name} <span class="tab-dom">· ${t.domain}</span>`;
      b.addEventListener('click', () => select(t.key));
      el.appendChild(b);
    });
  }

  function select(key) {
    active = key; mode = 'code';
    run = MSTAR.buildRun(key);
    nodeById = Object.fromEntries(run.nodes.map(n => [n.id, n]));
    document.querySelectorAll('.insp-tab').forEach(b => b.classList.toggle('active', b.dataset.key === key));
    renderMetrics();
    renderTrend();
    renderTree();
    selectedId = run.bestNodeId;
    renderDetail();
    highlightSelected();
  }

  function f3(v) { return v.toFixed(3); }

  function renderMetrics() {
    const t = run.task;
    const delta = run.finalBest - run.seedBest;
    document.getElementById('insp-metricbar').innerHTML = `
      <dl class="mb-item"><dt>Metric</dt><dd style="font-size:16px;font-family:var(--mono);color:${t.textHex}">${t.metric}</dd></dl>
      <dl class="mb-item"><dt>Seed best</dt><dd>${f3(run.seedBest)}</dd></dl>
      <dl class="mb-item"><dt>Evolved best</dt><dd style="color:${t.textHex}">${f3(run.finalBest)}</dd></dl>
      <dl class="mb-item"><dt>Lift</dt><dd class="up">+${f3(delta)}</dd></dl>
      <dl class="mb-item"><dt>Programs</dt><dd>${run.count}</dd></dl>`;
  }

  function renderTrend() {
    const svg = d3.select('#trend-svg');
    svg.selectAll('*').remove();
    const wrap = document.querySelector('.insp-trend');
    const W = Math.max(280, wrap.clientWidth), H = 150;
    const m = { t: 14, r: 16, b: 22, l: 36 };
    svg.attr('viewBox', `0 0 ${W} ${H}`).attr('preserveAspectRatio', 'none').style('height', H + 'px');
    const t = run.task;

    const x = d3.scaleLinear().domain([0, run.maxGen]).range([m.l, W - m.r]);
    const ymax = d3.max(run.trend, d => d.best) * 1.12 || 1;
    const ymin = Math.max(0, run.trend[0].best - (ymax - run.trend[0].best) * .2);
    const y = d3.scaleLinear().domain([ymin, ymax]).range([H - m.b, m.t]);

    y.ticks(4).forEach(tv => {
      svg.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y(tv)).attr('y2', y(tv)).attr('stroke', 'rgba(0,0,0,.09)');
      svg.append('text').attr('x', m.l - 6).attr('y', y(tv) + 3).attr('text-anchor', 'end')
        .attr('font-family', 'var(--mono)').attr('font-size', 9).attr('fill', '#5f6368').text(tv.toFixed(2));
    });

    const grad = svg.append('defs').append('linearGradient').attr('id', 'tg').attr('x1', 0).attr('y1', 0).attr('x2', 0).attr('y2', 1);
    grad.append('stop').attr('offset', '0%').attr('stop-color', t.hex).attr('stop-opacity', .3);
    grad.append('stop').attr('offset', '100%').attr('stop-color', t.hex).attr('stop-opacity', 0);

    const area = d3.area().x(d => x(d.gen)).y0(H - m.b).y1(d => y(d.best)).curve(d3.curveMonotoneX);
    const line = d3.line().x(d => x(d.gen)).y(d => y(d.best)).curve(d3.curveMonotoneX);
    svg.append('path').datum(run.trend).attr('d', area).attr('fill', 'url(#tg)');
    const lp = svg.append('path').datum(run.trend).attr('d', line).attr('fill', 'none')
      .attr('stroke', t.hex).attr('stroke-width', 2).attr('stroke-linecap', 'round');
    const Ln = lp.node().getTotalLength();
    lp.attr('stroke-dasharray', Ln).attr('stroke-dashoffset', Ln)
      .transition().duration(1000).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0);

    run.nodes.filter(n => n.score > 0).forEach(n => {
      svg.append('circle').attr('cx', x(n.gen)).attr('cy', y(Math.min(n.score, ymax))).attr('r', 2).attr('fill', t.hex).attr('opacity', .3);
    });
    const last = run.trend[run.trend.length - 1];
    svg.append('circle').attr('cx', x(last.gen)).attr('cy', y(last.best)).attr('r', 3.5).attr('fill', '#fff').attr('stroke', t.hex).attr('stroke-width', 2);
    svg.append('text').attr('x', W - m.r).attr('y', y(last.best) - 8).attr('text-anchor', 'end')
      .attr('font-family', 'var(--mono)').attr('font-size', 10).attr('font-weight', 600).attr('fill', t.textHex).text(f3(last.best));
    svg.append('text').attr('x', W / 2).attr('y', H - 5).attr('text-anchor', 'middle')
      .attr('font-family', 'var(--mono)').attr('font-size', 9).attr('fill', '#5f6368').text('iteration →');
  }

  // ---------- evolve tree ----------
  function renderTree() {
    const svg = d3.select('#tree-svg');
    svg.selectAll('*').remove();
    const t = run.task;
    const ids = new Set(run.nodes.map(n => n.id));
    const roots = run.nodes.filter(n => !n.parent || !ids.has(n.parent));
    let data = run.nodes.map(n => ({ ...n }));
    let rootId;
    if (roots.length === 1) rootId = roots[0].id;
    else {
      rootId = '__origin__';
      data.unshift({ id: rootId, parent: null, gen: -1, score: 0, label: 'origin', synthetic: true });
      data.forEach(n => { if (n.id !== rootId && (!n.parent || !ids.has(n.parent))) n.parent = rootId; });
    }
    const strat = d3.stratify().id(d => d.id).parentId(d => d.parent)(data);
    d3.tree().nodeSize([46, 60]).separation((a, b) => a.parent === b.parent ? 1 : 1.3)(strat);

    const xs = strat.descendants().map(d => d.x);
    const minX = Math.min(...xs), maxX = Math.max(...xs), pad = 36;
    const W = (maxX - minX) + pad * 2;
    const depth = Math.max(...strat.descendants().map(d => d.depth));
    const H = depth * 60 + pad * 2;
    svg.attr('width', Math.max(W, 100)).attr('height', H);
    const g = svg.append('g').attr('transform', `translate(${pad - minX},${pad})`);

    const norm = s => run.maxScore > run.minScore ? (s - run.minScore) / (run.maxScore - run.minScore) : .5;
    const fillFor = n => n.data.synthetic ? '#cfd8dc' : (n.data.score > 0 ? d3.interpolateRgb('#dfe6ea', t.hex)(norm(n.data.score)) : '#eceff1');

    g.selectAll('path.tlk').data(strat.links()).join('path').attr('class', 'tlk').attr('fill', 'none')
      .attr('stroke', 'rgba(0,0,0,.20)').attr('stroke-width', 1.2)
      .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y));

    const treeNodes = strat.descendants().filter(d => !d.data.synthetic);
    const gn = g.selectAll('g.tnd').data(treeNodes).join('g').attr('class', 'tnd')
      .attr('transform', d => `translate(${d.x},${d.y})`).style('cursor', 'pointer')
      .attr('tabindex', 0).attr('role', 'button')
      .attr('aria-label', d => `${d.data.label}, score ${d.data.score > 0 ? d.data.score.toFixed(2) : 'failed'}`)
      .on('click', (e, d) => { selectedId = d.id; mode = 'code'; renderDetail(); highlightSelected(); })
      .on('keydown', (e, d) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectedId = d.id; mode = 'code'; renderDetail(); highlightSelected(); } });

    gn.append('circle').attr('class', 'tnd-c')
      .attr('r', d => d.id === run.bestNodeId ? 7 : 5.5)
      .attr('fill', fillFor)
      .attr('stroke', d => d.id === run.bestNodeId ? '#37474f' : 'rgba(0,0,0,.4)')
      .attr('stroke-width', d => d.id === run.bestNodeId ? 2 : 1);

    gn.append('text').attr('x', 11).attr('y', 3.5).attr('font-family', 'var(--mono)')
      .attr('font-size', 9.5).attr('fill', '#5f6368')
      .text(d => d.data.score > 0 ? d.data.score.toFixed(2) : '✗');

    gn.attr('opacity', 0).transition().delay(d => d.depth * 70).duration(320).attr('opacity', 1);
  }

  function highlightSelected() {
    d3.selectAll('#tree-svg g.tnd').select('.tnd-c')
      .attr('stroke', d => d.id === selectedId ? '#212121' : (d.id === run.bestNodeId ? '#37474f' : 'rgba(0,0,0,.4)'))
      .attr('stroke-width', d => d.id === selectedId ? 2.5 : (d.id === run.bestNodeId ? 2 : 1));
  }

  // ---------- detail ----------
  function renderDetail() {
    const el = document.getElementById('insp-detail');
    const n = nodeById[selectedId];
    if (!n) { el.innerHTML = '<div class="detail-empty">Select a program in the tree to view its source.</div>'; return; }
    const t = run.task;
    const parent = n.parent ? nodeById[n.parent] : null;
    const delta = parent && parent.score > 0 && n.score > 0 ? (n.score - parent.score) : null;
    const canDiff = !!(parent && parent.code && n.code);
    const genLabel = n.gen <= 0 ? 'Seed · generation 0' : 'Generation ' + n.gen;

    let stats = '';
    if (canDiff) { const d = MSTAR.diffStats(MSTAR.lineDiff(parent.code, n.code)); stats = ` (+${d.add} −${d.del})`; }

    const head = `<div class="detail-head">
      <div class="detail-gen">${genLabel}</div>
      <div class="detail-title">${esc(n.label)}</div>
      <div class="detail-meta">
        <span class="dm-chip">${t.metric} <b style="color:${t.textHex}">${n.score > 0 ? n.score.toFixed(3) : '—'}</b></span>
        ${delta != null ? `<span class="dm-chip">Δ parent <b style="color:${delta >= 0 ? '#2e7d32' : '#c62828'}">${delta >= 0 ? '+' : ''}${delta.toFixed(3)}</b></span>` : ''}
        ${n.tag ? `<span class="dm-chip">${esc(n.tag)}</span>` : ''}
        ${parent ? `<span class="dm-chip">parent: ${esc(parent.label)}</span>` : ''}
      </div>
      <div class="detail-modes">
        <button class="dm-btn ${mode === 'code' ? 'active' : ''}" data-m="code">Source</button>
        <button class="dm-btn ${mode === 'diff' ? 'active' : ''}" data-m="diff" ${canDiff ? '' : 'disabled title="No parent to diff against"'}>Diff vs parent${stats}</button>
      </div>
    </div>`;

    let body;
    if (mode === 'diff' && canDiff) {
      const diff = MSTAR.lineDiff(parent.code, n.code);
      const rows = diff.map(d => {
        const cls = d.t === '+' ? 'di-add' : d.t === '-' ? 'di-del' : 'di-ctx';
        return `<div class="di ${cls}"><span class="di-g">${d.t === ' ' ? ' ' : d.t}</span>${esc(d.s) || ' '}</div>`;
      }).join('');
      body = `<div class="detail-code diffview" tabindex="0" aria-label="Diff against parent program">${rows}</div>`;
    } else {
      body = `<pre class="detail-code" tabindex="0" aria-label="Program source">${MSTAR.highlightPython(n.code)}</pre>`;
    }
    el.innerHTML = head + body;
    el.querySelectorAll('.dm-btn').forEach(b => b.addEventListener('click', () => {
      if (b.disabled) return; mode = b.dataset.m; renderDetail();
    }));
  }

  function onResize() { if (run) renderTrend(); }

  return { init, onResize };
})();
