// Shared runtime for the explainer scenes (web/scripts/video/scenes/*.html).
//
// A scene is a pure function of time: Scene.run({ duration, build(ctx), update(t, ctx) }). Nothing depends on
// the wall clock, so a frame at t is always the same frame:
//   ?t=3.2        render the frame at 3.2 s and stop (stills for checking)
//   ?capture=1    render t = 0 and wait; the recorder calls window.__scene.seek(t) once per output frame
//   (neither)     play in real time from requestAnimationFrame, looping, for previewing in a browser
// The page is laid out for the window it is given: 1920x1080 (landscape) or 1080x1920 (portrait, the X clip).
// Style: the site's dark theme (web/src/styles/base.css): #0d0d0d, #ededed, #8b8b8b, one accent #f59e4b,
// hairline rules, Geist and Geist Mono; no gradients, glass or emoji.
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const C = {
    bg: '#0d0d0d',
    raised: '#141414',
    sunken: '#1a1a1a',
    fg: '#ededed',
    muted: '#8b8b8b',
    faint: '#858585',
    rule: '#242424',
    ruleStrong: '#333333',
    accent: '#f59e4b',
  };
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const ease = {
    linear: (k) => k,
    out: (k) => 1 - (1 - k) ** 3,
    inOut: (k) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2),
    expo: (k) => (k >= 1 ? 1 : 1 - 2 ** (-10 * k)),
    back: (k) => {
      const c = 1.4;
      return 1 + (c + 1) * (k - 1) ** 3 + c * (k - 1) ** 2;
    },
  };
  /** 0..1 progress of t through [t0, t1], eased. */
  const tw = (t, t0, t1, e = ease.inOut) => e(clamp((t - t0) / Math.max(1e-6, t1 - t0)));
  /** Fade in over [a, a+d], hold, fade out over [b-d, b] (b optional). */
  const win = (t, a, b = Infinity, d = 0.35) => Math.min(tw(t, a, a + d, ease.out), b === Infinity ? 1 : 1 - tw(t, b - d, b, ease.inOut));
  // deterministic noise for wobble
  const hash = (n) => {
    const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return s - Math.floor(s);
  };

  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'text') e.textContent = v;
      else e.setAttribute(k, v);
    }
    if (parent) parent.appendChild(e);
    return e;
  }
  function set(e, attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'text') {
        if (e.textContent !== String(v)) e.textContent = v;
      } else e.setAttribute(k, v);
    }
    return e;
  }
  /** Text helper. kind: 'sans' | 'mono'. */
  function text(parent, str, x, y, size, opts = {}) {
    const t = el(
      'text',
      {
        x,
        y,
        'font-size': size,
        fill: opts.fill ?? C.fg,
        'font-family': opts.mono ? "'Geist Mono', ui-monospace, monospace" : "Geist, system-ui, sans-serif",
        'font-weight': opts.weight ?? (opts.mono ? 400 : 500),
        'letter-spacing': opts.ls ?? (opts.mono ? '0' : `${(-0.035 * size).toFixed(2)}px`),
        'text-anchor': opts.anchor ?? 'start',
        'dominant-baseline': opts.baseline ?? 'alphabetic',
      },
      parent,
    );
    if (opts.mono) t.setAttribute('style', "font-variant-numeric: tabular-nums slashed-zero; font-feature-settings: 'zero' 1");
    t.textContent = str;
    return t;
  }
  /** A two-tone headline like the site's: first part in fg, second in the muted gray, wrapped in lines. */
  function headline(parent, lines, x, y, size, lh = 1.08) {
    const g = el('g', {}, parent);
    lines.forEach((ln, i) => {
      const t = text(g, '', x, y + i * size * lh, size);
      for (const [s, muted] of ln) {
        const sp = el('tspan', { fill: muted ? C.faint : C.fg }, t);
        sp.textContent = s;
      }
    });
    return g;
  }

  function run(scene) {
    const q = new URLSearchParams(location.search);
    const W = window.innerWidth;
    const H = window.innerHeight;
    const portrait = H > W;
    document.documentElement.style.background = C.bg;
    document.body.style.cssText = `margin:0;background:${C.bg};overflow:hidden;width:${W}px;height:${H}px`;
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, style: 'position:absolute;left:0;top:0' });
    document.body.appendChild(svg);
    const ctx = { svg, W, H, portrait, C, data: window.SCENE_DATA, layers: {} };
    // load both faces before building (layouts measure text), then wait until everything drawn has its font
    const fonts = document.fonts
      ? Promise.all(["500 40px Geist", "400 40px Geist", "400 20px 'Geist Mono'"].map((f) => document.fonts.load(f).catch(() => null)))
      : Promise.resolve();
    const ready = fonts
      .then(() => scene.build(ctx))
      .then(() => (document.fonts ? document.fonts.ready : null));
    let current = 0;
    const api = {
      duration: scene.duration,
      ready: false,
      seek(t) {
        current = t;
        scene.update(t, ctx);
        return t;
      },
    };
    window.__scene = api;
    ready.then(() => {
      api.ready = true;
      if (q.has('t')) return api.seek(Number(q.get('t')) || 0);
      if (q.has('capture')) return api.seek(0);
      const t0 = performance.now();
      const loop = (now) => {
        api.seek((((now - t0) / 1000) % (scene.duration + 1.5)));
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });
    return ctx;
  }

  window.Scene = { run, el, set, text, headline, C, clamp, lerp, ease, tw, win, hash, NS };
})();
