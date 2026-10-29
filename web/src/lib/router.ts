import { useEffect, useState } from 'react';

export interface Route {
  path: string[]; // e.g. ['console', 'A5-P1-s0-...']
  query: URLSearchParams;
}

function parse(): Route {
  const raw = window.location.hash.replace(/^#/, '') || '/';
  const [p, q = ''] = raw.split('?');
  return { path: p.split('/').filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q) };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return r;
}

/** Hash href that keeps `?live=1` (it lives in the real query string, so plain hashes keep it). */
export const href = (path: string) => `#${path}`;
