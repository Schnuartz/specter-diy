const roots = {
  ghost: {
    label: 'Ghost test seed',
    mnemonic: 'ghost ghost ghost ghost ghost ghost ghost ghost ghost ghost ghost machine',
    cardSecret: 'CXNkaXkAAAAAAJqm8y/nqN8cx7IhDZsgvJxXv0z/xb2QKksDAwBGKlaDe+zi7C09rvdl5S6PDZZK8jRXLmG3k7VS8ywWqh4xDcWE0G5mZcXGoD8co8bpY3TV',
    pin: '1234',
    pinDigest: 'A6xnQhbz4Vx2HuGl4lXwZ5U2I8iziLRFnhP5eNfIRvQ=',
    children: [
      'forget pumpkin lumber cherry print casino resist three public accident toddler admit',
      'diamond wish drip energy toss mean budget novel lift print ocean label',
    ],
  },
  zoo: {
    label: 'Zoo test seed',
    mnemonic: 'zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong',
    cardSecret: 'CXNkaXkAAAAAAD8sdNlUbm41JgXQ/1CkcQUGFsD6uOUVk4GS9HiNeTmDrEGAcpdMq6YFJ//eq8LsQ5nbAD3z+45ZlboQGoxkZTS2aLcU05dhsPmZHad7KNki',
    pin: '21',
    pinDigest: 'b0tmEhJfs6Da7NJ5nf1snCmUJP2SD5swgRCiwfvY9EM=',
    children: [
      'salt option burden habit silent tone breeze fade idle dilemma subway mix',
      'also voice raise tray tree detail exchange run start still cube actual',
    ],
  },
};

const wallets = [
  ['testnet-ghost-zoo-mirror-2of3.json', 'Testnet Ghost + Zoo + Mirror 2-of-3', 'wsh(sortedmulti(2,[8c24a510/48h/1h/0h/2h]tpubDDzWqfZ5TH48383Byd9PFGxEP1Ws5NVXyYcHTmnHwmhJciowLeBDWNHcpLGocofanSyVHeiNqL4HZkXZfKM7NKm7gZZoPjmA9vTKPpwRSkx/{0,1}/*,[3f635a63/48h/1h/0h/2h]tpubDFPtPArj4GzBEFHohegg1Xatrc1Fi9oSox5LzuSRX91miwQxuUrEpBxpvDRsmZYJKYFhgdK3UStsjC8JKXfUbMinjFqiEM4uNwzVaCaHpys/{0,1}/*,[74d682c3/48h/1h/0h/2h]tpubDFj1hZAYMiqqHgrVQ98sLcStdrnAhNx74ynU6QFDULUhyp1BDeyz6E76HPN16t1fswttuwijEsMnskuZPtCvtdbHt74Rs8Vfk3JsM86wah7/{0,1}/*))#syw6qp8z', 'tb1qem2p9awpmhyts2rrg7wxnze0qm4znep7zl032qwrh0yy4m36364qhza3rn'],
];

