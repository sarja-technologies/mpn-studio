<script lang="ts">
  // The centre of the workspace, drawn with Canvas2D. The network is shown as clusters that open one level at a time:
  // its domains, a domain's kinds, a kind's datasets, and then the real nodes of that cluster, a page at a time, with the
  // relationships among them. Every count is the network's own (its count tables); nothing is sampled or invented.
  // Double-clicking a node opens its neighbourhood, which grows by double-clicking neighbours and is capped so the
  // canvas stays bounded.
  import { onMount, untrack } from 'svelte';
  import { errText, fmtInt, get, post, qs } from '../api';
  import Icon from '../ui/Icon.svelte';
  import { net, word, type Card, type GEdge, type GNode, type Graph } from './net.svelte';
  import { packDomains, relax, ring, separate, type DomainCircle, type P } from './layout';

  let { onadd, onconnect }: { onadd: () => void; onconnect: () => void } = $props();

  const CAP = 300;
  /** nodes on one page of a cluster */
  const PAGE = 60;
  // ax: the x of the node this one was placed around; its label points away from it
  type N = GNode & { x: number; y: number; gen: number; ax: number };
  /** a place in the network: everything, a domain, a domain's kind, or one dataset of that kind */
  type Scope = { domain?: string; kind?: string; dataset?: string; dlabel?: string | null };
  type Cluster = { key: string; label: string; nodes: number; scope: Scope };
  type ClusterReply = { level: string; exact: boolean; total: number | null; children: any[]; note?: string };

  let wrap: HTMLDivElement;
  let cv: HTMLCanvasElement;
  let ctx: CanvasRenderingContext2D | null = null;
  let W = 0, H = 0, dpr = 1;
  let view = { x: 0, y: 0, k: 1 };
  // true while the view is the one fit() chose; a pan or zoom by the person turns it off
  let fitted = false;
  let fitK = 1;

  let mode = $state<'map' | 'nodes' | 'graph'>('map');
  /** where the map or the page of nodes is; in a neighbourhood, where it was opened from */
  let scope = $state<Scope>({});
  let clusters: Cluster[] = [];
  let circles: DomainCircle[] = [];
  const byKey = new Map<string, Cluster>();
  /** why a cluster lists its nodes directly (a source without counts inside a domain) */
  let note = $state<string | null>(null);
  /** the page of nodes shown: where it starts, the starts of the pages before it, and the cluster's size */
  let pg = $state<{ start: string | null; prev: (string | null)[]; next: string | null; total: number | null; count: number; label: string }>(
    { start: null, prev: [], next: null, total: null, count: 0, label: '' });
  /** the last page of nodes, kept to go back to it from a neighbourhood */
  let saved: { scope: Scope; pg: typeof pg; nodes: N[]; edges: GEdge[]; view: typeof view; note: string | null } | null = null;
  const nodes = new Map<string, N>();
  const edges = new Map<string, GEdge>();
  let rootId: string | null = null;
  let gen = 0;
  let loading = $state<string | null>(null);
  let gErr = $state<string | null>(null);
  let capped = $state(false);
  let shown = $state(0);
  let linkCount = $state(0);
  let crumbTitle = $state<string | null>(null);

  let hoverNode: string | null = null;
  let hoverEdge: string | null = null;
  let hoverDomain: string | null = null;
  let mouse: P | null = null;
  let cursor = $state('default');

  const ek = (e: { src: string; dst: string; rel: string }) => `${e.src}|${e.dst}|${e.rel}`;
  const toScreen = (p: P): P => ({ x: p.x * view.k + view.x, y: p.y * view.k + view.y });
  const toWorld = (p: P): P => ({ x: (p.x - view.x) / view.k, y: (p.y - view.y) / view.k });

  // ---------------------------------------------------------------- drawing
  let raf = 0;
  function draw() {
    if (!raf) raf = requestAnimationFrame(paint);
  }
  function resize() {
    if (!wrap || !cv) return;
    const r = wrap.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    cv.style.width = W + 'px';
    cv.style.height = H + 'px';
    paint();
  }
  const FONT = '400 12px "Space Grotesk", system-ui, sans-serif';
  const FONT_M = '500 13px "Space Grotesk", system-ui, sans-serif';
  const FONT_S = '500 12px "Space Grotesk", system-ui, sans-serif';
  const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s);

  function paint() {
    raf = 0;
    if (!ctx) return;
    if ((window.devicePixelRatio || 1) !== dpr) return resize();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (mode === 'map') paintMap(ctx);
    else paintGraph(ctx);
  }

  const labelOf = (key: string) => clip(byKey.get(key)?.label ?? key, 34);
  function paintMap(c: CanvasRenderingContext2D) {
    c.textAlign = 'center';
    for (const d of circles) {
      const s = toScreen(d);
      const r = Math.max(2, d.r * view.k);
      if (s.x + r + 80 < 0 || s.x - r - 80 > W || s.y + r + 40 < 0 || s.y - r - 40 > H) continue;
      const hot = hoverDomain === d.domain;
      c.beginPath();
      c.arc(s.x, s.y, r, 0, Math.PI * 2);
      c.fillStyle = hot ? '#1c1c1c' : '#111111';
      c.fill();
      c.lineWidth = 1;
      c.strokeStyle = hot ? '#6a6a6a' : '#303030';
      c.stroke();
    }
    // labels after circles; a label under a small circle that would cover another circle or label waits for zoom
    const boxes: [number, number, number, number][] = [];
    const scr = circles.map((d) => ({ ...toScreen(d), r: Math.max(2, d.r * view.k) }));
    const clear = (x: number, y: number, w: number, h: number) => {
      for (const b of boxes) if (x < b[0] + b[2] && x + w > b[0] && y < b[1] + b[3] && y + h > b[1]) return false;
      return true;
    };
    for (let i = 0; i < circles.length; i++) {
      const d = circles[i];
      const s = scr[i];
      const r = s.r;
      const name = labelOf(d.domain);
      // inside the circle when the name fits, at 13px or else 12px
      c.font = FONT_M;
      let inFont = FONT_M;
      if (c.measureText(name).width + 10 > r * 2) {
        c.font = inFont = FONT_S;
      }
      const tw = c.measureText(name).width;
      c.font = FONT;
      const inside = r >= 22 && Math.max(tw, c.measureText(fmtInt(d.nodes)).width) + 8 <= r * 2;
      if (inside) {
        const bx: [number, number, number, number] = [s.x - tw / 2 - 2, s.y - 14, tw + 4, 34];
        if (hoverDomain !== d.domain && !clear(...bx)) continue;
        boxes.push(bx);
        c.font = inFont;
        c.fillStyle = '#f7f7f2';
        c.fillText(name, s.x, s.y - 1);
        c.font = FONT;
        c.fillStyle = '#ababab';
        c.fillText(fmtInt(d.nodes), s.x, s.y + 15);
      } else {
        c.font = FONT;
        const w = Math.max(c.measureText(name).width, c.measureText(fmtInt(d.nodes)).width) + 8;
        const bx: [number, number, number, number] = [s.x - w / 2, s.y + r + 3, w, 30];
        if (hoverDomain !== d.domain && !clear(...bx)) continue;
        boxes.push(bx);
        c.fillStyle = 'rgba(5,5,5,0.8)';
        c.fillRect(s.x - w / 2, s.y + r + 3, w, 30);
        c.fillStyle = hoverDomain === d.domain ? '#f7f7f2' : '#d8d8d2';
        c.fillText(name, s.x, s.y + r + 14);
        c.fillStyle = '#8a8a8a';
        c.fillText(fmtInt(d.nodes), s.x, s.y + r + 28);
      }
    }
  }

  function paintGraph(c: CanvasRenderingContext2D) {
    const sel = net.sel;
    const ref = (sel && nodes.get(sel)) || (rootId && nodes.get(rootId)) || null;
    const refDomain = ref?.domain ?? null;
    const touch = (e: GEdge) => !!sel && (e.src === sel || e.dst === sel);
    const scr = new Map<string, P>();
    for (const [id, n] of nodes) scr.set(id, toScreen(n));

    // edges: plain, dashed (cross-domain), then the selected node's edges on top
    const batches: [boolean, boolean][] = [[false, false], [true, false], [false, true], [true, true]];
    for (const [dashed, hot] of batches) {
      c.beginPath();
      let any = false;
      for (const [k, e] of edges) {
        if (!!e.cross_domain !== dashed || (touch(e) || hoverEdge === k) !== hot) continue;
        const a = scr.get(e.src), b = scr.get(e.dst);
        if (!a || !b) continue;
        c.moveTo(a.x, a.y);
        c.lineTo(b.x, b.y);
        any = true;
      }
      if (!any) continue;
      c.setLineDash(dashed ? [5, 4] : []);
      c.lineWidth = hot ? 1.4 : 1;
      c.strokeStyle = hot ? '#9a9a94' : '#383838';
      c.stroke();
    }
    c.setLineDash([]);

    // connect mode: a line from the source to the pointer
    if (net.connecting && !net.connecting.dst && mouse) {
      const a = scr.get(net.connecting.src);
      if (a) {
        c.beginPath();
        c.moveTo(a.x, a.y);
        c.lineTo(mouse.x, mouse.y);
        c.setLineDash([3, 3]);
        c.strokeStyle = '#f7f7f2';
        c.lineWidth = 1.2;
        c.stroke();
        c.setLineDash([]);
      }
    }
    if (net.connecting?.dst) {
      const a = scr.get(net.connecting.src), b = scr.get(net.connecting.dst);
      if (a && b) {
        c.beginPath();
        c.moveTo(a.x, a.y);
        c.lineTo(b.x, b.y);
        c.setLineDash([3, 3]);
        c.strokeStyle = '#f7f7f2';
        c.lineWidth = 1.2;
        c.stroke();
        c.setLineDash([]);
      }
    }

    // nodes
    for (const [id, n] of nodes) {
      const s = scr.get(id)!;
      if (s.x < -20 || s.x > W + 20 || s.y < -20 || s.y > H + 20) continue;
      const isSel = id === sel, isRoot = id === rootId;
      const r = isSel || isRoot ? 7 : 5;
      c.beginPath();
      c.arc(s.x, s.y, r, 0, Math.PI * 2);
      if (isSel) {
        c.fillStyle = '#f34b27';
        c.fill();
      } else if (!refDomain || n.domain === refDomain) {
        c.fillStyle = '#d6d6cf';
        c.fill();
      } else {
        c.fillStyle = '#050505';
        c.fill();
        c.lineWidth = 1.5;
        c.strokeStyle = '#ababab';
        c.stroke();
      }
      const ringed = hoverNode === id || net.connecting?.src === id || net.connecting?.dst === id;
      if (ringed) {
        c.beginPath();
        c.arc(s.x, s.y, r + 3.5, 0, Math.PI * 2);
        c.lineWidth = 1.2;
        c.strokeStyle = '#f7f7f2';
        c.stroke();
      }
    }

    // labels, most important first; a label that would overlap one already drawn is skipped
    const boxes: [number, number, number, number][] = [];
    const free = (x: number, y: number, w: number, h: number) => {
      for (const b of boxes) if (x < b[0] + b[2] && x + w > b[0] && y < b[1] + b[3] && y + h > b[1]) return false;
      return true;
    };
    c.font = FONT;
    c.textAlign = 'left';
    c.textBaseline = 'middle';
    const order: string[] = [];
    const push = (id: string | null | undefined) => id && nodes.has(id) && !order.includes(id) && order.push(id);
    push(sel);
    push(hoverNode);
    push(rootId);
    push(net.connecting?.src);
    push(net.connecting?.dst);
    const near = new Set<string>();
    if (sel) for (const e of edges.values()) if (e.src === sel) near.add(e.dst); else if (e.dst === sel) near.add(e.src);
    for (const id of near) push(id);
    const crowd = view.k < 0.3;
    for (const id of nodes.keys()) if (!near.has(id)) order.push(id);
    const important = new Set([sel, hoverNode, rootId, net.connecting?.src, net.connecting?.dst]);
    for (const id of order) {
      const n = nodes.get(id)!;
      const s = scr.get(id)!;
      if (s.x < -200 || s.x > W + 20 || s.y < -20 || s.y > H + 20) continue;
      if (crowd && !important.has(id)) continue;
      const strong = id === sel || id === hoverNode;
      c.font = strong || id === rootId ? FONT_M : FONT;
      const t = clip(n.title || 'Untitled', strong ? 60 : 26);
      const w = c.measureText(t).width;
      // the label points away from the node it was placed around, unless that runs off the canvas
      let leftSide = n.x < n.ax - 0.5;
      if (leftSide && s.x - 10 - w < 4) leftSide = false;
      else if (!leftSide && s.x + 10 + w > W - 4 && s.x - 10 - w >= 4) leftSide = true;
      const x = leftSide ? s.x - 10 - w : s.x + 10, y = s.y - 9;
      if (!free(x - 2, y, w + 4, 18) && !strong) continue;
      boxes.push([x - 2, y, w + 4, 18]);
      if (strong || id === rootId) {
        c.fillStyle = 'rgba(5,5,5,0.85)';
        c.fillRect(x - 3, y, w + 6, 18);
      }
      c.fillStyle = strong ? '#f7f7f2' : near.has(id) || id === rootId ? '#dcdcd5' : '#9c9c97';
      c.fillText(t, x, s.y);
    }

    // relationship labels: on hover, every one when there are few, or the selected node's when it has few
    const few = edges.size <= 14;
    let selEdges = 0;
    if (sel) for (const e of edges.values()) if (e.src === sel || e.dst === sel) selEdges++;
    c.font = FONT;
    c.textAlign = 'center';
    for (const [k, e] of edges) {
      if (!(few || hoverEdge === k || (selEdges <= 12 && touch(e)))) continue;
      const a = scr.get(e.src), b = scr.get(e.dst);
      if (!a || !b) continue;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const t = clip(e.rel, 32);
      const w = c.measureText(t).width + 8;
      if (hoverEdge !== k && !free(mx - w / 2, my - 9, w, 18)) continue;
      boxes.push([mx - w / 2, my - 9, w, 18]);
      c.fillStyle = '#050505';
      c.fillRect(mx - w / 2, my - 9, w, 18);
      c.strokeStyle = hoverEdge === k ? '#6a6a6a' : '#262626';
      c.lineWidth = 1;
      c.strokeRect(mx - w / 2 + 0.5, my - 8.5, w - 1, 17);
      c.fillStyle = hoverEdge === k ? '#f7f7f2' : '#ababab';
      c.fillText(t, mx, my + 0.5);
    }
    c.textBaseline = 'alphabetic';
  }

  // ---------------------------------------------------------------- view
  function bounds(): [number, number, number, number] | null {
    if (mode === 'map') return circleBounds();
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes.values()) {
      x0 = Math.min(x0, n.x);
      y0 = Math.min(y0, n.y);
      x1 = Math.max(x1, n.x);
      y1 = Math.max(y1, n.y);
    }
    return x0 === Infinity ? null : [x0, y0, x1, y1];
  }
  function circleBounds(): [number, number, number, number] | null {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const d of circles) {
      x0 = Math.min(x0, d.x - d.claim);
      y0 = Math.min(y0, d.y - d.claim);
      x1 = Math.max(x1, d.x + d.claim);
      y1 = Math.max(y1, d.y + d.claim);
    }
    return x0 === Infinity ? null : [x0, y0, x1, y1];
  }
  function fit() {
    const b = bounds();
    if (!b || !W) return;
    // room for the toolbar, the legend and labels to the right of nodes
    // node labels run up to ~180px beside their node; a narrow canvas gives them less room
    const lab = mode !== 'map' ? Math.min(185, W * 0.18) : 40;
    const padL = lab, padR = lab, padT = 60, padB = 44;
    const bw = Math.max(1, b[2] - b[0]), bh = Math.max(1, b[3] - b[1]);
    const k = Math.max(0.08, Math.min(1.4, (W - padL - padR) / bw, (H - padT - padB) / bh));
    view = { k, x: padL + (W - padL - padR - bw * k) / 2 - b[0] * k, y: padT + (H - padT - padB - bh * k) / 2 - b[1] * k };
    fitted = true;
    fitK = k;
    draw();
  }
  /** after a change of level, zooming waits a moment before it can change level again */
  let calm = 0;
  function zoomAt(f: number, at: P = { x: W / 2, y: H / 2 }) {
    // the map zooms far enough for any cluster to fill the canvas (and so open)
    // a map or a page never shrinks below half its fitted size: zooming out further goes up a level, or at the top stops
    const k = Math.max(mode === 'graph' ? 0.06 : fitK * 0.5, Math.min(mode === 'map' ? 60 : 4, view.k * f));
    if (k !== view.k) {
      const w = toWorld(at);
      view = { k, x: at.x - w.x * k, y: at.y - w.y * k };
      fitted = false;
      draw();
    }
    if (mode === 'graph' || loading || performance.now() < calm) return;
    // zooming into a cluster until it fills the canvas opens it; zooming well out of a level goes up one
    if (f > 1 && mode === 'map') {
      const key = hitDomain(at);
      const d = key && circles.find((c) => c.domain === key);
      if (d && d.r * view.k > 0.42 * Math.min(W, H)) openCluster(key!);
    } else if (f < 1 && scope.domain && view.k < fitK * 0.55) up();
  }

  // ---------------------------------------------------------------- clusters
  const skey = (s: Scope) => `${s.domain ?? ''}|${s.kind ?? ''}|${s.dataset ?? ''}`;
  const same = (a: Scope, b: Scope) => skey(a) === skey(b);
  function parentOf(s: Scope): Scope {
    if (s.dataset !== undefined) return { domain: s.domain, kind: s.kind };
    if (s.kind) return { domain: s.domain };
    return {};
  }
  /** cluster replies already read, by place (a few rows each; cleared when the network changes) */
  const cache = new Map<string, ClusterReply>();
  /** node counts of the clusters seen, by place, for "61–120 of 499,054" */
  const totals = new Map<string, number>();
  /** places with a single cluster inside: opening them goes straight through */
  const single = new Set<string>();
  let sv = $state(0);

  async function clustersOf(s: Scope, signal: AbortSignal): Promise<ClusterReply> {
    if (!s.domain) {
      const ds = (net.ov?.domains ?? []).filter((d: any) => d.nodes > 0);
      return { level: 'network', exact: true, total: net.ov?.validated_nodes ?? null, children: ds };
    }
    const k = skey(s);
    const have = cache.get(k);
    if (have) return have;
    const r = await get<ClusterReply>(`api/ws/clusters${qs({ domain: s.domain, kind: s.kind })}`, signal);
    if (cache.size > 64) cache.clear();
    cache.set(k, r);
    return r;
  }
  function toClusters(r: ClusterReply, s: Scope): Cluster[] {
    return r.children.map((c: any) => {
      const sc: Scope = !s.domain ? { domain: c.domain } : !s.kind ? { domain: s.domain, kind: c.kind } : { domain: s.domain, kind: s.kind, dataset: c.dataset, dlabel: c.label };
      const key = !s.domain ? c.domain : !s.kind ? c.kind : c.dataset;
      const label = !s.domain ? word(c.domain) : !s.kind ? word(c.kind) : c.label || c.dataset;
      totals.set(skey(sc), c.nodes);
      return { key, label, nodes: c.nodes, scope: sc };
    });
  }

  let seq = 0;
  let ctl: AbortController | null = null;
  function begin() {
    const my = ++seq;
    ctl?.abort();
    ctl = new AbortController();
    gErr = null;
    if (net.connecting) net.connecting = null;
    return { my, signal: ctl.signal };
  }
  /** show a place: the clusters inside it, or its nodes when it has no clusters to show; a place with a single cluster
   * inside is passed through (downwards when opening, upwards when going back) */
  async function go(target: Scope, dir: 'down' | 'up' = 'down', quiet = false) {
    const { my, signal } = begin();
    if (!quiet) loading = target.domain ? 'Reading the cluster…' : null;
    try {
      let s = target;
      for (let hop = 0; hop < 5; hop++) {
        if (s.dataset !== undefined) return await page(s, null, [], my, signal);
        const r = await clustersOf(s, signal);
        if (my !== seq) return;
        if (!s.domain && !r.children.length) return showMap(s, r);
        if (!r.exact || !r.children.length) {
          // a source that does not report what is inside a domain: list the domain's nodes
          return await page(s, null, [], my, signal, r.note ?? null);
        }
        if (r.children.length === 1) {
          if (!single.has(skey(s))) (single.add(skey(s)), sv++);
          if (dir === 'up' && s.domain) {
            s = parentOf(s);
            continue;
          }
          if (dir === 'down') {
            s = toClusters(r, s)[0].scope;
            continue;
          }
        }
        return showMap(s, r);
      }
    } catch (e) {
      if (my !== seq || signal.aborted) return;
      gErr = errText(e);
      draw();
    } finally {
      if (my === seq) loading = null;
    }
  }
  function openCluster(key: string) {
    const c = byKey.get(key);
    if (c) go(c.scope, 'down');
  }
  function syncFilters(s: Scope) {
    // the explorer on the left lists what the canvas shows
    if (net.q) return;
    if (net.domain !== (s.domain ?? '')) net.domain = s.domain ?? '';
    if (net.kind !== (s.kind ?? '')) net.kind = s.kind ?? '';
  }
  function clearNodes() {
    nodes.clear();
    edges.clear();
    rootId = null;
    gen = 0;
    capped = false;
    hoverNode = hoverEdge = hoverDomain = null;
  }
  function showMap(s: Scope, r: ClusterReply) {
    scope = s;
    clusters = toClusters(r, s);
    byKey.clear();
    for (const c of clusters) byKey.set(c.key, c);
    note = null;
    crumbTitle = null;
    mode = 'map';
    clearNodes();
    syncFilters(s);
    calm = performance.now() + 450;
    changed();
    repack();
  }

  /** one page of a cluster's real nodes, read with the network's own cursor; a cluster spread thinly through a large
   * network takes a few bounded reads to fill a page */
  async function page(s: Scope, start: string | null, prev: (string | null)[], my: number, signal: AbortSignal, why: string | null = null) {
    loading = 'Reading the nodes…';
    const got: Card[] = [];
    const seen = new Set<string>();
    let cur = start, calls = 0;
    do {
      const r = await get<any>(`api/ws/browse${qs({ domain: s.domain, kind: s.kind, dataset: s.dataset, cursor: cur, limit: PAGE - got.length })}`, signal);
      if (my !== seq) return;
      for (const c of r.results ?? []) if (!seen.has(c.id)) (seen.add(c.id), got.push(c));
      cur = r.cursor ?? null;
      calls++;
    } while (cur && got.length < PAGE && calls < 8);
    let links: GEdge[] = [];
    if (got.length > 1) {
      try {
        links = (await post<any>('api/ws/links', { ids: got.map((c) => c.id) }, signal)).edges ?? [];
      } catch (e) {
        if (signal.aborted) return;
      }
      if (my !== seq) return;
    }
    clearNodes();
    // a sunflower: evenly spread, the first nodes in the middle, the same page always in the same places
    got.forEach((c, i) => {
      const r = 62 * Math.sqrt(i + 0.5), a = i * 2.399963;
      nodes.set(c.id, { id: c.id, title: c.title, domain: c.domain, kind: c.kind, edited: c.edited, x: r * Math.cos(a), y: r * Math.sin(a), gen: 0, ax: 0 });
    });
    for (const e of links) if (nodes.has(e.src) && nodes.has(e.dst)) edges.set(ek(e), e);
    scope = s;
    note = why;
    pg = { start, prev, next: cur, total: totals.get(skey(s)) ?? (s.domain && !s.kind ? domainTotal(s.domain) : null), count: got.length,
      label: s.dlabel ?? '' };
    linkCount = edges.size;
    crumbTitle = null;
    mode = 'nodes';
    syncFilters(s);
    calm = performance.now() + 450;
    changed();
    fit();
  }
  const domainTotal = (d: string) => (net.ov?.domains ?? []).find((x: any) => x.domain === d)?.nodes ?? null;
  async function turn(dir: 1 | -1) {
    const { my, signal } = begin();
    const s = scope;
    try {
      if (dir === 1) await page(s, pg.next, [...pg.prev, pg.start], my, signal, note);
      else {
        const p = [...pg.prev];
        const st = p.pop() ?? null;
        await page(s, st, p, my, signal, note);
      }
    } catch (e) {
      if (my === seq && !signal.aborted) gErr = errText(e);
    } finally {
      if (my === seq) loading = null;
    }
  }
  function snapshot() {
    saved = { scope: { ...scope }, pg: { ...pg, prev: [...pg.prev] }, nodes: [...nodes.values()].map((n) => ({ ...n })), edges: [...edges.values()],
      view: { ...view }, note };
  }
  function restore() {
    if (!saved) return;
    begin();
    loading = null;
    clearNodes();
    for (const n of saved.nodes) nodes.set(n.id, { ...n });
    for (const e of saved.edges) edges.set(ek(e), e);
    scope = saved.scope;
    pg = saved.pg;
    note = saved.note;
    view = saved.view;
    fitted = false;
    linkCount = edges.size;
    crumbTitle = null;
    mode = 'nodes';
    syncFilters(scope);
    calm = performance.now() + 450;
    changed();
  }
  /** one level up: from a neighbourhood back to the page it was opened from (or its node's cluster), from a page or a
   * map to the place around it */
  function up() {
    if (mode === 'graph') {
      if (saved && same(saved.scope, scope)) return restore();
      return go(scope, 'down');
    }
    if (scope.domain) go(parentOf(scope), 'up');
  }
  function toPlace(s: Scope) {
    // back to the page the neighbourhood was opened from, also through a level with a single cluster inside
    const back = saved && (same(saved.scope, s) || (saved.scope.dataset !== undefined && single.has(skey(s)) && same(parentOf(saved.scope), s)));
    if (mode === 'graph' && back) return restore();
    go(s, 'down');
  }

  // where you are: Network › domain › kind › dataset (› node); a level with a single cluster inside is not a stop
  type Crumb = { label: string; at?: Scope };
  const crumbs = $derived.by<Crumb[]>(() => {
    void sv;
    const s = scope;
    const out: Crumb[] = [{ label: 'Network', at: {} }];
    if (s.domain) out.push({ label: word(s.domain), at: { domain: s.domain } });
    if (s.domain && s.kind) out.push({ label: word(s.kind), at: { domain: s.domain, kind: s.kind } });
    if (s.dataset !== undefined && !single.has(skey({ domain: s.domain, kind: s.kind }))) out.push({ label: s.dlabel || s.dataset, at: { ...s } });
    if (mode === 'graph') out.push({ label: crumbTitle || 'Untitled' });
    // the place shown is not a link to itself
    if (mode !== 'graph') out[out.length - 1].at = undefined;
    return out;
  });

  // ---------------------------------------------------------------- map layout
  function measure(key: string) {
    if (!ctx) return labelOf(key).length * 7;
    ctx.font = FONT_M;
    return ctx.measureText(labelOf(key)).width;
  }
  // the map is packed for the canvas it is shown in, so it reads at its natural size (labels do not scale)
  let packedFor = '';
  function repack() {
    const items = clusters.map((c) => ({ domain: c.key, nodes: c.nodes }));
    const availW = Math.max(200, W - 80), availH = Math.max(160, H - 110);
    // first with room for every label; a small canvas packs the circles tight and shows the labels that fit
    for (const room of [true, false]) {
      let maxR = Math.max(36, Math.min(110, Math.min(availW, availH) * (room ? 0.17 : 0.22)));
      let f = 0;
      for (let i = 0; i < 5; i++) {
        circles = packDomains(items, measure, maxR, room, Math.max(0.6, Math.min(2.2, availW / availH)));
        const b = circleBounds();
        if (!b) break;
        f = Math.min(availW / (b[2] - b[0]), availH / (b[3] - b[1]));
        if (f >= 0.97 || maxR <= 30) break;
        maxR = Math.max(30, maxR * Math.max(0.6, f));
      }
      if (f >= 0.85) break;
    }
    packedFor = `${W}x${H}`;
    if (mode === 'map') fit();
  }

  async function fetchGraph(id: string): Promise<Graph> {
    await net.pending;
    return get<Graph>(`api/ws/node/${encodeURIComponent(id)}/graph?limit=40`);
  }
  function setEdges(g: Graph, sync: string | null) {
    if (sync) {
      // an edge of the refreshed node to a node in its new neighbourhood that is no longer listed has been removed
      const now = new Set(g.edges.map(ek));
      const inG = new Set(g.nodes.map((n) => n.id));
      for (const [k, e] of edges) {
        if (e.src !== sync && e.dst !== sync) continue;
        const other = e.src === sync ? e.dst : e.src;
        if (inG.has(other) && !now.has(k)) edges.delete(k);
      }
    }
    for (const e of g.edges) if (nodes.has(e.src) && nodes.has(e.dst)) edges.set(ek(e), e);
  }
  function update(n: GNode) {
    const have = nodes.get(n.id);
    if (have) Object.assign(have, { title: n.title, domain: n.domain, kind: n.kind, edited: n.edited });
  }
  function order(list: GNode[], centre: GNode | undefined) {
    return [...list].sort((a, b) => {
      const sa = a.domain === centre?.domain ? 0 : 1, sb = b.domain === centre?.domain ? 0 : 1;
      return sa - sb || (a.domain ?? '').localeCompare(b.domain ?? '') || (a.title ?? '').localeCompare(b.title ?? '');
    });
  }

  async function root(id: string) {
    const fromPage = mode === 'nodes' && nodes.has(id);
    if (mode === 'nodes') snapshot();
    const { my } = begin();
    loading = 'Reading the neighbourhood…';
    try {
      const g = await fetchGraph(id);
      if (my !== seq) return;
      nodes.clear();
      edges.clear();
      gen = 0;
      rootId = g.center;
      const centre = g.nodes.find((n) => n.id === g.center) ?? { id: g.center, title: null, domain: null, kind: null };
      nodes.set(g.center, { ...centre, x: 0, y: 0, gen: 0, ax: -1 });
      const others = order(g.nodes.filter((n) => n.id !== g.center), centre);
      const pos = ring(others.map((n) => n.id), { x: 0, y: 0 }, null);
      for (const n of others) nodes.set(n.id, { ...n, ...pos[n.id], gen: 0, ax: 0 });
      setEdges(g, null);
      // opened from the explorer or by id: the node's own cluster is where it sits
      if (!fromPage) scope = centre.domain ? { domain: centre.domain, ...(centre.kind ? { kind: centre.kind } : {}) } : {};
      crumbTitle = centre.title;
      note = null;
      mode = 'graph';
      changed();
      fit();
    } catch (e) {
      if (my !== seq) return;
      gErr = errText(e);
      draw();
    } finally {
      if (my === seq) loading = null;
    }
  }
  async function expand(id: string, near: string | null = null) {
    if (mode !== 'graph' || !nodes.size) return root(id);
    const { my } = begin();
    loading = 'Reading the neighbourhood…';
    try {
      const g = await fetchGraph(id);
      if (my !== seq) return;
      gen++;
      const cid = g.center;
      const anchor = (near && nodes.get(near)) || (rootId && nodes.get(rootId)) || null;
      if (!nodes.has(cid)) {
        // a node that is not on the canvas yet goes beside the node it was reached from
        const from = anchor ?? { x: 0, y: 0 };
        const away = rootId && nodes.get(rootId) && anchor && anchor.id !== rootId ? nodes.get(rootId)! : null;
        const a = away ? Math.atan2(from.y - away.y, from.x - away.x) : -Math.PI / 4;
        const c = g.nodes.find((n) => n.id === cid) ?? { id: cid, title: null, domain: null, kind: null };
        nodes.set(cid, { ...c, x: from.x + Math.cos(a) * 170, y: from.y + Math.sin(a) * 170, gen, ax: from.x });
        separate(nodes as Map<string, P>, new Set([cid]), 40);
      }
      const at = nodes.get(cid)!;
      const fresh = order(g.nodes.filter((n) => !nodes.has(n.id)), at);
      for (const n of g.nodes) update(n);
      const parent = anchor && anchor.id !== cid ? anchor : rootId && rootId !== cid ? nodes.get(rootId)! : null;
      const pos = ring(fresh.map((n) => n.id), at, parent ?? { x: at.x - 1, y: at.y });
      for (const n of fresh) nodes.set(n.id, { ...n, ...pos[n.id], gen, ax: at.x });
      relax(nodes as Map<string, P>, new Map(fresh.map((n) => [n.id, at])));
      separate(nodes as Map<string, P>, new Set(fresh.map((n) => n.id)), 34);
      setEdges(g, null);
      cap();
      changed();
      ensureVisible([cid, ...fresh.map((n) => n.id)]);
    } catch (e) {
      if (my !== seq) return;
      gErr = errText(e);
    } finally {
      if (my === seq) loading = null;
    }
  }
  async function refresh(ids: string[]) {
    if (mode === 'nodes') {
      // a page of nodes: the edited node's new title
      const d = net.detail?.node;
      if (d && nodes.has(d.id)) update(d);
      return changed();
    }
    if (mode !== 'graph') return;
    const list = (ids.length ? ids : rootId ? [rootId] : []).filter((id) => nodes.has(id));
    for (const id of list) {
      try {
        const g = await fetchGraph(id);
        for (const n of g.nodes) update(n);
        const at = nodes.get(id);
        if (!at) continue;
        const fresh = g.nodes.filter((n) => !nodes.has(n.id));
        const pos = ring(fresh.map((n) => n.id), at, rootId && rootId !== id ? nodes.get(rootId)! : { x: at.x - 1, y: at.y });
        for (const n of fresh) nodes.set(n.id, { ...n, ...pos[n.id], gen, ax: at.x });
        if (fresh.length) {
          relax(nodes as Map<string, P>, new Map(fresh.map((n) => [n.id, at])));
          separate(nodes as Map<string, P>, new Set(fresh.map((n) => n.id)), 34);
        }
        setEdges(g, id);
      } catch (e) {
        if (net.isGone(e)) drop(id);
      }
    }
    cap();
    changed();
  }
  function drop(id: string) {
    nodes.delete(id);
    for (const [k, e] of edges) if (e.src === id || e.dst === id) edges.delete(k);
    if (rootId === id) rootId = nodes.keys().next().value ?? null;
    if (mode === 'nodes') linkCount = edges.size;
    if (!nodes.size && mode === 'graph') up();
    else if (!nodes.size && mode === 'nodes') go(scope);
    changed();
  }
  function dropEdge(src: string, dst: string, rel: string) {
    for (const [k, e] of edges) if (e.src === src && e.dst === dst && (e.rel === rel || e.rel === rel.replace(/_/g, ' '))) edges.delete(k);
    changed();
  }
  /** let go of the oldest expansions until the canvas is within its cap */
  function cap() {
    capped = false;
    while (nodes.size > CAP) {
      const keep = new Set([net.sel, rootId, net.connecting?.src, net.connecting?.dst]);
      let oldest = Infinity;
      for (const n of nodes.values()) if (n.gen < gen && !keep.has(n.id)) oldest = Math.min(oldest, n.gen);
      if (oldest === Infinity) break;
      for (const [id, n] of nodes) if (n.gen === oldest && !keep.has(id)) nodes.delete(id);
      capped = true;
    }
    for (const [k, e] of edges) if (!nodes.has(e.src) || !nodes.has(e.dst)) edges.delete(k);
  }
  function changed() {
    shown = nodes.size;
    draw();
  }
  /** fit the view when any of these nodes landed outside it */
  function ensureVisible(ids: string[]) {
    for (const id of ids) {
      const n = nodes.get(id);
      if (!n) continue;
      const s = toScreen(n);
      if (s.x < 20 || s.x > W - 20 || s.y < 56 || s.y > H - 40) return fit();
    }
    draw();
  }
  /** back to the whole network (another project was opened: nothing read before applies) */
  function overview() {
    cache.clear();
    totals.clear();
    single.clear();
    saved = null;
    clearNodes();
    shown = 0;
    go({});
  }

  // ---------------------------------------------------------------- pointer
  function hitNode(p: P): string | null {
    let best: string | null = null, bd = 100;
    for (const [id, n] of nodes) {
      const s = toScreen(n);
      const d = (s.x - p.x) ** 2 + (s.y - p.y) ** 2;
      if (d < bd) {
        bd = d;
        best = id;
      }
    }
    return best;
  }
  function hitEdge(p: P): string | null {
    let best: string | null = null, bd = 25;
    for (const [k, e] of edges) {
      const a = nodes.get(e.src), b = nodes.get(e.dst);
      if (!a || !b) continue;
      const sa = toScreen(a), sb = toScreen(b);
      const dx = sb.x - sa.x, dy = sb.y - sa.y;
      const L = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((p.x - sa.x) * dx + (p.y - sa.y) * dy) / L));
      const d = (sa.x + t * dx - p.x) ** 2 + (sa.y + t * dy - p.y) ** 2;
      if (d < bd) {
        bd = d;
        best = k;
      }
    }
    return best;
  }
  function hitDomain(p: P): string | null {
    const w = toWorld(p);
    let best: string | null = null;
    for (const d of circles) {
      const r = Math.max(d.r, 8 / view.k);
      if ((w.x - d.x) ** 2 + (w.y - d.y) ** 2 <= r * r) best = d.domain;
    }
    if (best) return best;
    // the label under a small circle is part of it
    for (const d of circles) {
      if (d.inside) continue;
      const s = toScreen(d), r = d.r * view.k;
      if (Math.abs(p.x - s.x) < 50 && p.y > s.y + r && p.y < s.y + r + 34) return d.domain;
    }
    return null;
  }
  const local = (e: MouseEvent): P => {
    const r = cv.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  let drag: { kind: 'pan' | 'node'; id?: string; start: P; at: P; moved: boolean; vx: number; vy: number } | null = null;
  function down(e: PointerEvent) {
    if (e.button !== 0) return;
    cv.focus({ preventScroll: true });
    const p = local(e);
    const id = mode !== 'map' ? hitNode(p) : null;
    drag = { kind: id ? 'node' : 'pan', id: id ?? undefined, start: p, at: p, moved: false, vx: view.x, vy: view.y };
    cv.setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    const p = local(e);
    mouse = p;
    if (drag) {
      if (!drag.moved && Math.hypot(p.x - drag.start.x, p.y - drag.start.y) > 3) drag.moved = true;
      if (drag.moved) {
        if (drag.kind === 'pan') (view = { ...view, x: drag.vx + p.x - drag.start.x, y: drag.vy + p.y - drag.start.y }), (fitted = false);
        else if (drag.id) {
          const n = nodes.get(drag.id);
          if (n) {
            n.x += (p.x - drag.at.x) / view.k;
            n.y += (p.y - drag.at.y) / view.k;
          }
        }
        drag.at = p;
        cursor = 'grabbing';
        draw();
        return;
      }
    }
    if (mode !== 'map') {
      const n = hitNode(p);
      const ed = n ? null : hitEdge(p);
      if (n !== hoverNode || ed !== hoverEdge || net.connecting) {
        hoverNode = n;
        hoverEdge = ed;
        draw();
      }
      cursor = net.connecting && !net.connecting.dst ? 'crosshair' : n ? 'pointer' : 'grab';
    } else {
      const d = hitDomain(p);
      if (d !== hoverDomain) {
        hoverDomain = d;
        draw();
      }
      cursor = d ? 'pointer' : 'grab';
    }
  }
  function up_(e: PointerEvent) {
    const d = drag;
    drag = null;
    if (cv.hasPointerCapture(e.pointerId)) cv.releasePointerCapture(e.pointerId);
    if (!d || d.moved) {
      cursor = 'default';
      return move(e);
    }
    click(local(e));
  }
  function click(p: P) {
    if (mode === 'map') {
      const d = hitDomain(p);
      if (d) openCluster(d);
      return;
    }
    const id = hitNode(p);
    if (net.connecting && !net.connecting.dst) {
      if (id && id !== net.connecting.src) {
        const n = nodes.get(id)!;
        net.connecting = { ...net.connecting, dst: id, dstTitle: n.title };
        draw();
      }
      return;
    }
    if (id && id !== net.sel) net.select(id);
  }
  function dbl(e: MouseEvent) {
    if (mode === 'map' || net.connecting) return;
    const id = hitNode(local(e));
    if (!id) return;
    if (id !== net.sel) net.select(id);
    // on a page of nodes: open this node's neighbourhood; in a neighbourhood: grow it
    if (mode === 'nodes') root(id);
    else expand(id, id);
  }
  function wheel(e: WheelEvent) {
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    zoomAt(Math.exp(-dy * 0.0015), local(e));
  }
  function leave() {
    mouse = null;
    if (hoverNode || hoverEdge || hoverDomain) {
      hoverNode = hoverEdge = hoverDomain = null;
      draw();
    }
  }
  function key(e: KeyboardEvent) {
    if (e.key === '+' || e.key === '=') zoomAt(1.25);
    else if (e.key === '-') zoomAt(0.8);
    else if (e.key === '0') fit();
    else if (e.key === 'Backspace' && !net.connecting) {
      e.preventDefault();
      up();
    }
  }

  // ---------------------------------------------------------------- wiring
  $effect(() => {
    // the network changed (opened, or an edit in a fork): counts are read again
    void net.ov;
    untrack(() => {
      cache.clear();
      if (ctx && mode === 'map') go(scope, 'down', true);
    });
  });
  $effect(() => {
    void net.sel;
    void net.connecting;
    draw();
  });

  const fromTo = $derived(pg.count ? `${fmtInt(pg.prev.length * PAGE + 1)}–${fmtInt(pg.prev.length * PAGE + pg.count)}` : '');
  const legend = $derived.by(() => {
    if (net.connecting && !net.connecting.dst) return `Click the node to connect “${net.connecting.srcTitle}” to.`;
    if (mode === 'map') {
      if (!net.ov) return net.ovErr ? '' : 'Reading the network…';
      if (!scope.domain) return 'Each circle is a domain, sized by its number of nodes. Click one, or zoom into it, to see what it holds.';
      if (!scope.kind) return `Each circle is one kind of node in ${word(scope.domain)}. Click one to open it. Zoom out or press Backspace to go back.`;
      return `Each circle is a dataset these nodes come from. Click one to see its nodes.`;
    }
    if (mode === 'nodes') {
      if (!pg.count) return pg.next ? 'None of these nodes in this stretch of the network. Next reads further.' : 'No nodes here.';
      const of = pg.total != null ? ` of ${fmtInt(pg.total)}` : '';
      const rel = linkCount ? `${fmtInt(linkCount)} ${linkCount === 1 ? 'relationship' : 'relationships'} among them.` : 'None of them is related to another.';
      return `Nodes ${fromTo}${of}. ${rel} Click a node to inspect it, double-click to open its relationships.${note ? ' ' + note : ''}`;
    }
    if (nodes.size === 1 && !edges.size) return `“${crumbTitle || 'This node'}” has no relationships stored in this network.`;
    return `Double-click a node to expand it. Hollow nodes and dashed lines lie in another domain.${capped ? ` Showing the newest ${fmtInt(shown)} nodes.` : ''}`;
  });

  onMount(() => {
    ctx = cv.getContext('2d');
    const ro = new ResizeObserver(() => {
      const first = !W;
      const [w0, h0] = [W, H];
      resize();
      if (mode === 'map' && packedFor !== `${W}x${H}`) repack();
      else if (first) fit();
      else if ((W !== w0 || H !== h0) && fitted) fit();
      else if (W !== w0 || H !== h0) {
        // keep what was in the middle of the view in the middle when the window or a side panel changes size
        view = { ...view, x: view.x + (W - w0) / 2, y: view.y + (H - h0) / 2 };
        draw();
      }
    });
    ro.observe(wrap);
    resize();
    document.fonts?.ready.then(() => {
      if (mode === 'map') repack();
      draw();
    });
    cv.addEventListener('wheel', wheel, { passive: false });
    net.canvas = {
      root,
      expand,
      refresh,
      drop,
      dropEdge,
      has: (id) => nodes.has(id),
      overview,
      open: (domain) => go({ domain }),
      up,
      fit,
      mode: () => mode,
    };
    if (net.sel) root(net.sel);
    else go({});
    // for the end-to-end checks: where each node and cluster is drawn
    if (import.meta.env.DEV)
      (window as any).__netCanvas = {
        mode: () => mode, view: () => view, scope: () => scope, edges: () => edges.size,
        nodes: () => [...nodes.values()].map((n) => ({ id: n.id, title: n.title, kind: n.kind, ...toScreen(n) })),
        clusters: () => circles.map((c) => ({ key: c.domain, nodes: c.nodes, ...toScreen(c), r: c.r * view.k })),
      };
    return () => {
      ro.disconnect();
      cv.removeEventListener('wheel', wheel);
      cancelAnimationFrame(raf);
      ctl?.abort();
      if (net.canvas?.root === root) net.canvas = null;
    };
  });
</script>

<div class="cv" bind:this={wrap} data-mode={mode} data-nodes={shown} data-place={skey(scope)}>
  <canvas bind:this={cv} tabindex="0" style="cursor:{cursor}"
    aria-label={mode === 'graph' ? 'Neighbourhood of the selected node' : mode === 'nodes' ? 'Nodes of the cluster' : 'Clusters of the network'}
    onpointerdown={down} onpointermove={move} onpointerup={up_} onpointerleave={leave} ondblclick={dbl} onkeydown={key}></canvas>

  <div class="tools">
    {#if mode === 'graph' || scope.domain}
      <button class="btn icon upbtn" onclick={up} aria-label="Back" title="Back one level (Backspace)"><Icon name="back" /></button>
    {/if}
    <nav class="crumbs" aria-label="Where you are in the network">
      {#each crumbs as c, i (i)}
        {#if i}<span class="sep" aria-hidden="true">›</span>{/if}
        {#if c.at}
          <button class="crumb" onclick={() => toPlace(c.at!)} title={c.label}>{c.label}</button>
        {:else}
          <span class="crumb here" aria-current="location" title={c.label}>{c.label}</span>
        {/if}
      {/each}
    </nav>
    {#if mode === 'nodes' && (pg.prev.length || pg.next)}
      <div class="pair">
        <button class="btn icon" onclick={() => turn(-1)} disabled={!pg.prev.length || !!loading} aria-label="Previous nodes" title="The {PAGE} nodes before these"><Icon name="back" /></button>
        <button class="btn icon" onclick={() => turn(1)} disabled={!pg.next || !!loading} aria-label="Next nodes" title="The next {PAGE} nodes"><Icon name="forward" /></button>
      </div>
    {/if}
    <div class="pair">
      <button class="btn icon" onclick={() => zoomAt(0.8)} aria-label="Zoom out" title="Zoom out"><Icon name="minus" /></button>
      <button class="btn icon" onclick={() => zoomAt(1.25)} aria-label="Zoom in" title="Zoom in"><Icon name="plus" /></button>
    </div>
    <button class="btn" onclick={fit} aria-label="Fit" title="Fit everything in view"><Icon name="fit" size={14} /><span class="t">Fit</span></button>
    {#if net.editable}
      <span class="gap"></span>
      <button class="btn" onclick={onadd} aria-label="Add node" title="Add a node (N)"><Icon name="plus" size={14} /><span class="t">Add node</span></button>
      <button class="btn" onclick={onconnect} disabled={!net.sel || !!net.connecting} aria-pressed={!!net.connecting}
        aria-label="Connect" title={net.sel ? 'Connect the selected node to another (C)' : 'Select a node first'}><Icon name="connect" size={14} /><span class="t">Connect</span></button>
    {/if}
  </div>

  {#if loading}<p class="state muted" role="status">{loading}</p>{/if}
  {#if gErr}<p class="state error-line" role="alert">{gErr}</p>{/if}

  <p class="legend muted" title={legend}>{legend}</p>
</div>

<style>
  .cv {
    container-type: inline-size;
    position: relative;
    flex: 1;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
    background: var(--void);
  }
  canvas {
    position: absolute;
    inset: 0;
    display: block;
    touch-action: none;
  }
  canvas:focus-visible {
    outline: 1px solid #4a4a4a;
    outline-offset: -1px;
  }
  .tools {
    position: absolute;
    top: 10px;
    left: 10px;
    right: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: none;
  }
  .tools > * {
    pointer-events: auto;
  }
  .tools .btn {
    background: rgba(17, 17, 17, 0.92);
  }
  .tools .btn:hover:not(:disabled) {
    background: var(--raise);
  }
  .pair {
    display: flex;
    gap: 2px;
  }
  .upbtn {
    display: none;
  }
  .crumbs {
    display: flex;
    align-items: center;
    min-width: 0;
    height: var(--ctl);
    padding: 0 4px;
    background: rgba(17, 17, 17, 0.92);
    border: 1px solid var(--line);
    border-radius: 2px;
    font-size: 13px;
  }
  .crumb {
    min-width: 0;
    max-width: 26ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 0 6px;
    line-height: calc(var(--ctl) - 2px);
    color: var(--muted);
    background: none;
    border: 0;
    font: inherit;
  }
  button.crumb {
    cursor: pointer;
  }
  button.crumb:hover {
    color: var(--white);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  button.crumb:focus-visible {
    outline: 1px solid #6a6a6a;
    outline-offset: -2px;
  }
  .crumb.here {
    color: var(--white);
    font-weight: 500;
    flex-shrink: 1;
  }
  .sep {
    color: #5a5a5a;
    flex-shrink: 0;
  }
  /* a narrow canvas keeps the toolbar on one line: icons only, names in the tooltips, and only the place shown */
  @container (max-width: 560px) {
    .tools .t {
      display: none;
    }
    .tools .btn {
      width: var(--ctl);
      padding: 0;
    }
    .upbtn {
      display: inline-flex;
    }
    .crumbs > :not(:last-child) {
      display: none;
    }
  }
  .gap {
    flex: 1;
    pointer-events: none;
  }
  .state {
    position: absolute;
    top: 48px;
    left: 50%;
    transform: translateX(-50%);
    max-width: min(520px, calc(100% - 24px));
    padding: 4px 10px;
    background: rgba(17, 17, 17, 0.92);
    border: 1px solid var(--line);
    border-radius: 2px;
  }
  .state.error-line {
    border-color: var(--clay);
    background: #1a0d09;
  }
  .legend {
    position: absolute;
    left: 12px;
    right: 12px;
    bottom: 8px;
    font-size: 12px;
    pointer-events: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
