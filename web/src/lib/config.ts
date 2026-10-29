declare const __REPO_URL__: string;
declare const __SITE_URL__: string;

/** Public repository. Set in web/site.config.json. */
export const REPO_URL: string = __REPO_URL__;
export const SITE_URL: string = __SITE_URL__;
export const PREREG_URL = `${REPO_URL}/blob/main/PREREG.md`;

/** `?live=1` switches run data to the local live server's /api. */
export const LIVE: boolean = (() => {
  try {
    return new URLSearchParams(window.location.search).get('live') === '1';
  } catch {
    return false;
  }
})();

/** Static data lives next to the page (docs/data/...). Relative so it works under any Pages base path. */
export const DATA = './data';
