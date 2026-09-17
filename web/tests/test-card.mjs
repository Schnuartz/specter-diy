import { chromium } from 'playwright';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
const browser = await chromium.launch(process.env.CI ? { headless: true } : { channel: 'chrome', headless: true });
const page = await browser.newPage();
await page.goto(base);
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await page.locator('#card-panel').waitFor({ state: 'visible' });
await page.locator('#card-panel').screenshot({ path: 'test-results/card-panel.png' });
const rows = page.locator('#card-slots > div');
await rows.nth(0).locator('.smartcard-graphic').click();
await rows.nth(0).getByText('Inserted').waitFor();
await rows.nth(1).locator('.smartcard-graphic').click();
await rows.nth(1).getByText('Inserted').waitFor();
page.once('dialog', dialog => dialog.accept());
await rows.nth(1).locator('.smartcard-graphic').click({ button: 'right' });
await rows.nth(1).locator('.smartcard-graphic strong').getByText('MemoryCard 2', { exact: true }).waitFor();
await rows.nth(0).locator('.smartcard-graphic').click();
await rows.nth(0).getByText('Inserted').waitFor();
let previousCanvas = await page.locator('#screen').elementHandle();
await page.locator('#restart-btn').click();
await page.waitForFunction(previous => document.querySelector('#screen') !== previous,
  previousCanvas, { timeout: 10000 });
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await previousCanvas.dispose();
await rows.nth(0).getByText('Inserted').waitFor();
await page.locator('#technical-details > summary').click();
previousCanvas = await page.locator('#screen').elementHandle();
await page.locator('#factory-btn').click();
await page.waitForFunction(previous => document.querySelector('#screen') !== previous,
  previousCanvas, { timeout: 10000 });
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await previousCanvas.dispose();
await rows.nth(0).getByText('Inserted').waitFor();
const logs = await page.evaluate(async () => {
  const { build: relativeBuild, version } = await (await fetch(new URL('browser/current.json', location.href))).json();
  const build = new URL(relativeBuild, location.href).href;
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('browser/runtime-worker.js', location.href));
    const canvas = new OffscreenCanvas(480, 800);
    const output = [];
    const timer = setTimeout(() => { worker.terminate(); reject(new Error(output.join('; '))); }, 20000);
    worker.onmessage = ({ data }) => {
      if (data.type === 'log' || data.type === 'debug') output.push(data.message);
      if (data.type === 'abort' || data.type === 'operation-error') {
        clearTimeout(timer); worker.terminate(); reject(new Error(data.message));
      }
      if (data.type === 'log' && data.message === 'CARD_PROBE_READY') {
        worker.postMessage({ type: 'card-insert', slot: 1 });
      }
      if (data.type === 'log' && data.message === 'CARD_PROBE_SLOT1_PASS') {
        worker.postMessage({ type: 'card-insert', slot: 2 });
      }
      if (data.type === 'log' && data.message === 'CARD_PROBE_SLOT2_PASS') {
        worker.postMessage({ type: 'card-insert', slot: 1 });
      }
      if (data.type === 'log' && data.message === 'CARD_PROBE_SLOT1_RETURN_PASS') {
        worker.postMessage({ type: 'card-reset', slot: 1 });
        worker.postMessage({ type: 'card-insert', slot: 1 });
      }
      if (data.type === 'log' && data.message === 'CARD_PROBE_PASS') {
        clearTimeout(timer); worker.terminate(); resolve(output);
      }
    };
    worker.onerror = error => { clearTimeout(timer); worker.terminate(); reject(new Error(error.message)); };
    worker.postMessage({ type: 'start', build, version, canvas, cardProbe: true }, [canvas]);
  });
});
if (!logs.includes('CARD_PROBE_PASS')) throw new Error(logs.join('; '));
console.log(JSON.stringify({ result: 'pass', card: 'Specter APDU, secure channel, PIN, isolated slots and reset' }));
await browser.close();
