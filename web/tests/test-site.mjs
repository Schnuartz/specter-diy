import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import { readFile, mkdir } from 'node:fs/promises';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch(process.env.CI ? { headless: true } : { channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 850, height: 1000 }, acceptDownloads: true });
const requests = [];
const errors = [];
page.on('request', request => requests.push(request.url()));
page.on('pageerror', error => errors.push(error.message));
await page.goto(base, { waitUntil: 'domcontentloaded' });
try {
  await page.locator('#st').getByText('Running locally').waitFor({ timeout: 75000 });
} catch (error) {
  console.error(JSON.stringify({
    status: await page.locator('#st').textContent(),
    loadingTitle: await page.locator('[data-loading-title]').textContent().catch(() => null),
    loadingLabel: await page.locator('[data-loading-label]').textContent().catch(() => null),
    diagnostics: await page.locator('#debug-log').textContent().catch(() => null),
    pageErrors: errors,
  }, null, 2));
  throw error;
}
if (!await page.locator('.phone-mockup').evaluate(img => img.complete && img.naturalWidth > 0)) {
  throw new Error('Specter Shield Metal device image did not load');
}
const isolated = await page.evaluate(() => crossOriginIsolated);
// GitHub Pages cannot set COOP/COEP; this build does not require SharedArrayBuffer.
const canvas = page.locator('#screen');
const before = await canvas.screenshot({ path: 'test-results/specter-screen.png' });
const png = PNG.sync.read(before);
const colors = new Set();
for (let i = 0; i < png.data.length; i += 4) {
  colors.add(`${png.data[i]},${png.data[i + 1]},${png.data[i + 2]}`);
}
if (colors.size < 12) throw new Error(`Specter canvas has only ${colors.size} colors`);
const box = await canvas.boundingBox();
await page.mouse.click(box.x + box.width * 0.17, box.y + box.height * 0.35);
await page.waitForTimeout(700);
const after = await canvas.screenshot();
if (before.equals(after)) throw new Error('Pointer input did not change the Specter screen');

await page.locator('#sd-toggle').click();
await page.locator('#sd-state').getByText('Inserted').waitFor();
await page.locator('#sd-picker').setInputFiles([
  { name: 'probe.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 1, 2, 255]) },
  { name: 'second.txt', mimeType: 'text/plain', buffer: Buffer.from('second file') },
]);
await page.locator('#sd-files').getByText('probe.bin', { exact: false }).waitFor();
await page.locator('#sd-files').getByText('second.txt', { exact: false }).waitFor();
await page.evaluate(() => {
  const clipboard = new DataTransfer();
  clipboard.items.add(new File(['pasted one'], 'pasted-one.txt', { type: 'text/plain' }));
  clipboard.items.add(new File(['pasted two'], 'pasted-two.txt', { type: 'text/plain' }));
  dispatchEvent(new ClipboardEvent('paste', { clipboardData: clipboard, bubbles: true, cancelable: true }));
});
await page.locator('#sd-files').getByText('pasted-one.txt', { exact: false }).waitFor();
await page.locator('#sd-files').getByText('pasted-two.txt', { exact: false }).waitFor();
const probeDownload = page.locator('#sd-files li').filter({ hasText: 'probe.bin' })
  .getByRole('button', { name: 'Download', exact: true });
const downloadPromise = page.waitForEvent('download');
await probeDownload.click();
const download = await downloadPromise;
if (!(await readFile(await download.path())).equals(Buffer.from([0, 1, 2, 255]))) {
  throw new Error('Virtual SD export bytes differ from imported bytes');
}

await page.locator('#restart-btn').click();
await page.locator('#st').getByText('Starting locally').waitFor({ timeout: 10000 });
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 75000 });
await page.locator('#sd-state').getByText('Inserted').waitFor();
await page.locator('#sd-files').getByText('probe.bin', { exact: false }).waitFor();
await canvas.screenshot({ path: 'test-results/specter-after-restart.png' });
if (requests.some(url => /\/api\/(allocate|heartbeat)|\/novnc\//.test(url))) {
  throw new Error('Browser mode requested legacy VNC/session infrastructure');
}
if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);

