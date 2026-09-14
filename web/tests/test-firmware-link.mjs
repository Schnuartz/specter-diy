import { chromium } from 'playwright';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const pointer = await (await page.request.get(new URL('browser/current.json', base).href)).json();
  const manifest = await (await page.request.get(new URL(`${pointer.build}build-info.json`, base).href)).json();
  const buildRepository = manifest.platform_repository || manifest.repository;
  const firmwareUrl = `https://github.com/${buildRepository}/actions/runs/123/artifacts/456`;
  const metadata = {
    source_repository: manifest.repository,
    source_commit: manifest.commit,
    build_repository: buildRepository,
    firmware_url: firmwareUrl,
  };

  await page.route('**/firmware-link.json', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify(metadata),
  }));
  await page.goto(base);
  await page.locator('#firmware-download-row').waitFor({ state: 'visible', timeout: 15000 });
  if (await page.locator('#firmware-download').getAttribute('href') !== firmwareUrl) {
    throw new Error('Preview firmware link does not point to the matching artifact');
  }
  await page.close();

  const wrongPage = await browser.newPage();
  await wrongPage.route('**/firmware-link.json', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...metadata, source_commit: '0'.repeat(40) }),
  }));
  await wrongPage.goto(base);
  await wrongPage.waitForFunction(
    sha => document.querySelector('#build-link')?.textContent === sha,
    manifest.commit.slice(0, 12),
  );
  if (await wrongPage.locator('#firmware-download-row').isVisible()) {
    throw new Error('Firmware link for another source commit was displayed');
  }
  await wrongPage.close();
  console.log('Matching firmware artifact link visible; mismatched source rejected');
} finally {
  await browser.close();
}
