import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

const site = JSON.parse(readFileSync(new URL('./site.config.json', import.meta.url), 'utf8')) as {
  REPO_URL: string;
  SITE_URL: string;
};

export default defineConfig({
  base: './',
  plugins: [
    react(),
    {
      name: 'site-config-html',
      transformIndexHtml: (html) => html.replaceAll('__SITE_URL__', site.SITE_URL),
    },
  ],
  define: {
    __REPO_URL__: JSON.stringify(site.REPO_URL),
    __SITE_URL__: JSON.stringify(site.SITE_URL),
  },
  build: {
    outDir: '../docs',
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 700,
    assetsInlineLimit: 0,
  },
  server: { port: 5173 },
});
