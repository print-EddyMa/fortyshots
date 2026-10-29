import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import './styles/base.css';
import './styles/site.css';
import { createRoot } from 'react-dom/client';
import { App } from './App';

createRoot(document.getElementById('root')!).render(<App />);