export function createDemoFiles() {
  const primary = 'ghost';
  const secondary = 'zoo';
  const files = [];
  const add = (name, text) => files.push({ name, bytes: new TextEncoder().encode(text) });
  add('00-CLAVASTACK-DEMO-README.txt', [
    'UNSAFE PUBLIC TEST DATA - NEVER USE FOR REAL FUNDS', '',
    `MemoryCard 1 prepared seed: ${roots[primary].label}`,
    `MemoryCard 2 prepared seed: ${roots[secondary].label}`,
    '2-of-3 multisig: Ghost + Zoo + public-only Mirror cosigner (fingerprint 74d682c3).',
    'The Mirror mnemonic is deliberately not included or stored.',
    'The card seed is written only after you confirm Save key to the card in Specter DIY.',
    'Testnet only. The multisig JSON contains the single demo receive address.',
  ].join('\n'));
  for (const [id, root] of Object.entries(roots)) {
    add(`01-${id}-PUBLIC-TEST-SEED.txt`, `${root.mnemonic}\n`);
    root.children.forEach((mnemonic, index) => add(`02-${id}-bip85-child-${index}.txt`, `${mnemonic}\n`));
  }
  for (const [name, label, descriptor, address] of wallets) {
    add(name, `${JSON.stringify({ label, descriptor, address, warning: 'UNSAFE PUBLIC TEST DATA' }, null, 2)}\n`);
  }
  add('testnet-ghost-payment-low-fee.psbt', 'cHNidP8BAHECAAAAASIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiAAAAAAD9////AmDqAAAAAAAAFgAUx6q7+sTbbs7TU1rVoghQnTkhKctMmgAAAAAAABYAFOeecUR7lROm99MUyL5extM/T11XAAAAAAABAR+ghgEAAAAAABYAFGLab1CVpSs0DBrqUxM0E2SvvC6OIgYDMe3LFs/Q+FmAUveyh7BwR6EcYJZ8Hn6wJX4CVSU52YQYjCSlEFQAAIABAACAAAAAgAAAAAAAAAAAAAAiAgIlH+LuS8Q3KbCQP/rbz4RtnmrLs6pZOwnWAIVkXL42UxiMJKUQVAAAgAEAAIAAAACAAQAAAAAAAAAA\n');
  add('testnet-ghost-payment-high-fee.psbt', 'cHNidP8BAHECAAAAATMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzMzAAAAAAD9////AmDqAAAAAAAAFgAUx6q7+sTbbs7TU1rVoghQnTkhKcu4iAAAAAAAABYAFOeecUR7lROm99MUyL5extM/T11XAAAAAAABAR+ghgEAAAAAABYAFGLab1CVpSs0DBrqUxM0E2SvvC6OIgYDMe3LFs/Q+FmAUveyh7BwR6EcYJZ8Hn6wJX4CVSU52YQYjCSlEFQAAIABAACAAAAAgAAAAAAAAAAAAAAiAgIlH+LuS8Q3KbCQP/rbz4RtnmrLs6pZOwnWAIVkXL42UxiMJKUQVAAAgAEAAIAAAACAAQAAAAAAAAAA\n');
  add('testnet-multisig-unsigned.psbt', 'cHNidP8BAH0CAAAAAVPh/+2JhFAfPrp/zRvelFDxQmZkKeE7mzuxid4jI7HeAAAAAAD9////AsDUAQAAAAAAFgAUYtpvUJWlKzQMGupTEzQTZK+8Lo6YqwIAAAAAACIAILoTspSokGA0cjE8CnfeIvHqzgm0tdVZxxlrhs1HGyUlAAAAAAABASvgkwQAAAAAACIAIM7UEvXB3ci4KGNHnGmLLwbqKeQ+F98VAcO7yEruOo6qAQVpUiECIGrVr1HvJb+EyrVsXvN+zA7qrbkHWZn0FHMNM1a/YW8hAwLIaxKLZzb4vbk/CzPpvpJ1xyK4gdnYsqDRcQj/5AxHIQN2U+Ja/EjsBdkIPdeOUr7cFnnwU7deuC0N1b6VqHoVg1OuIgYCIGrVr1HvJb+EyrVsXvN+zA7qrbkHWZn0FHMNM1a/YW8cjCSlEDAAAIABAACAAAAAgAIAAIAAAAAAAAAAACIGA3ZT4lr8SOwF2Qg9145SvtwWefBTt164LQ3VvpWoehWDHD9jWmMwAACAAQAAgAAAAIACAACAAAAAAAAAAAAiBgMCyGsSi2c2+L25Pwsz6b6SdcciuIHZ2LKg0XEI/+QMRxx01oLDMAAAgAEAAIAAAACAAgAAgAAAAAAAAAAAAAABAWlSIQJm1MSsX0fl00vbSLL0WO71Iu2PBZjpqOJ3iKPoztoYACECd+th5B1LbNaBUgaMQ1l5+bJQr6k5DbV+AZVqfuPhOVohA5smzt5dkVK71zJ84yDQxEfmRf2bpDxbkf2STK+bIRnVU64iAgObJs7eXZFSu9cyfOMg0MRH5kX9m6Q8W5H9kkyvmyEZ1RyMJKUQMAAAgAEAAIAAAACAAgAAgAEAAAAAAAAAIgICd+th5B1LbNaBUgaMQ1l5+bJQr6k5DbV+AZVqfuPhOVocP2NaYzAAAIABAACAAAAAgAIAAIABAAAAAAAAACICAmbUxKxfR+XTS9tIsvRY7vUi7Y8FmOmo4neIo+jO2hgAHHTWgsMwAACAAQAAgAAAAIACAACAAQAAAAAAAAAA\n');
  const bytes = value => Uint8Array.from(atob(value), character => character.charCodeAt(0));
  const cards = [primary, secondary].map((id, index) => ({
    slot: index + 1,
    id,
    label: roots[id].label,
    pin: roots[id].pin,
    secret: bytes(roots[id].cardSecret),
    pinDigest: bytes(roots[id].pinDigest),
  }));
  return { files, roots, primary, secondary, cards };
}
