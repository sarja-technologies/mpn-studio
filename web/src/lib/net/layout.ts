// Deterministic layouts for the network canvas: a packed domain map and a radial neighbourhood that grows by expansion.
// No randomness and no animation: the same data always lands in the same place.

export interface DomainCircle {
  domain: string;
  nodes: number;
  x: number;
  y: number;
  r: number;
  /** the label sits inside the circle */
  inside: boolean;
  /** the radius the circle claims including its label */
  claim: number;
}

/** Circles sized by the square root of their node count, packed greedily around the largest one. */
export function packDomains(domains: { domain: string; nodes: number }[], measure: (s: string) => number, maxR = 84, labelRoom = true, aspect = 1.5): DomainCircle[] {
  const items = [...domains].filter((d) => d.nodes > 0).sort((a, b) => b.nodes - a.nodes);
  if (!items.length) return [];
  const scale = maxR / Math.sqrt(items[0].nodes);
  const out: DomainCircle[] = [];
  for (const d of items) {
    const r = Math.max(3, Math.sqrt(d.nodes) * scale);
    const w = measure(d.domain);
    const inside = r >= 30 && w + 10 <= r * 2;
    // a circle too small for its label carries the label underneath: claim room for it
    const claim = inside || !labelRoom ? r + 5 : Math.max(r + 30, w / 2 + 8);
    out.push({ domain: d.domain, nodes: d.nodes, x: 0, y: 0, r, inside, claim });
  }
  const placed: DomainCircle[] = [];
  for (const c of out) {
    if (!placed.length) {
      placed.push(c);
      continue;
    }
    let best: { x: number; y: number; d: number } | null = null;
    for (const p of placed) {
      const dist = p.claim + c.claim + 2;
      for (let i = 0; i < 48; i++) {
        const a = (i / 48) * Math.PI * 2;
        const x = p.x + Math.cos(a) * dist;
        const y = p.y + Math.sin(a) * dist;
        let ok = true;
        for (const q of placed) {
          const dx = q.x - x, dy = q.y - y;
          if (dx * dx + dy * dy < (q.claim + c.claim) ** 2) {
            ok = false;
            break;
          }
        }
        if (!ok) continue;
        // grow in the shape of the canvas (aspect = width / height)
        const d = (x * x) / (aspect * aspect) + y * y;
        if (!best || d < best.d) best = { x, y, d };
      }
    }
    if (best) {
      c.x = best.x;
      c.y = best.y;
    }
    placed.push(c);
  }
  return placed;
}

export interface P {
  x: number;
  y: number;
}

/** Positions for `ids` around `at`: rings for a fresh neighbourhood, an arc facing away from `from` for an expansion. */
export function ring(ids: string[], at: P, from: P | null): Record<string, P> {
  const out: Record<string, P> = {};
  const n = ids.length;
  if (!n) return out;
  if (!from) {
    // fresh neighbourhood: one ring up to 14 nodes, two staggered rings beyond
    const inner = n <= 14 ? n : Math.ceil(n * 0.42);
    ids.forEach((id, i) => {
      const onInner = i < inner;
      const k = onInner ? i : i - inner;
      const m = onInner ? inner : n - inner;
      const a = (k / m) * Math.PI * 2 - Math.PI / 2 + (onInner ? 0 : Math.PI / m);
      const R = onInner ? Math.max(150, inner * 13) : Math.max(260, inner * 13 + 120);
      out[id] = { x: at.x + Math.cos(a) * R * 1.25, y: at.y + Math.sin(a) * R * 0.82 };
    });
    return out;
  }
  // expansion: rows of arcs facing away from where the node was reached from, filled inner row first
  const base = Math.atan2(at.y - from.y, at.x - from.x);
  const spread = Math.PI * 0.9;
  let done = 0, row = 0;
  while (done < n) {
    const R = 150 + row * 72;
    const cap = Math.max(3, Math.floor((R * spread) / 34));
    const take = Math.min(cap, n - done);
    const used = spread * Math.min(1, take / cap);
    for (let j = 0; j < take; j++) {
      const t = take === 1 ? 0 : j / (take - 1) - 0.5;
      const a = base + t * used + (row % 2 ? 0.08 : 0);
      out[ids[done + j]] = { x: at.x + Math.cos(a) * R, y: at.y + Math.sin(a) * R };
    }
    done += take;
    row++;
  }
  return out;
}

/** Push apart nodes that overlap. Only the `free` ones move; a fixed number of passes keeps it deterministic. */
export function separate(pos: Map<string, P>, free: Set<string>, minDist = 34, passes = 40) {
  const all = [...pos.keys()];
  for (let pass = 0; pass < passes; pass++) {
    let moved = false;
    for (const a of free) {
      const pa = pos.get(a);
      if (!pa) continue;
      for (const b of all) {
        if (a === b) continue;
        const pb = pos.get(b)!;
        let dx = pa.x - pb.x, dy = pa.y - pb.y;
        let d2 = dx * dx + dy * dy;
        if (d2 >= minDist * minDist) continue;
        if (d2 < 0.01) {
          // identical spot: nudge along a direction derived from the ids so it stays deterministic
          const h = hash(a + b);
          dx = Math.cos(h);
          dy = Math.sin(h);
          d2 = 1;
        }
        const d = Math.sqrt(d2);
        const push = (minDist - d) / (free.has(b) ? 2 : 1);
        pa.x += (dx / d) * push;
        pa.y += (dy / d) * push;
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) / 4294967296) * Math.PI * 2;
}

/**
 * Spread newly placed nodes into free space: they repel every node and are held near the node they belong to.
 * Only the `free` nodes move; a fixed number of cooling steps keeps it deterministic and quick (no animation).
 */
export function relax(pos: Map<string, P>, free: Map<string, P>, iters = 140, rest = 150) {
  const K2 = 70 * 70;
  const all = [...pos.entries()];
  for (let it = 0; it < iters; it++) {
    const step = 14 * (1 - it / iters) + 0.5;
    for (const [id, anchor] of free) {
      const p = pos.get(id);
      if (!p) continue;
      let fx = 0, fy = 0;
      for (const [oid, q] of all) {
        if (oid === id) continue;
        const dx = p.x - q.x, dy = p.y - q.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > 360000) continue;
        const f = K2 / Math.max(d2, 25);
        fx += dx * f * 0.1;
        fy += dy * f * 0.1;
      }
      const ax = anchor.x - p.x, ay = anchor.y - p.y;
      const d = Math.sqrt(ax * ax + ay * ay) || 1;
      const pull = (d - rest) * 0.12;
      fx += (ax / d) * pull;
      fy += (ay / d) * pull;
      const m = Math.sqrt(fx * fx + fy * fy);
      if (m > step) {
        fx = (fx / m) * step;
        fy = (fy / m) * step;
      }
      p.x += fx;
      p.y += fy;
    }
  }
}
