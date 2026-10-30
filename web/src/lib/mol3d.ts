// Lazy 3Dmol loader shared by the console viewer and the landing hero (its own chunk; never in the main bundle).
let lib: Promise<any> | null = null;

export function load3Dmol(): Promise<any> {
  lib ??= import('3dmol/build/3Dmol-min.js').then((m: any) => {
    const w = window as any;
    if (m?.createViewer) return m;
    if (m?.default?.createViewer) return m.default;
    if (w.$3Dmol?.createViewer) return w.$3Dmol;
    throw new Error('3Dmol did not load');
  });
  return lib;
}

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

export const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export const cssVar = (el: Element, name: string, fallback: string) => getComputedStyle(el).getPropertyValue(name).trim() || fallback;
