import { chromium, devices } from 'playwright';
import assert from 'node:assert/strict';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765';
const browser = await chromium.launch(process.env.TEST_BRAVE
  ? { executablePath: 'C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe', headless: true }
  : { channel: 'chrome', headless: true });
const results = [];
async function scenario(name, setup, expected = 'running', extra) {
  const context = await browser.newContext({ ...devices['Pixel 5'], viewport: { width: 360, height: 740 }, deviceScaleFactor: 3 });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const Native = Worker;
    window.workerAudit = [];
    window.oldCallbacks = [];
    window.startupTimeouts = [];
    const nativeTimeout = window.setTimeout;
    window.setTimeout = (callback, delay, ...args) => {
      if (delay === 60000) window.startupTimeouts.push(callback);
      return nativeTimeout(callback, delay, ...args);
    };
    window.Worker = class extends Native {
      constructor(url, options) { super(url, options); window.workerAudit.push({ url: String(url), terminated: false }); this.audit = window.workerAudit.at(-1); }
      set onmessage(fn) { window.oldCallbacks.push(fn); super.onmessage = fn; }
      terminate() { this.audit.terminated = true; super.terminate(); }
    };
  });
  try {
    await setup?.(page, context);
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.locator('#st').getByText(expected === 'running' ? 'Running locally' : 'Simulator error', { exact: true }).waitFor({ timeout: 70000 });
    const log = await page.locator('#debug-log').textContent();
    assert.match(log, /userAgent.*Android/);
    assert.match(log, /Firmware: .*build: .*worker:/);
    const audit = await page.evaluate(() => window.workerAudit);
    assert.ok(audit.length <= 2, `Unbounded retries: ${audit.length}`);
    for (const entry of audit) assert.match(entry.url, /\?v=[a-f0-9]{16}&worker=/);
    if (audit.length === 2) assert.equal(audit[0].terminated, true);
    if (expected !== 'running') {
      assert.match(await page.locator('[data-loading-title]').textContent(), new RegExp(expected));
      assert.match(await page.locator('[data-loading-label]').textContent(), /Phase: .*Display: .*Worker:/);
      assert.match(await page.locator('[data-loading-timer]').textContent(), /Elapsed \d+\.\d+ s/);
      await page.locator('[data-loading-details]').click();
      assert.equal(await page.locator('#technical-details').evaluate(el => el.open), true);
      assert.ok(await page.locator('[data-loading-actions] a[href*="legacy"]').count());
    } else {
      for (const marker of ['wasm-ready', 'SPECTER_BROWSER_BOOT', 'SPECTER_IMPORTS_DONE', 'SPECTER_MAIN_IMPORTED', 'running']) assert.ok(log.includes(marker), marker);
      assert.match(log, /asset-response.*micropython.wasm.*status":200/);
    }
    await extra?.(page, log, audit);
    assert.deepEqual(errors, []);
    results.push({ name, result: 'pass', attempts: audit.length });
    console.log(JSON.stringify(results.at(-1)));
  } finally { await context.close(); }
}
const fault = type => async page => {
  let count = 0;
  await page.route('**/browser/runtime-worker.js*', async route => {
    if (++count > 1) return route.continue();
    await route.fulfill({ headers: { 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin' }, contentType: 'text/javascript', body: `onmessage = () => postMessage(${JSON.stringify({ type, name: 'RangeError', message: 'injected failure', stack: 'RangeError: injected failure\n at injected-runtime:42' })});` });
  });
};
try {
  await scenario('Android OffscreenCanvas touch', null, 'running', async page => {
    const canvas = page.locator('#screen');
    const before = await canvas.screenshot();
    const box = await canvas.boundingBox();
    await page.touchscreen.tap(box.x + box.width * .17, box.y + box.height * .35);
    await page.waitForTimeout(700);
    assert.ok(!before.equals(await canvas.screenshot()));
  });
  await scenario('Android without OffscreenCanvas', page => page.addInitScript(() => {
    Object.defineProperty(HTMLCanvasElement.prototype, 'transferControlToOffscreen', { value: undefined });
    window.OffscreenCanvas = undefined;
  }), 'running', async (page, log) => assert.match(log, /Canvas-Pixelbridge/));
  await scenario('worker-error recovery and stale callbacks', fault('worker-error'), 'running', async (page, log, audit) => {
    assert.equal(audit.length, 2);
    assert.match(log, /injected-runtime:42/);
    await page.evaluate(() => {
      window.oldCallbacks[0]({ data: { type: 'abort', message: 'stale abort' } });
      window.startupTimeouts[0]();
    });
    await page.waitForTimeout(600);
    assert.equal(await page.locator('#st').textContent(), 'Running locally');
    assert.equal(await page.evaluate(() => window.workerAudit.length), 2);
  });
  await scenario('WASM abort recovery', fault('abort'));
  await scenario('worker onmessageerror recovery', page => page.addInitScript(() => {
    const Native = Worker;
    let first = true;
    window.Worker = class extends Native {
      set onmessageerror(fn) {
        super.onmessageerror = fn;
        if (first) { first = false; setTimeout(() => fn(new MessageEvent('messageerror')), 10); }
      }
    };
  }));
  await scenario('WASM RuntimeError and rejection stack', async page => {
    let count = 0;
    await page.route('**/browser/runtime-worker.js*', async route => {
      if (++count > 1) return route.continue();
      const response = await route.fetch();
      await route.fulfill({ response, body: `${await response.text()}\nonmessage = () => { Promise.reject(new WebAssembly.RuntimeError('injected unreachable')); };` });
    });
  }, 'running', async (page, log) => assert.match(log, /RuntimeError: injected unreachable/));
  await scenario('Canvas transfer DataCloneError', page => page.addInitScript(() => {
    HTMLCanvasElement.prototype.transferControlToOffscreen = () => { throw new DOMException('injected canvas transfer', 'DataCloneError'); };
  }));
  await scenario('missing WASM', page => page.route('**/micropython.wasm*', route => route.fulfill({ status: 404, body: 'missing' })), 'HTTP 404');
  await scenario('missing runtime JS', page => page.route('**/micropython.js*', route => route.fulfill({ status: 404, body: 'missing' })), 'HTTP 404');
  await scenario('missing runtime data', page => page.route('**/micropython.data*', route => route.fulfill({ status: 503, body: 'unavailable' })), 'HTTP 503');
  await scenario('wrong WASM MIME uses real ArrayBuffer fallback', page => page.route('**/micropython.wasm*', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, contentType: 'application/octet-stream' });
  }), 'running', async (page, log) => assert.match(log, /mime-warning/));
  await scenario('worker load failure and manual restart', async page => {
    await page.route('**/browser/runtime-worker.js*', route => route.abort());
  }, 'WorkerError', async page => {
    await page.unroute('**/browser/runtime-worker.js*');
    await page.locator('[data-loading-retry]').click();
    await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
  });
  await scenario('low memory RangeError bounded failure', page => page.route('**/browser/runtime-worker.js*', route => route.fulfill({ headers: { 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin' }, contentType: 'text/javascript', body: 'onmessage = () => { throw new RangeError("WebAssembly.Memory: simulated allocation failure"); };' })), 'WorkerError');
  await scenario('slow network and CPU', async (page, context) => {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: 750000, uploadThroughput: 250000 });
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  });
} finally { await browser.close(); }
console.log(JSON.stringify({ results }, null, 2));
