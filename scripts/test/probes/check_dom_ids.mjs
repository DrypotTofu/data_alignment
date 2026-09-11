/* Static check: every $('id') / getElementById('id') used by the frontend
 * exists in index.html (catches picker/dialog id typos without a browser).
 *
 * Usage:  node scripts/test/probes/check_dom_ids.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, '..', '..', '..', 'src', 'vlgp', 'web', 'static');
const html = readFileSync(resolve(staticDir, 'index.html'), 'utf8');
const js = readFileSync(resolve(staticDir, 'app.js'), 'utf8');

const htmlIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const used = new Set();
for (const m of js.matchAll(/\$\('([^']+)'\)/g)) used.add(m[1]);
for (const m of js.matchAll(/getElementById\('([^']+)'\)/g)) used.add(m[1]);

const missing = [...used].filter((id) => !htmlIds.has(id)).sort();
const unused = [...htmlIds].filter((id) => !used.has(id)).sort();

console.log(`html ids: ${htmlIds.size}   js lookups: ${used.size}`);
if (missing.length) console.log('MISSING in index.html:', missing.join(', '));
if (unused.length) console.log('(note) html ids never looked up:', unused.join(', '));
if (missing.length) process.exit(1);
console.log('DOM id check OK');
