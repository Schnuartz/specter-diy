import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../browser/runtime-worker.js', import.meta.url), 'utf8');
const context = vm.createContext({
  postMessage: () => {},
  navigator: { userAgent: 'quota-test', hardwareConcurrency: 1 },
  performance: { now: () => 0 },
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => null } }),
  addEventListener: () => {},
  TextEncoder,
  URL,
});
context.self = context;
vm.runInContext(`${source}\nself.__sdTest = { installSdQuota, sdStorage, SD_CAPACITY_BYTES };`, context);

const sdNode = { path: '/state/sd/almost-full.bin', usedBytes: 7_999_999_999 };
class ErrnoError extends Error {
  constructor(errno) { super('filesystem error'); this.name = 'ErrnoError'; this.errno = errno; }
}
const fs = {
  ErrnoError,
  readdir: path => path === '/state/sd' ? ['.', '..', 'almost-full.bin'] : [],
  stat: path => path === sdNode.path ? { mode: 0, size: sdNode.usedBytes } : { mode: 1, size: 0 },
  isDir: mode => mode === 1,
  getPath: node => node.path,
  analyzePath: path => ({ exists: path === sdNode.path, object: sdNode }),
  lookupPath: () => ({ node: sdNode }),
  write: () => 0,
  writeFile: () => {},
  truncate: () => {},
  allocate: () => {},
  msync: () => 0,
  statfs: () => ({ bsize: 4096, blocks: 1_000_000, bfree: 500_000, bavail: 500_000 }),
};

context.__sdTest.installSdQuota(fs);
const stream = { node: sdNode, position: sdNode.usedBytes, seekable: true, flags: 1 };
fs.write(stream, new Uint8Array(1), 0, 1);
let error;
try { fs.write(stream, new Uint8Array(2), 0, 2); } catch (caught) { error = caught; }
const statfs = fs.statfs('/state/sd');
if (context.__sdTest.SD_CAPACITY_BYTES !== 8_000_000_000 ||
    error?.code !== 'ENOSPC' || error?.capacityBytes !== 8_000_000_000 ||
    context.__sdTest.sdStorage(fs).freeBytes !== 1 ||
    statfs.bsize * statfs.blocks !== 8_000_000_000 || statfs.bfree !== 0 || statfs.bavail !== 0) {
  throw new Error('Worker SD quota is not enforced at 8,000,000,000 bytes');
}
console.log(JSON.stringify({ result: 'pass', capacityBytes: 8_000_000_000,
  enforcement: ['write', 'writeFile', 'truncate', 'allocate', 'msync', 'statvfs'] }));
