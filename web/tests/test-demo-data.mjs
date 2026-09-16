import { createDemoFiles } from '../browser/demo-data.js';

const demo = createDemoFiles();
const names = new Set(demo.files.map(file => file.name));
for (const required of [
  '01-ghost-PUBLIC-TEST-SEED.txt',
  '01-zoo-PUBLIC-TEST-SEED.txt',
  'testnet-ghost-zoo-mirror-2of3.json',
  'testnet-ghost-payment-high-fee.psbt',
  'testnet-multisig-unsigned.psbt',
]) {
  if (!names.has(required)) throw new Error(`Missing demo file: ${required}`);
}
if (demo.files.length !== 11) throw new Error(`Expected 11 demo files, got ${demo.files.length}`);
if (demo.cards.length !== 2 || demo.cards[0].pin !== '1234' || demo.cards[1].pin !== '21') {
  throw new Error('Unexpected demo Smartcard configuration');
}
if ('mirror' in demo.roots || demo.files.some(file => /mirror.*seed/i.test(file.name))) {
  throw new Error('Mirror seed must not be included in public demo data');
}
console.log(JSON.stringify({ result: 'pass', files: demo.files.length, cards: demo.cards.length }));
