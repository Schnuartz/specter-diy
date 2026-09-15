import { chromium } from 'playwright';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
const browser = await chromium.launch(process.env.CI ? { headless: true } : { channel: 'chrome', headless: true });
try {
  for (const [repository, commit] of [
    ['Schnuartz/specter-diy', '0123456789abcdef0123456789abcdef01234567'],
    ['cryptoadvance/specter-diy', 'abcdef0123456789abcdef0123456789abcdef01'],
  ]) {
    const page = await browser.newPage({ viewport: { width: 850, height: 1000 } });
    const build = `builds/${repository}/${commit}/`;
    const version = 'fedcba9876543210';
    await page.route('**/browser/current.json', route => route.fulfill({ json: { build, version } }));
    await page.route('**/build-info.json', route => route.fulfill({ json: {
      repository, commit, artifact_set_sha256: `${version}${'0'.repeat(48)}`,
      firmware_version: 'v1.10.3', pr_number: 6,
      source_url: 'https://example.invalid/should-not-be-used',
    } }));
    await page.route('**/browser/runtime-worker.js', route => route.abort());
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    const link = page.locator('#source-commit-link');
    await link.filter({ hasText: `GitHub · v1.10.3 · PR #6 · Commit ${commit.slice(0, 7)}` }).waitFor();
    const expectedUrl = `https://github.com/${repository}/commit/${commit}`;
    if (await link.getAttribute('href') !== expectedUrl) {
      throw new Error('Preview source link does not target the exact build commit');
    }
    const buttonBox = await page.locator('#restart-btn').boundingBox();
    const linkBox = await link.boundingBox();
    if (!buttonBox || !linkBox || linkBox.y < buttonBox.y + buttonBox.height) {
      throw new Error('Preview source link is not visible below Restart');
    }
    if (!await link.evaluate(element => getComputedStyle(element).textDecorationLine.includes('underline'))) {
      throw new Error('Preview source link is not visibly styled as a link');
    }
    await page.context().route('https://github.com/**', route => route.fulfill({ body: 'GitHub link test' }));
    const popupPromise = page.waitForEvent('popup');
    await link.click();
    const popup = await popupPromise;
    await popup.waitForURL(expectedUrl);
    await popup.close();
    await page.close();
  }
  const badPage = await browser.newPage();
  const badCommit = '0123456789abcdef0123456789abcdef01234567';
  await badPage.route('**/browser/current.json', route => route.fulfill({ json: {
    build: `builds/Schnuartz/specter-diy/${badCommit}/`, version: 'fedcba9876543210',
  } }));
  await badPage.route('**/build-info.json', route => route.fulfill({ json: {
    repository: 'Schnuartz/specter-diy', commit: badCommit,
    artifact_set_sha256: '0000000000000000',
  } }));
  await badPage.goto(base, { waitUntil: 'domcontentloaded' });
  await badPage.locator('#source-commit-link').filter({ hasText: 'GitHub · unavailable' }).waitFor();
  if (await badPage.locator('#source-commit-link').getAttribute('href')) {
    throw new Error('Mismatched build unexpectedly has a clickable source link');
  }
  await badPage.close();
} finally {
  await browser.close();
}
console.log('Preview source links match both tested build manifests');
