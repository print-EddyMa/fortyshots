import { useState, type ReactNode } from 'react';
import { REPO_URL } from '../lib/config';
import { href } from '../lib/router';

export function Wordmark() {
  return (
    <a href={href('/')} className="wordmark" aria-label="fortyshots, home">
      fortyshot<span className="faint-mark">s</span>
    </a>
  );
}

export function Nav({ current }: { current?: string }) {
  const item = (to: string, label: string) => (
    <a href={href(to)} className="nav-link" aria-current={current === to ? 'page' : undefined}>
      {label}
    </a>
  );
  return (
    <header className="nav">
      <div className="wrap nav-inner">
        <Wordmark />
        <nav className="nav-links" aria-label="Main">
          {item('/method', 'Method')}
          {item('/results', 'Science')}
          {item('/runs', 'Runs')}
          <a href={REPO_URL} className="nav-link nav-code">
            Code
          </a>
          <a href={href('/console')} className="btn btn-primary btn-sm">
            Open console
          </a>
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <hr className="hairline" />
        <div className="footer-top">
          <Wordmark />
          <nav className="footer-links" aria-label="Footer">
            <a className="link-quiet" href={href('/method')}>Method</a>
            <a className="link-quiet" href={href('/results')}>Science</a>
            <a className="link-quiet" href={href('/runs')}>Runs</a>
            <a className="link-quiet" href={REPO_URL}>Code</a>
            <a className="link-quiet" href="./licenses/Geist-OFL-1.1.txt">Font licenses</a>
          </nav>
        </div>
        <p className="small footer-fine">
          Built Oct 27 – Nov 4, 2026 · Not affiliated with or endorsed by Meta. Geist and Geist Mono are used under the SIL
          Open Font License 1.1.
        </p>
      </div>
    </footer>
  );
}

export function Page({ current, children }: { current?: string; children: ReactNode }) {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Nav current={current} />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <Footer />
    </>
  );
}

export function CommandPill({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="cmd">
      <span className="prompt" aria-hidden>
        $
      </span>
      <code tabIndex={0}>{cmd}</code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(cmd).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            },
            () => undefined,
          );
        }}
        aria-label="Copy command"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}

export function Prelim({ status, mockRuns, children }: { status?: string; mockRuns?: boolean; children?: ReactNode }) {
  const what =
    status === 'final'
      ? 'mock runs'
      : status === 'pilot'
        ? mockRuns
          ? 'pilot results and mock runs'
          : 'pilot results'
        : 'mock data';
  return (
    <p className="prelim" role="note">
      <span className="dot" aria-hidden />
      <span>
        Preliminary — {what}.{' '}
        {children ?? 'Numbers will be replaced by the recorded, pre-registered runs.'}
      </span>
    </p>
  );
}

export function HitWord({ hit }: { hit: boolean | null }) {
  if (hit == null) return <span className="faint">—</span>;
  return (
    <span className="hitword">
      <span className={`dot ${hit ? 'pass' : 'hollow'}`} aria-hidden />
      <span className={hit ? '' : 'faint'}>{hit ? 'hit' : 'miss'}</span>
    </span>
  );
}

export function Stat({ label, value, unit, sub }: { label: ReactNode; value: ReactNode; unit?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">
        {value}
        {unit != null && <span className="unit">{unit}</span>}
      </div>
      {sub != null && <div className="small sub">{sub}</div>}
    </div>
  );
}
