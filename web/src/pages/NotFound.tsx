import { Page } from '../components/Chrome';
import { href } from '../lib/router';

export function NotFound() {
  return (
    <Page>
      <section className="wrap notfound">
        <h1 className="h-section">
          Nothing here. <span className="second">Try the console.</span>
        </h1>
        <p style={{ marginTop: 24 }}>
          <a className="link" href={href('/')}>
            Home
          </a>
        </p>
      </section>
    </Page>
  );
}
