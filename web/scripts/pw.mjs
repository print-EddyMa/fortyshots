// Resolve Playwright from the project, NODE_PATH, or the global npm root (it is not a project dependency).
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
function load() {
  try {
    return require('playwright');
  } catch {
    const g = execSync('npm root -g').toString().trim();
    return require(join(g, 'playwright'));
  }
}
export const { chromium } = load();
