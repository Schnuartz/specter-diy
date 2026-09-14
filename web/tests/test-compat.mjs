import { firefox, webkit } from 'playwright';
import { PNG } from 'pngjs';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765/';
for (const [engine, launcher] of [['firefox', firefox], ['webkit', webkit]]) {
  const browser = await launcher.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 850, height: 1000 } });
  await page.goto(base);
  await page.locator('#st').getByText('Running locally').waitFor({ timeout: 60000 });
  const canvas = page.locator('#screen');
  const before = await canvas.screenshot();
  const png = PNG.sync.read(before);
  const colors = new Set();
  for (let i = 0; i < png.data.length; i += 4) {
    colors.add(`${png.data[i]},${png.data[i + 1]},${png.data[i + 2]}`);
  }
  if (colors.size < 12) throw new Error(`${engine}: firmware display is blank`);
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * .17, box.y + box.height * .35);
  await page.waitForTimeout(1000);
  if (before.equals(await canvas.screenshot())) throw new Error(`${engine}: pointer did not reach LVGL`);
  await page.locator('#sd-toggle').click();
  await page.locator('#sd-state').getByText('Inserted').waitFor();
  await page.locator('#restart-btn').click();
  await page.locator('#st').getByText('Running locally').waitFor({ timeout: 60000 });
  await page.locator('#sd-state').getByText('Inserted').waitFor();
  console.log(JSON.stringify({ engine, displayColors: colors.size, pointer: 'pass',
    restart: 'pass', sdState: 'pass' }));
  await browser.close();
}