const probe = await page.evaluate(async () => {
  const { build: relativeBuild, version } = await (await fetch(new URL('browser/current.json', location.href))).json();
  const build = new URL(relativeBuild, location.href).href;
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('browser/runtime-worker.js', location.href));
    const canvas = new OffscreenCanvas(480, 800);
    const logs = [];
    let written;
    let checkingAtomicImport = false;
    const timer = setTimeout(() => { worker.terminate(); reject(new Error(logs.join('\n'))); }, 10000);
    worker.onmessage = ({ data }) => {
      if (data.type === 'log') logs.push(data.message);
      if (data.type === 'abort') { clearTimeout(timer); worker.terminate(); reject(new Error(data.message)); }
      if (data.type === 'log' && data.message === 'SD_PROBE_WRITTEN') {
        worker.postMessage({ type: 'snapshot', requestId: 1 });
      }
      if (data.type === 'snapshot' && data.requestId === 1) {
        const file = data.files.find(file => file.path === 'sd/written-by-specter.txt');
        written = file ? new TextDecoder().decode(file.bytes) : null;
        checkingAtomicImport = true;
        worker.postMessage({ type: 'state-import', files: [
          { path: 'sd/must-not-be-partial.bin', bytes: new Uint8Array([1]) },
          { path: 'invalid/outside.bin', bytes: new Uint8Array([2]) },
        ] });
      }
      if (data.type === 'operation-error' && checkingAtomicImport) {
        checkingAtomicImport = false;
        worker.postMessage({ type: 'snapshot', requestId: 2 });
      }
      if (data.type === 'snapshot' && data.requestId === 2) {
        const partial = data.files.some(file => file.path === 'sd/must-not-be-partial.bin');
        clearTimeout(timer); worker.terminate();
        resolve({ logs, written, partial });
      }
    };
    worker.onerror = error => { clearTimeout(timer); worker.terminate(); reject(new Error(error.message)); };
    worker.postMessage({ type: 'start', build, version, canvas, sdInserted: true, sdProbe: true,
      stateFiles: [{ path: 'sd/probe.bin', bytes: new Uint8Array([0, 1, 2, 255]) }] }, [canvas]);
  });
});
if (!probe.logs.includes('SD_PROBE_PRESENT True') ||
    !probe.logs.some(line => line.includes("b'\\x00\\x01\\x02\\xff'")) ||
    probe.written !== 'firmware-created file' || probe.partial) {
  throw new Error(`Specter SD platform read/write failed: ${probe.logs.join('; ')}`);
}

const crashPage = await browser.newPage();
await crashPage.route('**/browser/runtime-worker.js*', route => route.abort());
await crashPage.goto(base);
await crashPage.locator('#st').getByText('Simulator error').waitFor({ timeout: 15000 });
await crashPage.close();

const mobile = await browser.newContext({ viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const mobilePage = await mobile.newPage();
await mobilePage.goto(base);
await mobilePage.locator('#st').getByText('Running locally').waitFor({ timeout: 75000 });
const mobileCanvas = mobilePage.locator('#screen');
const mobileBefore = await mobileCanvas.screenshot();
const mobileBox = await mobileCanvas.boundingBox();
await mobilePage.touchscreen.tap(mobileBox.x + mobileBox.width * 0.17,
  mobileBox.y + mobileBox.height * 0.35);
await mobilePage.waitForTimeout(700);
if (mobileBefore.equals(await mobileCanvas.screenshot())) {
  throw new Error('Scaled mobile touch did not reach Specter');
}
await mobile.close();

const sourceLinks = await page.locator('a[href]').evaluateAll(links =>
  links.map(link => link.href.toLowerCase()));
if (await page.locator('img[alt="ClavaStack"]').count() ||
    (await page.title()).includes('ClavaStack') ||
    !sourceLinks.some(href => href === 'https://github.com/schnuartz/specter-diy' ||
      href.startsWith('https://github.com/schnuartz/specter-diy/'))) {
  throw new Error('Fork page branding or source link is incorrect');
}
console.log(JSON.stringify({ result: 'pass', canvasColors: colors.size,
  crossOriginIsolated: isolated,
  pointer: 'changed Specter screen', sd: 'multi-select/paste/export/restart/Specter platform read+write',
  mobileTouch: 'changed Specter screen', workerCrash: 'handled', branding: 'Specter DIY',
  legacyRequestsInBrowserMode: 0 }, null, 2));
await browser.close();
