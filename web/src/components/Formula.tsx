import { DASH } from '../lib/format';

/** Chemical formula with real subscripts (Geist Mono has no subscript digits, so no Unicode ₀–₉). */
export function Formula({ f }: { f: string | null | undefined }) {
  if (!f) return <span className="faint">{DASH}</span>;
  const parts = f.split(/(\d+)/g).filter(Boolean);
  return (
    <span className="formula-txt" aria-label={f}>
      {parts.map((p, i) => (/^\d+$/.test(p) ? <sub key={i}>{p}</sub> : <span key={i}>{p}</span>))}
    </span>
  );
}
