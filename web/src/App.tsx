import { useEffect } from 'react';
import { useRoute } from './lib/router';
import { Landing } from './pages/Landing';
import { ConsolePage } from './pages/ConsolePage';
import { ResultsPage } from './pages/Results';
import { RunsPage } from './pages/Runs';
import { MethodPage } from './pages/Method';
import { NotFound } from './pages/NotFound';

const TITLES: Record<string, string> = {
  '': 'fortyshots — an offline AI lab',
  console: 'Console · fortyshots',
  results: 'Results · fortyshots',
  runs: 'Runs · fortyshots',
  method: 'Method · fortyshots',
};

export function App() {
  const route = useRoute();
  const page = route.path[0] ?? '';
  const dark = page === 'console';

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0d0d0d' : '#fafafa');
    document.title = TITLES[page] ?? 'fortyshots';
  }, [dark, page]);

  useEffect(() => {
    if (page !== 'console') window.scrollTo(0, 0);
  }, [page, route.path[1]]);

  switch (page) {
    case '':
      return <Landing />;
    case 'console':
      return <ConsolePage runId={route.path[1] ?? null} query={route.query} />;
    case 'results':
      return <ResultsPage />;
    case 'runs':
      return <RunsPage />;
    case 'method':
      return <MethodPage />;
    default:
      return <NotFound />;
  }
}
