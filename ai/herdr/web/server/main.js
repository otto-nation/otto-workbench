/**
 * PR board server entry (spec § 8.2): `bun ai/herdr/web/server/main.js`. The only module that
 * touches Bun. Reads config, wires exec → board → app, serves on the loopback host, stops on
 * SIGTERM/SIGINT.
 */
import { mkdir, readFile } from 'node:fs/promises';
import { loadConfig, ConfigError } from './config.js';
import { makeExec, makeReadTextFile } from './exec.js';
import { createBoard, createPoller } from './board.js';
import { createHub, changeFeed } from './sse.js';
import { createApp } from './app.js';
import { createFiles } from './files.js';
import { loadStatics } from './static.js';
import { fill } from './pages.js';
import { checkTools } from '../../app/mods/_lib/board_data.js';
import * as tokens from '../../app/mods/_lib/tokens.js';
import { BRAND } from '../../app/mods/_lib/brand.generated.js';
import { mediaQueries, themeCss, tokensCss } from '../lib/css.js';

const HERDR = new URL('../../', import.meta.url);
const RELEASE_ROOT = new URL('../../../../', import.meta.url);
// Bun closes a connection idle this long; SSE pings every 15 s.
const IDLE_TIMEOUT_S = 60;

let config;
try {
  config = loadConfig(process.env);
} catch (err) {
  if (err instanceof ConfigError) {
    console.error(`pr-board: ${err.message}`);
    process.exit(2);
  }
  throw err;
}

// A source checkout has no VERSION; a release tree has one (bin/pr-board-release).
const version = (await readFile(new URL('VERSION', RELEASE_ROOT), 'utf8').catch(() => 'dev')).trim() || 'dev';
await mkdir(config.stateDir, { recursive: true, mode: 0o700 });

const text = (rel) => readFile(new URL(rel, HERDR), 'utf8');
const theme = async (name) => JSON.parse(await text(`app/themes/${name}.generated.json`));
const media = mediaQueries(tokens, BRAND);
const themeSheet = themeCss(await theme('otto-light'), await theme('otto-dark'));
const tokenSheet = tokensCss(tokens);
const pages = {
  index: fill(await text('web/public/index.html'), {
    name: config.name, version, compactMedia: media.compact, wrapMedia: media.wrap, wideMedia: media.wide,
  }),
  login: ((tpl) => (message) => fill(tpl, { name: config.name, message }))(await text('web/public/login.html')),
  // The sign-in page has no session, so its one stylesheet carries the theme and token variables too.
  loginCss: `${themeSheet}${tokenSheet}${await text('web/public/login.css')}`,
  themeCss: themeSheet,
  tokensCss: tokenSheet,
};

const exec = makeExec({ hcBin: config.hcBin, env: process.env, maxProcs: config.maxProcs });
const hub = createHub();
const feed = changeFeed(hub);
const board = createBoard({ exec, name: config.name, version, onChange: feed });
const poller = createPoller({ board, hub, fullMs: config.fullMs, listingMs: config.listingMs });
const app = createApp({
  config, board, hub, version, pages,
  files: createFiles({ exec }),
  readTextFile: makeReadTextFile({ exec, rootFor: (on) => board.reviewsRootFor(on) }),
  statics: await loadStatics(HERDR),
  onSubscribe: () => poller.wake(),
});

const server = Bun.serve({ hostname: config.host, port: config.port, idleTimeout: IDLE_TIMEOUT_S, development: false, fetch: (req) => app.fetch(req) });
console.log(`pr-board ${version} listening on http://${config.host}:${server.port}`);
poller.start();

checkTools(exec, { cwd: null, on: null }).then((r) => {
  if (!r.ok) console.error(`pr-board: tool check failed: ${r.error.message}`);
  else for (const [tool, have] of Object.entries(r.have)) if (!have) console.error(`pr-board: ${tool} is not on PATH`);
});

const stop = () => {
  poller.stop();
  server.stop(true);
  process.exit(0);
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
