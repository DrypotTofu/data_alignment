/* UI-logic regression checks for the static frontend (no browser needed).
 *
 * Tests the *shipped* source by extracting small pure helpers out of app.js and
 * evaluating them in node, plus a few structural assertions for bugs that have
 * actually happened:
 *   - session-name auto-fill: the field used to be filled only when empty, so
 *     after a recording it kept proposing the already-used old timestamp
 *   - the acquisition panel must treat a finished run as idle (last_run), not as
 *     the current state
 *   - the removed `acqRefreshed` one-shot flag must not come back
 *
 * Usage:  node scripts/test/probes/check_ui_logic.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..', '..');
const app = readFileSync(resolve(root, 'src/vlgp/web/static/app.js'), 'utf8');

let ok = true;
function check(label, cond, extra = '') {
  ok = ok && !!cond;
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${extra ? ' — ' + extra : ''}`);
}

// ---- extract and test sessionNameFor() ------------------------------------
const m = app.match(/function sessionNameFor\([\s\S]*?\n}/);
check('sessionNameFor() found in app.js', !!m);
let sessionNameFor = () => null;
if (m) {
  // eslint-disable-next-line no-eval
  sessionNameFor = eval(`(${m[0]})`);
  const cases = [
    ['empty field takes the suggestion', sessionNameFor('', '', 'test_1'), 'test_1'],
    ['untouched auto value is refreshed',
      sessionNameFor('test_1', 'test_1', 'test_2'), 'test_2'],
    ['operator-typed name is kept',
      sessionNameFor('my_session', 'test_1', 'test_2'), 'my_session'],
    ['whitespace-only counts as empty',
      sessionNameFor('   ', 'test_1', 'test_2'), 'test_2'],
    ['no suggestion keeps the field',
      sessionNameFor('my_session', '', ''), 'my_session'],
  ];
  for (const [label, got, want] of cases) check(label, got === want, `${got} (want ${want})`);
}

// the dialog-open path must use it (not the old "only when empty" guard)
check('loadAcqConfigs uses sessionNameFor',
  /field\.value = sessionNameFor\(field\.value, state\.acqSuggestedName, suggested\)/
    .test(app));
check('old "only when empty" guard is gone',
  !/\$\(\'acq-name\'\)\.value\)\s*\$\(\'acq-name\'\)\.value = data\.suggested_name/
    .test(app));

// ---- acquisition panel state semantics ------------------------------------
check('frontend reads the last-run summary from the status payload',
  /\bst\.last_run\b/.test(app) && /last\.session/.test(app));
check('setAcqState maps every live state',
  /\bstarting\b/.test(app) && /recording/.test(app) && /stopping/.test(app));
check('removed acqRefreshed flag did not come back',
  !app.includes('acqRefreshed'));
check('edge-triggered refresh present', app.includes('acqWasRecording'));

// ---- pipeline dialog ------------------------------------------------------
check('pipeline dialog open/close helpers exist',
  app.includes('function openPipeDialog') && app.includes('function closePipeDialog'));
check('pipeline badge + message helpers exist',
  app.includes('function setPipeBadge') && app.includes('function setPipeMsg'));
check('pipeline result reported outside the dialog when closed',
  /state\.pipeOpen/.test(app));

console.log('RESULT:', ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);
