import { chromium } from 'playwright';
import QRCode from 'qrcode';
import { PNG } from 'pngjs';
import { writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
await mkdir('test-results', { recursive: true });
const text = 'specter synthetic webcam qr';
const png = PNG.sync.read(await QRCode.toBuffer(text, { width: 320, margin: 4 }));
const width = png.width, height = png.height;
if (width % 2 || height % 2) throw new Error('QR video requires even dimensions');
const y = Buffer.alloc(width * height);
const u = Buffer.alloc(width * height / 4, 128);
const v = Buffer.alloc(width * height / 4, 128);
for (let i = 0; i < width * height; i++) y[i] = png.data[i * 4] > 127 ? 235 : 16;
const frame = Buffer.concat([Buffer.from('FRAME\n'), y, u, v]);
const header = Buffer.from(`YUV4MPEG2 W${width} H${height} F5:1 Ip A1:1 C420jpeg\n`);
const videoPath = resolve('test-results/fake-qr.y4m');
await writeFile(videoPath, Buffer.concat([header, ...Array(12).fill(frame)]));

const browser = await chromium.launch({
  ...(process.env.CI ? {} : { channel: 'chrome' }), headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    `--use-file-for-fake-video-capture=${videoPath}`],
});
const context = await browser.newContext({ permissions: ['camera'] });
const page = await context.newPage();
await page.addInitScript(() => {
  const original = Worker.prototype.postMessage;
  Worker.prototype.postMessage = function (message, ...args) {
    if (message?.type === 'qr') window.__qrSends = (window.__qrSends || 0) + 1;
    return original.call(this, message, ...args);
  };
});
await page.goto(new URL('?probe=qr', base).href);
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await page.waitForFunction(() => document.querySelector('#debug-log').textContent.includes('QR_PROBE_HEX '),
  null, { timeout: 20000 });
const debug = await page.locator('#debug-log').textContent();
if (!debug.includes('Specter scanner active') || !debug.includes('Browser camera opened locally')) {
  throw new Error(`Firmware scan did not open the in-screen camera: ${debug}`);
}
const firmwareQrHex = debug.match(/QR_PROBE_HEX ([a-f0-9]+)/)?.[1];
if (firmwareQrHex !== Buffer.from(text).toString('hex')) {
  throw new Error('Webcam QR did not reach Specter QRHost byte-for-byte');
}
await page.waitForFunction(() => window.__qrSends >= 1, null, { timeout: 5000 });
if (await page.evaluate(() => window.__qrSends) > 10) throw new Error('Repeated QR frames flooded the worker');
await page.locator('#camera-toggle').click();
await page.locator('#camera-toggle').getByText('Hide backup preview').waitFor();
await page.locator('#camera-preview').waitFor({ state: 'visible' });
await page.locator('#camera-toggle').click();
await page.locator('#camera-state').getByText('Camera off').waitFor();
await page.goto(base);
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await page.evaluate(() => {
  const decode = window.jsQR;
  window.__decodeCount = 0;
  window.jsQR = (...args) => { window.__decodeCount++; return decode(...args); };
});
await page.locator('#camera-toggle').click();
await page.locator('#camera-state').getByText('Camera active', { exact: false }).waitFor();
await page.waitForTimeout(500);
if (await page.evaluate(() => window.__decodeCount) !== 0) {
  throw new Error('Backup camera decoded QR frames while Specter scanner was inactive');
}
const previousCanvas = await page.locator('#screen').elementHandle();
await page.locator('#restart-btn').click();
await page.waitForFunction(previous => document.querySelector('#screen') !== previous,
  previousCanvas, { timeout: 10000 });
await page.locator('#st').getByText('Running locally').waitFor({ timeout: 45000 });
await previousCanvas.dispose();
await page.locator('#camera-state').getByText('Camera off').waitFor();
const denied = await browser.newPage();
await denied.addInitScript(() => Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: { getUserMedia: () => Promise.reject(new DOMException('Denied', 'NotAllowedError')) },
}));
await denied.goto(new URL('?probe=qr', base).href);
await denied.locator('#camera-screen').waitFor({ state: 'visible', timeout: 15000 });
await denied.locator('#camera-state').getByText('Camera permission denied').waitFor();
await denied.locator('#camera-screen-start').waitFor({ state: 'visible' });
await denied.close();
console.log(JSON.stringify({ result: 'pass', camera: 'fake webcam to browser decoder to Specter QRHost',
  backupPreview: 'no idle QR decoding; restart closes camera',
  payload: text }, null, 2));
await browser.close();
