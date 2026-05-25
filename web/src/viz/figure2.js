/* ============================================================
   Figure 2 — procedural evolution loop (drives Figure 1 growth)
   Sample Pool → Evaluate → Reflect & Mutate → Quality Gates.
   Each completed turn fires onRound(), which grows one program
   per task quadrant in Figure 1.
   ============================================================ */
import * as d3 from "d3";

export const Figure2 = (function () {
  const VB = 600, C = 300, RR = 188;
  const MOVE_DUR = 380, NEXT_DELAY = 200;
  // graphic colours (>=3:1 on white) and text-safe colours (>=4.5:1)
  const COL  = { pool:'#1976d2', eval:'#00796b', refl:'#5e35b1', gate:'#e64a19' };
  const COLT = { pool:'#1565c0', eval:'#00695c', refl:'#5e35b1', gate:'#bf360c' };
  const INK = '#212121', INK2 = '#616161', INK3 = '#5f6368', SCORE = '#3a7a2e';

  const STAGES = [
    { key:'pool', ang:-90, color:COL.pool, tcol:COLT.pool, title:'Sample a parent', sub:'softmax on fitness' },
    { key:'eval', ang:0,   color:COL.eval, tcol:COLT.eval, title:'Evaluate',        sub:'write → read → score' },
    { key:'refl', ang:90,  color:COL.refl, tcol:COLT.refl, title:'Reflect & mutate', sub:'diagnose → code patch' },
    { key:'gate', ang:180, color:COL.gate, tcol:COLT.gate, title:'Quality gates',   sub:'compile · smoke · limits' },
  ];

  let svg, gRing, gCards, token, flowDots, ringArcs = [];
  let cur = -1, playing = false, raf = null;
  let iter = 1, score = 0.29;
  let scoreText, iterText, pcodeText;
  let stepEls = [];
  let roundCb = null, pendingRound = false;
  const $ = id => document.getElementById(id);

  function pt(angDeg, r = RR) { const a = angDeg * Math.PI / 180; return [C + r * Math.cos(a), C + r * Math.sin(a)]; }

  function init() {
    svg = d3.select('#loop-svg').attr('viewBox', `0 0 ${VB} ${VB}`).attr('preserveAspectRatio', 'xMidYMid meet');

    gRing = svg.append('g');
    for (let i = 0; i < 4; i++) {
      ringArcs.push(gRing.append('path').attr('d', arcPath(STAGES[i].ang, STAGES[(i + 1) % 4].ang))
        .attr('fill', 'none').attr('stroke', 'rgba(0,0,0,.16)').attr('stroke-width', 1.5).attr('stroke-linecap', 'round'));
    }

    const gC = svg.append('g').attr('transform', `translate(${C},${C})`);
    gC.append('circle').attr('r', 72).attr('fill', 'none').attr('stroke', 'rgba(0,0,0,.16)');
    gC.append('text').attr('text-anchor', 'middle').attr('y', -32)
      .attr('font-family', 'var(--mono)').attr('font-size', 11).attr('fill', INK2).text('memory program');
    pcodeText = gC.append('text').attr('text-anchor', 'middle').attr('y', 8)
      .attr('font-family', 'var(--mono)').attr('font-size', 30).attr('font-weight', 500).attr('fill', INK).text('P′');
    iterText = gC.append('text').attr('text-anchor', 'middle').attr('y', 32)
      .attr('font-family', 'var(--mono)').attr('font-size', 11).attr('fill', INK2).text('iteration 1');
    scoreText = gC.append('text').attr('text-anchor', 'middle').attr('y', 52)
      .attr('font-family', 'var(--mono)').attr('font-size', 15).attr('font-weight', 600).attr('fill', SCORE).text('0.290');

    flowDots = svg.append('g');
    gCards = svg.append('g');
    STAGES.forEach((s, i) => drawCard(s, i));
    token = svg.append('circle').attr('r', 6).attr('fill', INK).attr('opacity', 0);

    buildStepList();
    setNarration('The evolution loop', 'press Play to run a generation', INK);
    placeToken(STAGES[0].ang);
  }

  function arcPath(a0, a1) { const [x0, y0] = pt(a0), [x1, y1] = pt(a1); return `M${x0},${y0} A${RR},${RR} 0 0 1 ${x1},${y1}`; }

  function drawCard(s, i) {
    const [cx, cy] = pt(s.ang);
    const w = 162, h = 64;
    const g = gCards.append('g').attr('class', `loop-card lc-${i}`)
      .attr('transform', `translate(${cx - w / 2},${cy - h / 2})`).style('cursor', 'pointer')
      .on('click', () => { stop(); goTo(i, true); });
    g.append('rect').attr('class', 'lc-bg').attr('width', w).attr('height', h).attr('rx', 3)
      .attr('fill', '#ffffff').attr('stroke', 'rgba(0,0,0,.20)').attr('stroke-width', 1);
    g.append('circle').attr('class', 'lc-bar').attr('cx', 22).attr('cy', 22).attr('r', 11)
      .attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 1.6).attr('opacity', .6);
    g.append('text').attr('x', 22).attr('y', 26).attr('text-anchor', 'middle')
      .attr('font-family', 'var(--mono)').attr('font-size', 12).attr('font-weight', 700).attr('fill', s.tcol).text(i + 1);
    g.append('text').attr('x', 40).attr('y', 26).attr('font-family', '"Roboto",sans-serif')
      .attr('font-size', 15).attr('font-weight', 500).attr('fill', INK).text(s.title);
    g.append('text').attr('x', 16).attr('y', 48).attr('font-family', 'var(--mono)')
      .attr('font-size', 10.5).attr('fill', INK2).text(s.sub);
    s._g = g;
  }

  function buildStepList() {
    const ol = $('loop-steps'); if (!ol) return;
    ol.innerHTML = '';
    stepEls = STAGES.map((s, i) => {
      const li = document.createElement('li');
      li.innerHTML = `<span class="ls-num">${i + 1}</span><span class="ls-text">${s.title}</span>`;
      ol.appendChild(li); return li;
    });
  }

  function highlightCard(i) {
    STAGES.forEach((s, j) => {
      const on = j === i;
      s._g.select('.lc-bg').transition().duration(260)
        .attr('stroke', on ? s.color : 'rgba(0,0,0,.20)').attr('stroke-width', on ? 2 : 1);
      s._g.select('.lc-bar').transition().duration(260).attr('opacity', on ? 1 : .6);
    });
    stepEls.forEach((li, j) => li.classList.toggle('active', j === i));
    const activeArc = i >= 0 ? ((i - 1 + 4) % 4) : -1;
    ringArcs.forEach((a, j) => a.transition().duration(220)
      .attr('stroke', (j === activeArc && i >= 0) ? STAGES[i].color : 'rgba(0,0,0,.16)')
      .attr('stroke-width', (j === activeArc && i >= 0) ? 2.6 : 1.5));
  }

  function setNarration(title, body, color) {
    const t = $('ln-title'); if (t) { t.textContent = title; t.style.color = color || INK; }
    const b = $('ln-body'); if (b) b.textContent = body ? '· ' + body : '';
  }

  function placeToken(ang) { const [x, y] = pt(ang); token.attr('cx', x).attr('cy', y).attr('opacity', 1); }

  // Stage advancement is driven by setTimeout (fires even when the tab is
  // backgrounded); the token glide is a cosmetic rAF tween that does not
  // gate progress. This keeps the loop — and the phylogeny growth it drives —
  // running regardless of requestAnimationFrame throttling.
  function goTo(i, immediate) {
    const prev = cur < 0 ? i : cur;
    cur = i;
    const completedGen = (prev === 3 && i === 0);
    highlightCard(i);
    setNarration(STAGES[i].title, STAGES[i].sub, STAGES[i].tcol);
    spawnFlow(STAGES[i].color);
    tweenToken(prev, i, immediate);
    arriveEffect(i);
    if (completedGen && typeof roundCb === 'function') roundCb();
    if (playing) {
      clearTimeout(token._t);
      const dwell = (immediate || prev === i) ? NEXT_DELAY : (MOVE_DUR + NEXT_DELAY);
      token._t = setTimeout(() => { if (playing) goTo((cur + 1) % 4); }, dwell);
    }
  }

  function tweenToken(prev, i, immediate) {
    if (immediate || prev === i) { placeToken(STAGES[i].ang); return; }
    const fromAng = STAGES[prev].ang, toAng = fromAng + 90;
    cancelAnimationFrame(raf);
    const t0 = performance.now();
    (function move(now) {
      const segT = Math.min(1, (now - t0) / MOVE_DUR);
      const e = segT < .5 ? 2 * segT * segT : 1 - Math.pow(-2 * segT + 2, 2) / 2;
      const [x, y] = pt(fromAng + (toAng - fromAng) * e);
      token.attr('cx', x).attr('cy', y).attr('opacity', 1).attr('fill', d3.interpolateRgb(INK, STAGES[i].color)(e));
      if (segT < 1) raf = requestAnimationFrame(move);
      else { const [ex, ey] = pt(toAng); token.attr('cx', ex).attr('cy', ey); }
    })(t0);
  }

  function arriveEffect(i) {
    const [x, y] = pt(STAGES[i].ang);
    svg.append('circle').attr('cx', x).attr('cy', y).attr('r', 7).attr('fill', 'none')
      .attr('stroke', STAGES[i].color).attr('stroke-width', 2)
      .transition().duration(560).ease(d3.easeCubicOut).attr('r', 28).attr('stroke-width', 0).attr('opacity', 0).remove();
    if (STAGES[i].key === 'refl') {
      iter += 1;
      score = Math.min(0.49, score + Math.max(.004, (0.49 - score) * (0.18 + Math.random() * 0.12)));
      iterText.text('iteration ' + iter);
      tick(scoreText, score);
      pcodeText.text('P' + '′'.repeat(Math.min(3, iter)));
    }
  }

  function tick(sel, target) {
    const start = +sel.text() || 0, t0 = performance.now(), dur = 600;
    (function s(now) { const p = Math.min(1, (now - t0) / dur); sel.text((start + (target - start) * p).toFixed(3)); if (p < 1) requestAnimationFrame(s); })(t0);
  }

  function spawnFlow(color) {
    flowDots.selectAll('*').remove();
    const path = ringArcs[(cur - 1 + 4) % 4].node(); if (!path) return;
    const L = path.getTotalLength();
    for (let k = 0; k < 4; k++) {
      const c = flowDots.append('circle').attr('r', 2.2).attr('fill', color).attr('opacity', .8);
      const delay = k * 120, t0 = performance.now();
      (function run(now) {
        let p = ((now - t0 - delay) % 900) / 900;
        if (p < 0) { requestAnimationFrame(run); return; }
        const pos = path.getPointAtLength(p * L);
        c.attr('cx', pos.x).attr('cy', pos.y).attr('opacity', .8 * (1 - p));
        if (cur >= 0 && flowDots.node().contains(c.node())) requestAnimationFrame(run);
      })(t0);
    }
  }

  function scheduleNext() { clearTimeout(token._t); token._t = setTimeout(() => { if (playing) goTo((cur + 1) % 4); }, NEXT_DELAY); }
  function play() { playing = true; setBtn('Pause'); if (cur < 0) goTo(0); else goTo((cur + 1) % 4); }
  function stop() { playing = false; clearTimeout(token._t); cancelAnimationFrame(raf); setBtn('Play'); }
  function setBtn(t) { const b = $('loop-play'); if (b) b.textContent = t; }
  function togglePlay() { playing ? stop() : play(); }
  function step() { stop(); goTo(cur < 0 ? 0 : (cur + 1) % 4, false); }

  function reset() {
    stop(); cur = -1; iter = 1; score = 0.29;
    if (iterText) iterText.text('iteration 1');
    if (scoreText) scoreText.text('0.290');
    if (pcodeText) pcodeText.text('P′');
    setNarration('The evolution loop', 'press Play to run a generation', INK);
    if (token) placeToken(STAGES[0].ang);
    highlightCard(-1);
  }
  function setOnRound(fn) { roundCb = fn; }

  return { init, togglePlay, step, play, stop, reset, setOnRound };
})();
