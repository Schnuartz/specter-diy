import { chromium } from 'playwright';
import QRCode from 'qrcode';
import { PNG } from 'pngjs';
import { writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765';
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
await page.goto(`${base}/?probe=qr`);
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
await page.locator('#camera-panel').waitFor({ state: 'hidden' });
const denied = await browser.newPage();
await denied.addInitScript(() => Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: { getUserMedia: () => Promise.reject(new DOMException('Denied', 'NotAllowedError')) },
}));
await denied.goto(`${base}/?probe=qr`);
await denied.locator('#camera-screen').waitFor({ state: 'visible', timeout: 15000 });
await denied.waitForFunction(() => document.querySelector('#debug-log').textContent.includes('Camera permission denied'));
await denied.locator('#camera-screen-start').waitFor({ state: 'visible' });
await denied.close();
console.log(JSON.stringify({ result: 'pass', camera: 'fake webcam to browser decoder to Specter QRHost',
  payload: text }, null, 2));
await browser.close();
