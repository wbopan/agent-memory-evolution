/* ============================================================
   Figure 1 — radial phylogeny that grows with the loop
   d3.cluster radial layout. Programs are revealed incrementally:
   each completed turn of the evolution loop (Figure 2) reveals
   one new program on each of the four task quadrants, in the
   order they were produced during the recorded runs.
   ============================================================ */
import * as d3 from "d3";
import { MSTAR } from "./data";

export const Figure1 = (function () {
  const SIZE = 1000, CX = SIZE / 2, CY = SIZE / 2, R = 388;
  let svg, gTree, root, tip, readout, legendEl;
  let linkSel, nodeSel;
  const TASKS = MSTAR.TASKS;
  const taskByD = Object.fromEntries(TASKS.map(t => [t.d, t]));

  const order = {}, ptr = {};       // per-quadrant reveal order + pointer

  function px(n) { return n.y * Math.sin(n.x); }
  function py(n) { return -n.y * Math.cos(n.x); }
  function colorOf(d) { return d >= 0 ? taskByD[d].hex : '#90a4ae'; }
  function textOf(d) { return d >= 0 ? taskByD[d].textHex : '#5f6368'; }

  async function init() {
    const data = await MSTAR.loadPhylo();
    svg = d3.select('#phylo-svg').attr('viewBox', `0 0 ${SIZE} ${SIZE}`)
      .attr('preserveAspectRatio', 'xMidYMid meet');
    tip = document.getElementById('phylo-tip');
    readout = document.getElementById('phylo-readout');
    legendEl = document.getElementById('phylo-legend');

    const defs = svg.append('defs');
    TASKS.forEach(t => {
      const g = defs.append('radialGradient').attr('id', `sec-${t.d}`);
      g.append('stop').attr('offset', '0%').attr('stop-color', t.hex).attr('stop-opacity', .12);
      g.append('stop').attr('offset', '55%').attr('stop-color', t.hex).attr('stop-opacity', .045);
      g.append('stop').attr('offset', '100%').attr('stop-color', t.hex).attr('stop-opacity', 0);
    });

    gTree = svg.append('g').attr('transform', `translate(${CX},${CY})`);

    root = d3.hierarchy(data);
    root.each(n => {
      if (n.depth <= 1) n.data.d = -1;
      else if (n.depth >= 3) n.data.d = n.parent.data.d;
    });
    d3.cluster().size([2 * Math.PI, R]).separation((a, b) => a.parent === b.parent ? 1 : 1.7)(root);
    root.leaves().forEach(l => { l.y = R; });

    drawSectors();
    drawRings();
    drawLinks();
    drawNodes();
    drawDomainLabels();
    buildLegend();

    // reveal order per quadrant: by depth then angle (parent before child)
    TASKS.forEach(t => {
      order[t.d] = root.descendants()
        .filter(n => n.data.d === t.d && n.depth >= 2)
        .sort((a, b) => (a.depth - b.depth) || (a.x - b.x));
      ptr[t.d] = 0;
    });

    beginGrowth();
  }

  function drawSectors() {
    const arc = d3.arc();
    TASKS.forEach(t => {
      const leaves = root.leaves().filter(l => l.data.d === t.d);
      if (!leaves.length) return;
      const a = leaves.map(l => l.x);
      gTree.append('path').attr('class', `sector sec-d${t.d}`)
        .attr('d', arc({ innerRadius: 0, outerRadius: R + 28,
          startAngle: Math.min(...a) - .14, endAngle: Math.max(...a) + .14 }))
        .attr('fill', `url(#sec-${t.d})`);
    });
  }

  function drawRings() {
    [0.32, 0.58, 0.84, 1].forEach((f, i) => {
      gTree.append('circle').attr('r', R * f).attr('fill', 'none')
        .attr('stroke', i === 3 ? 'rgba(0,0,0,.18)' : 'rgba(0,0,0,.10)')
        .attr('stroke-width', i === 3 ? .8 : .5).attr('stroke-dasharray', i === 3 ? null : '2,6');
    });
  }

  function drawLinks() {
    const linkGen = d3.linkRadial().angle(d => d.x).radius(d => d.y);
    linkSel = gTree.append('g').selectAll('path.lk')
      .data(root.links()).join('path')
      .attr('class', 'lk').attr('fill', 'none')
      .attr('stroke', d => colorOf(d.target.data.d))
      .attr('stroke-opacity', d => d.source.depth <= 1 ? .35 : .55)
      .attr('stroke-width', d => Math.max(.9, 2.6 - d.target.depth * .26))
      .attr('stroke-linecap', 'round')
      .attr('d', linkGen);
  }

  function drawNodes() {
    const data = root.descendants().filter(n => n.depth >= 1);
    nodeSel = gTree.append('g').selectAll('circle.nd')
      .data(data).join('circle')
      .attr('class', 'nd')
      .attr('cx', px).attr('cy', py)
      .attr('r', d => d.children ? (d.depth <= 2 ? 3.6 : 2.6) : 4.4)
      .attr('fill', d => d.children ? '#ffffff' : colorOf(d.data.d))
      .attr('stroke', d => colorOf(d.data.d))
      .attr('stroke-width', d => d.children ? 1.4 : 1)
      .style('cursor', 'pointer')
      .on('mouseenter', (e, d) => focusNode(d, e))
      .on('mousemove', (e) => moveTip(e))
      .on('mouseleave', clearFocus)
      .on('click', (e, d) => focusNode(d, e, true));

    gTree.append('circle').attr('r', 5.5).attr('fill', '#37474f')
      .attr('stroke', '#90a4ae').attr('stroke-width', 1.5);
  }

  function drawDomainLabels() {
    TASKS.forEach(t => {
      const leaves = root.leaves().filter(l => l.data.d === t.d);
      if (!leaves.length) return;
      const mid = d3.mean(leaves, l => l.x);
      const lr = R + 46;
      const x = lr * Math.sin(mid), y = -lr * Math.cos(mid);
      let rot = mid * 180 / Math.PI;
      const flip = rot > 90 && rot < 270;
      if (flip) rot += 180;
      gTree.append('text').attr('class', `dlabel dlabel-d${t.d}`)
        .attr('x', x).attr('y', y).attr('text-anchor', 'middle')
        .attr('dominant-baseline', flip ? 'text-before-edge' : 'text-after-edge')
        .attr('fill', t.textHex).attr('font-family', 'var(--mono)')
        .attr('font-size', '16px').attr('font-weight', '600').attr('letter-spacing', '.04em')
        .attr('transform', `rotate(${rot},${x},${y})`).text(t.name);
    });
  }

  // ---- incremental growth ----
  function isHidden(n) { return n.depth >= 2; }   // quadrant programs are grown
  function collapse() {
    nodeSel.attr('display', d => isHidden(d) ? 'none' : null).attr('opacity', 1);
    linkSel.attr('display', d => isHidden(d.target) ? 'none' : null)
      .attr('stroke-dasharray', null).attr('stroke-dashoffset', null);
  }
  function beginGrowth() { TASKS.forEach(t => ptr[t.d] = 0); collapse(); }

  function revealNode(node) {
    nodeSel.filter(d => d === node).attr('display', null)
      .attr('opacity', 0).transition().duration(420).attr('opacity', 1);
    const lk = linkSel.filter(d => d.target === node).attr('display', null);
    const el = lk.node();
    if (el && el.getTotalLength) {
      const L = el.getTotalLength() || 0;
      lk.attr('stroke-dasharray', L).attr('stroke-dashoffset', L)
        .transition().duration(460).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
        .on('end', function () { d3.select(this).attr('stroke-dasharray', null); });
    }
  }

  function growNext() {
    let grew = false;
    TASKS.forEach(t => {
      const arr = order[t.d]; const p = ptr[t.d];
      if (arr && p < arr.length) { revealNode(arr[p]); ptr[t.d] = p + 1; grew = true; }
    });
    return grew;
  }
  function growthDone() { return TASKS.every(t => ptr[t.d] >= (order[t.d] ? order[t.d].length : 0)); }
  function totalRounds() { return Math.max(...TASKS.map(t => order[t.d] ? order[t.d].length : 0)); }

  // ---- interaction ----
  function focusNode(d, e) {
    const path = new Set(d.ancestors());
    const dlinks = new Set();
    d.ancestors().forEach(a => { if (a.parent) dlinks.add(a); });
    linkSel.attr('stroke-opacity', l => dlinks.has(l.target) ? .95 : .1)
      .attr('stroke-width', l => dlinks.has(l.target) ? Math.max(1.6, 3 - l.target.depth * .2) : Math.max(.7, 2 - l.target.depth * .26));
    nodeSel.attr('opacity', n => path.has(n) ? 1 : .2)
      .attr('r', n => n === d ? (n.children ? 6 : 6.6) : (n.children ? (n.depth <= 2 ? 3.6 : 2.6) : 4.4));
    showTip(d, e);
    showReadout(d);
  }
  function clearFocus() {
    if (tip) tip.hidden = true;
    linkSel.attr('stroke-opacity', l => l.source.depth <= 1 ? .35 : .55)
      .attr('stroke-width', l => Math.max(.9, 2.6 - l.target.depth * .26));
    nodeSel.attr('opacity', 1).attr('r', d => d.children ? (d.depth <= 2 ? 3.6 : 2.6) : 4.4);
  }

  function showTip(d, e) {
    if (!tip) return;
    const t = taskByD[d.data.d];
    const sc = (typeof d.data.score === 'number') ? d.data.score : null;
    tip.innerHTML =
      `<div class="tt-label">${d.data.label || 'Seed program'}</div>` +
      `<div class="tt-meta">${t ? t.name : 'shared seed'}${sc != null ? ` · <span class="tt-score">${sc.toFixed(3)}</span> ${t ? t.metric : ''}` : ''}</div>`;
    tip.hidden = false;
    moveTip(e);
  }
  function moveTip(e) {
    if (!tip) return;
    const host = document.querySelector('.merge-phylo') || tip.offsetParent || document.body;
    const wrap = host.getBoundingClientRect();
    tip.style.left = (e.clientX - wrap.left) + 'px';
    tip.style.top = (e.clientY - wrap.top) + 'px';
  }
  function showReadout(d) {
    if (!readout || readout.hidden) return;
    const t = taskByD[d.data.d];
    const sc = (typeof d.data.score === 'number') ? d.data.score : null;
    readout.textContent = `${t ? t.name : 'Seed'} · ${d.data.label || 'seed program'}` + (sc != null ? ` · ${sc.toFixed(3)} ${t.metric}` : '');
  }

  function buildLegend() {
    if (!legendEl) return;
    legendEl.innerHTML = '';
    TASKS.forEach(t => {
      const run = MSTAR.buildRun(t.key);
      const el = document.createElement('div');
      el.className = 'leg-item';
      el.innerHTML =
        `<span class="leg-swatch" style="background:${t.hex}"></span>` +
        `<span class="leg-name">${t.name}</span>` +
        `<span class="leg-best" style="color:${t.textHex}">${run.finalBest.toFixed(t.key === 'alfworld' ? 2 : 3)}</span>`;
      legendEl.appendChild(el);
    });
  }

  return { init, beginGrowth, growNext, growthDone, totalRounds, replay: beginGrowth };
})();
