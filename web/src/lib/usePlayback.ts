import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { timeline } from './replay';
import type { RunEvent } from './types';

export type Speed = 1 | 8 | 32;

export interface Playback {
  live: boolean;
  following: boolean;
  setFollowing: (f: boolean) => void;
  cursor: number; // number of events applied
  vt: number; // virtual replay time (ms, compressed)
  total: number;
  playing: boolean;
  speed: Speed;
  ended: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  setSpeed: (s: Speed) => void;
  seek: (ms: number) => void;
  seekIndex: (i: number) => void;
}

function upperBound(xs: number[], v: number): number {
  let lo = 0;
  let hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function usePlayback(
  events: RunEvent[],
  opts: { live?: boolean; speed?: Speed; autoplay?: boolean; startMs?: number } = {},
): Playback {
  const live = !!opts.live;
  const tl = useMemo(() => timeline(events), [events]);
  const total = tl.length ? tl[tl.length - 1] : 0;
  const [vt, setVt] = useState(opts.startMs ?? 0);
  const [playing, setPlaying] = useState(opts.autoplay ?? true);
  const [speed, setSpeed] = useState<Speed>(opts.speed ?? 8);
  const [following, setFollowing] = useState(true);
  const frozen = useRef(0);

  useEffect(() => {
    if (live || !playing || !events.length) return;
    let last = performance.now();
    const id = window.setInterval(() => {
      const now = performance.now();
      const dt = now - last;
      last = now;
      setVt((v) => {
        const nv = v + dt * speed;
        if (nv >= total) {
          setPlaying(false);
          return total;
        }
        return nv;
      });
    }, 80);
    return () => window.clearInterval(id);
  }, [live, playing, speed, total, events.length]);

  let cursor: number;
  if (live) {
    if (following) frozen.current = events.length;
    cursor = following ? events.length : frozen.current;
  } else {
    cursor = upperBound(tl, vt);
  }

  const seek = useCallback((ms: number) => setVt(Math.max(0, Math.min(total, ms))), [total]);
  const seekIndex = useCallback((i: number) => setVt(tl[Math.max(0, Math.min(tl.length - 1, i))] ?? 0), [tl]);
  const ended = !live && events.length > 0 && cursor >= events.length;

  return {
    live,
    following,
    setFollowing,
    cursor,
    vt,
    total,
    playing,
    speed,
    ended,
    play: () => {
      if (ended) setVt(0);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    toggle: () => {
      if (playing) setPlaying(false);
      else {
        if (ended) setVt(0);
        setPlaying(true);
      }
    },
    setSpeed,
    seek,
    seekIndex,
  };
}
