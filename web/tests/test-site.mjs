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
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
const previewPointer = await (await page.request.get(new URL('browser/current.json', base).href)).json();
const previewManifest = await (await page.request.get(new URL(`${previewPointer.build}build-info.json`, base).href)).json();
const sourceLink = page.locator('#source-commit-link');
const expectedCommitUrl = `https://github.com/${previewManifest.repository}/commit/${previewManifest.commit}`;
const expectedIdentity = [previewManifest.firmware_version,
  Number.isInteger(previewManifest.pr_number) ? `PR #${previewManifest.pr_number}` : null,
  `Commit ${previewManifest.commit.slice(0, 7)}`].filter(Boolean).join(' · ');
if (await sourceLink.textContent() !== `GitHub · ${expectedIdentity}` ||
    await sourceLink.getAttribute('href') !== expectedCommitUrl) {
  throw new Error('PR preview does not link to its exact firmware commit below Restart');
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
if (await page.locator('#sd-hint').textContent() !== 'Click to remove' ||
    await page.locator('#sd-toggle').getAttribute('aria-pressed') !== 'true') {
  throw new Error('SD card image did not switch to the inserted state');
}
await page.locator('#sd-picker').setInputFiles({
  name: 'probe.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 1, 2, 255]),
});
await page.locator('#sd-files').getByText('probe.bin', { exact: false }).waitFor();
const downloadPromise = page.waitForEvent('download');
await page.locator('#sd-files button').first().click();
const download = await downloadPromise;
if (!(await readFile(await download.path())).equals(Buffer.from([0, 1, 2, 255]))) {
  throw new Error('Virtual SD export bytes differ from imported bytes');
}
await page.locator('#sd-toggle').click();
await page.locator('#sd-state').getByText('Ejected').waitFor();
if (await page.locator('#sd-hint').textContent() !== 'Click to insert' ||
    await page.locator('#sd-toggle').getAttribute('aria-pressed') !== 'false') {
  throw new Error('SD card image did not switch to the ejected state');
}
await page.locator('#sd-toggle').click();
await page.locator('#sd-state').getByText('Inserted').waitFor();

const previousCanvas = await canvas.elementHandle();
await page.locator('#restart-btn').click();
await page.waitForFunction(previous => document.querySelector('#screen') !== previous,
  previousCanvas, { timeout: 10000 });
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await previousCanvas.dispose();
if (await sourceLink.getAttribute('href') !== expectedCommitUrl) {
  throw new Error('Source commit link changed after local restart');
}
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
    const timer = setTimeout(() => { worker.terminate(); reject(new Error(logs.join('\n'))); }, 10000);
    worker.onmessage = ({ data }) => {
      if (data.type === 'log') logs.push(data.message);
      if (data.type === 'abort') { clearTimeout(timer); worker.terminate(); reject(new Error(data.message)); }
      if (data.type === 'log' && data.message === 'SD_PROBE_WRITTEN') {
        worker.postMessage({ type: 'snapshot', requestId: 1 });
      }
      if (data.type === 'snapshot') {
        const file = data.files.find(file => file.path === 'sd/written-by-specter.txt');
        clearTimeout(timer); worker.terminate();
        resolve({ logs, written: file ? new TextDecoder().decode(file.bytes) : null });
      }
    };
    worker.onerror = error => { clearTimeout(timer); worker.terminate(); reject(new Error(error.message)); };
    worker.postMessage({ type: 'start', build, version, canvas, sdInserted: true, sdProbe: true,
      stateFiles: [{ path: 'sd/probe.bin', bytes: new Uint8Array([0, 1, 2, 255]) }] }, [canvas]);
  });
});
if (!probe.logs.includes('SD_PROBE_PRESENT True') ||
    !probe.logs.some(line => line.includes("b'\\x00\\x01\\x02\\xff'")) ||
    probe.written !== 'firmware-created file') {
  throw new Error(`Specter SD platform read/write failed: ${probe.logs.join('; ')}`);
}

const deniedPage = await browser.newPage();
await deniedPage.addInitScript(() => Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: {
    getUserMedia: () => Promise.reject(new DOMException('Denied for test', 'NotAllowedError')),
    enumerateDevices: () => Promise.resolve([]),
  },
}));
await deniedPage.goto(base);
await deniedPage.locator('#camera-toggle').click();
await deniedPage.locator('#camera-state').getByText('Camera permission denied').waitFor();
await deniedPage.close();

const missingCameraPage = await browser.newPage();
await missingCameraPage.addInitScript(() => Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: {
    getUserMedia: () => Promise.reject(new DOMException('No camera found', 'NotFoundError')),
    enumerateDevices: () => Promise.resolve([]),
  },
}));
await missingCameraPage.goto(base);
await missingCameraPage.locator('#camera-toggle').click();
await missingCameraPage.locator('#camera-state').getByText('Camera unavailable: No camera found').waitFor();
await missingCameraPage.close();

const crashPage = await browser.newPage();
await crashPage.route('**/browser/runtime-worker.js*', route => route.abort());
await crashPage.goto(base);
await crashPage.locator('#st').getByText('Simulator error').waitFor({ timeout: 15000 });
if (!await crashPage.locator('[data-loading-actions]').isVisible()) throw new Error('Loading error actions are not visible');
if (!/Elapsed \d+\.\d+ s/.test(await crashPage.locator('[data-loading-timer]').textContent())) throw new Error('Loading timer is missing');
await crashPage.locator('[data-loading-details]').click();
if (!await crashPage.locator('details').evaluate(details => details.open)) throw new Error('Technical details did not open from loading error');
await crashPage.close();

const mobile = await browser.newContext({ viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const mobilePage = await mobile.newPage();
await mobilePage.goto(base);
await mobilePage.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
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

if (await page.locator('img[alt="ClavaStack"]').count() ||
    (await page.title()).includes('ClavaStack') ||
    !await page.locator('a[href="https://github.com/Schnuartz/specter-diy"]').count() ||
    await page.locator('.header-brand strong').textContent() !== 'Specter DIY' ||
    await page.locator('.header-brand small').textContent() !== 'Simulator') {
  throw new Error('Fork page branding or source link is incorrect');
}
console.log(JSON.stringify({ result: 'pass', canvasColors: colors.size,
  crossOriginIsolated: isolated,
  pointer: 'changed Specter screen', sd: 'import/export/restart/Specter platform read+write',
  mobileTouch: 'changed Specter screen', cameraDenied: 'handled', noCamera: 'handled',
  workerCrash: 'handled', branding: 'Specter DIY',
  legacyRequestsInBrowserMode: 0 }, null, 2));
await browser.close();
