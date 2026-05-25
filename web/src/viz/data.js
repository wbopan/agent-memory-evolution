/* ============================================================
   M★ data layer
   Unifies phylo.json (radial tree, all 4 tasks) and runs.js
   (window.TREE_DATA + window.CODE_MAP: real per-node Python for
   LoCoMo & ALFWorld) into one per-task model.
   ============================================================ */
import * as d3 from "d3";
import { TREE_DATA, CODE_MAP } from "./runs";
import { EXTRA_TREE, EXTRA_CODE } from "./extra_runs";
import phylo from "./phylo.json";

// Merge the released LoCoMo/ALFWorld runs with the HealthBench/PRBench runs
// reconstructed from outputs/ by scripts/build_demo_runs.py.
const RUNS = { ...TREE_DATA, ...EXTRA_TREE };
const CODE = { ...CODE_MAP, ...EXTRA_CODE };

export const MSTAR = (function () {
  const TASKS = [
    // hex = graphic colour (fills/strokes, needs >=3:1 on white)
    // textHex = AA-safe colour for text/scores (needs >=4.5:1 on white)
    { key:'locomo',   d:0, name:'LoCoMo',      domain:'Conversational QA',  metric:'Token F1',
      color:'#1976d2', hex:'#1976d2', textHex:'#1565c0', runKey:'locomo'   },
    { key:'health',   d:1, name:'HealthBench', domain:'Expert Reasoning',   metric:'Rubric score',
      color:'#d81b60', hex:'#d81b60', textHex:'#c2185b', runKey:'health'   },
    { key:'alfworld', d:2, name:'ALFWorld',    domain:'Embodied Planning',  metric:'Success rate',
      color:'#00897b', hex:'#00897b', textHex:'#00695c', runKey:'alfworld' },
    { key:'prbench',  d:3, name:'PRBench',     domain:'Legal Reasoning',    metric:'Pairwise win',
      color:'#5e35b1', hex:'#5e35b1', textHex:'#5e35b1', runKey:'prbench'  },
  ];
  const byKey = Object.fromEntries(TASKS.map(t => [t.key, t]));

  let _phylo = null;
  async function loadPhylo() {
    if (_phylo) return _phylo;
    _phylo = phylo;
    return _phylo;
  }

  // ---- per-task evolution run, normalized ----
  // node = {id, parent, gen, score, label, tag, hasCode, code}
  const _runCache = {};
  function buildRun(taskKey) {
    if (_runCache[taskKey]) return _runCache[taskKey];
    const task = byKey[taskKey];
    let nodes;
    if (task.runKey && RUNS && RUNS[task.runKey]) {
      nodes = _fromRuns(task);
    } else {
      nodes = _fromPhylo(task);
    }
    // cumulative best score per generation -> trend
    const maxGen = d3.max(nodes, n => n.gen) || 0;
    const trend = [];
    let best = 0;
    for (let g = 0; g <= maxGen; g++) {
      const here = nodes.filter(n => n.gen === g && n.score > 0).map(n => n.score);
      if (here.length) best = Math.max(best, d3.max(here));
      trend.push({ gen: g, best });
    }
    const scored = nodes.filter(n => n.score > 0);
    const seedBest = d3.max(nodes.filter(n => n.gen === 0 && n.score > 0), n => n.score) || 0;
    const finalBest = d3.max(scored, n => n.score) || 0;
    const bestNode = scored.find(n => n.score === finalBest);
    const run = {
      task, nodes, trend, maxGen,
      seedBest, finalBest, bestNodeId: bestNode ? bestNode.id : null,
      maxScore: finalBest, minScore: d3.min(scored, n => n.score) || 0,
      count: nodes.length,
    };
    _runCache[taskKey] = run;
    return run;
  }

  function _fromRuns(task) {
    const raw = RUNS[task.runKey].nodes;
    return raw.map(n => ({
      id: n.id,
      parent: n.parent,
      gen: n.iter,
      score: n.score,
      label: n.label,
      tag: n.tag || '',
      hasCode: !!(CODE && CODE[n.id]),
      code: (CODE && CODE[n.id]) || null,
    }));
  }

  function _fromPhylo(task) {
    if (!_phylo) return [];
    const out = [];
    const rootId = task.key + '-root';
    out.push({ id: rootId, parent: null, gen: 0, score: task.key === 'health' ? 0.29 : 0.29,
      label: 'Seed pool', tag: 'three shared seeds', hasCode: false, code: null });
    let i = 0;
    function walk(node, parentId, parentInTask) {
      const inTask = node.d === task.d && node.label != null;
      let myId = parentId;
      if (inTask) {
        myId = task.key + '-' + (i++);
        const pid = parentInTask ? parentId : rootId;
        out.push({
          id: myId,
          parent: pid,
          gen: 0, // fixed up after, via depth from root
          score: typeof node.score === 'number' ? node.score : 0,
          label: node.label,
          tag: _mutationKind(node.label),
          hasCode: false, code: null,
        });
      }
      (node.children || []).forEach(c => walk(c, inTask ? myId : parentId, inTask));
    }
    (_phylo.children || []).forEach(c => walk(c, rootId, false));
    // compute gen as depth from root
    const map = Object.fromEntries(out.map(n => [n.id, n]));
    function depth(n) {
      let d = 0, cur = n;
      while (cur && cur.parent) { d++; cur = map[cur.parent]; }
      return d;
    }
    out.forEach(n => { if (n.id !== rootId) n.gen = depth(n); });
    return out;
  }

  function _mutationKind(label) {
    if (!label) return '';
    if (label[0] === '+') return 'addition';
    if (label[0] === '−' || label[0] === '-') return 'removal';
    return 'baseline';
  }

  // python syntax highlighter (shared)
  // Strings & comments are stashed as private-use placeholder chars BEFORE
  // tokenizing keywords/builtins, so later rules can never corrupt the
  // markup we already inserted (e.g. \bstr\b matching the "sy-str" class).
  function highlightPython(code) {
    let s = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const store = [];
    const stash = html => String.fromCharCode(0xE000 + store.push(html) - 1);
    s = s.replace(/("""[\s\S]*?"""|'''[\s\S]*?''')/g, m => stash('<span class="sy-str">' + m + '</span>'));
    s = s.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, m => stash('<span class="sy-str">' + m + '</span>'));
    s = s.replace(/(#[^\n]*)/g, m => stash('<span class="sy-cmt">' + m + '</span>'));
    s = s.replace(/(@\w+)/g, m => stash('<span class="sy-dec">' + m + '</span>'));
    s = s.replace(/\b(def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|pass|None|True|False|self|lambda|yield|global|assert|del|is|continue|break)\b/g, '<span class="sy-kw">$1</span>');
    s = s.replace(/\b(str|int|float|list|dict|set|tuple|bool|len|range|print|type|isinstance|super|Optional|Any|field|dataclass)\b/g, '<span class="sy-bi">$1</span>');
    s = s.replace(/\b(\d+\.?\d*)\b/g, '<span class="sy-num">$1</span>');
    s = s.replace(/[-]/g, ch => store[ch.charCodeAt(0) - 0xE000]);
    return s;
  }

  // ---- line-level diff (LCS) between two programs ----
  // returns [{type:' '|'+'|'-', text}] for child vs parent
  function lineDiff(parentCode, childCode) {
    const a = (parentCode || '').split('\n');
    const b = (childCode || '').split('\n');
    const n = a.length, m = b.length;
    // LCS table
    const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { out.push({ t: ' ', s: a[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: '-', s: a[i] }); i++; }
      else { out.push({ t: '+', s: b[j] }); j++; }
    }
    while (i < n) { out.push({ t: '-', s: a[i++] }); }
    while (j < m) { out.push({ t: '+', s: b[j++] }); }
    return out;
  }

  function diffStats(diff) {
    let add = 0, del = 0;
    diff.forEach(d => { if (d.t === '+') add++; else if (d.t === '-') del++; });
    return { add, del };
  }

  return { TASKS, byKey, loadPhylo, buildRun, highlightPython, lineDiff, diffStats };
})();
