// The Worker owns Emscripten MEMFS and the Specter Python application.
let runtimeReady = false;
const pending = [];
const qrQueue = [];
let scannerActive = false;
let program = 'wallet';
self.screen = { width: 480, height: 800 };
const send = (type, details = {}) => postMessage({ type, ...details });
const workerRevision = '2026-09-15.2';
const SD_CAPACITY_BYTES = 8_000_000_000;
const SD_ENOSPC = 51;
let fatalReported = false;
function reportError(error, source, details = {}) {
  if (fatalReported) return;
  fatalReported = true;
  send('worker-error', { name: error?.name || 'WorkerError', message: error?.message || String(error),
    stack: error?.stack, source, ...details });
}
send('diagnostic', { event: 'worker-created', workerRevision, userAgent: navigator.userAgent,
  crossOriginIsolated: self.crossOriginIsolated, hardwareConcurrency: navigator.hardwareConcurrency,
  deviceMemory: navigator.deviceMemory ?? 'unavailable', OffscreenCanvas: typeof OffscreenCanvas,
  requestAnimationFrame: typeof self.requestAnimationFrame });
// Observe the actual Emscripten requests without cloning the large WASM response.
const nativeFetch = self.fetch.bind(self);
self.fetch = async (input, options) => {
  const url = String(input?.url || input);
  const started = performance.now();
  send('diagnostic', { event: 'asset-request', url, method: options?.method || 'GET' });
  try {
    const response = await nativeFetch(input, options);
    const mime = response.headers.get('content-type');
    send('diagnostic', { event: 'asset-response', url, status: response.status, mime,
      cacheControl: response.headers.get('cache-control'), contentLength: response.headers.get('content-length'),
      elapsedMs: Math.round(performance.now() - started) });
    if (!response.ok) throw new Error(`HTTP ${response.status} loading ${url}`);
    if (/\.wasm(?:\?|$)/.test(url) && !/^application\/wasm(?:;|$)/i.test(mime || '')) {
      send('diagnostic', { event: 'mime-warning', message: `WASM MIME ${mime}; Emscripten will use ArrayBuffer compilation`, url });
    }
    return response;
  } catch (error) {
    reportError(error, 'asset-fetch', { url });
    throw error;
  }
};
self.addEventListener('error', event => {
  reportError(event.error || new Error(event.message || 'Unhandled worker error'), 'worker.error', {
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  });
});
self.addEventListener('unhandledrejection', event => {
  reportError(event.reason, 'unhandledrejection');
  event.preventDefault();
});

function relativePath(name) {
  if (typeof name !== 'string' || !name || name.startsWith('/') ||
      name.split('/').some(part => !part || part === '.' || part === '..' || part.includes('\\'))) {
    throw new Error('Invalid SD path');
  }
  return name;
}
function mkdirs(fs, path) {
  let current = '';
  for (const part of path.split('/').filter(Boolean)) {
    current += '/' + part;
    try { fs.mkdir(current); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
}
function walk(fs, root, prefix = '') {
  const files = [];
  for (const name of fs.readdir(root)) {
    if (name === '.' || name === '..') continue;
    const path = `${root}/${name}`;
    const relative = prefix ? `${prefix}/${name}` : name;
    if (fs.isDir(fs.stat(path).mode)) files.push(...walk(fs, path, relative));
    else files.push({ path: relative, size: fs.stat(path).size });
  }
  return files;
}
function sdStorage(fs) {
  const files = walk(fs, '/state/sd');
  const usedBytes = files.reduce((total, file) => total + file.size, 0);
  return { files, usedBytes, freeBytes: SD_CAPACITY_BYTES - usedBytes,
    capacityBytes: SD_CAPACITY_BYTES };
}
function sendSdList(fs) {
  send('sd-list', sdStorage(fs));
}
function isSdPath(path) {
  return path === '/state/sd' || path.startsWith('/state/sd/');
}
function sdFullError(fs, usedBytes, requestedBytes) {
  const error = new fs.ErrnoError(SD_ENOSPC);
  error.message = `Virtual SD card is full: ${usedBytes + requestedBytes} bytes would exceed its 8 GB (${SD_CAPACITY_BYTES} byte) capacity`;
  error.code = 'ENOSPC';
  error.capacityBytes = SD_CAPACITY_BYTES;
  error.usedBytes = usedBytes;
  error.requestedBytes = requestedBytes;
  return error;
}
function assertSdProjectedSize(fs, node, nextSize) {
  const currentSize = node.usedBytes ?? fs.stat(fs.getPath(node)).size;
  const usedBytes = sdStorage(fs).usedBytes;
  const projected = usedBytes - currentSize + nextSize;
  if (projected > SD_CAPACITY_BYTES) {
    throw sdFullError(fs, usedBytes, Math.max(0, nextSize - currentSize));
  }
}
function installSdQuota(fs) {
  if (fs.__specterSdQuotaInstalled) return;
  fs.__specterSdQuotaInstalled = true;

  const originalWrite = fs.write.bind(fs);
  fs.write = (stream, buffer, offset, length, position, canOwn) => {
    const path = fs.getPath(stream.node);
    if (isSdPath(path)) {
      const currentSize = stream.node.usedBytes ?? fs.stat(path).size;
      const writeAt = stream.seekable && (stream.flags & 1024)
        ? currentSize : (typeof position === 'undefined' ? stream.position : position);
      assertSdProjectedSize(fs, stream.node, Math.max(currentSize, writeAt + length));
    }
    return originalWrite(stream, buffer, offset, length, position, canOwn);
  };

  const originalWriteFile = fs.writeFile.bind(fs);
  fs.writeFile = (path, data, options) => {
    if (typeof path === 'string' && isSdPath(path)) {
      const result = fs.analyzePath(path);
      const node = result.exists ? result.object : { usedBytes: 0 };
      const byteLength = typeof data === 'string' ? new TextEncoder().encode(data).byteLength : data.byteLength;
      assertSdProjectedSize(fs, node, byteLength);
    }
    return originalWriteFile(path, data, options);
  };

  const originalTruncate = fs.truncate.bind(fs);
  fs.truncate = (path, length) => {
    const node = typeof path === 'string' ? fs.lookupPath(path, { follow: true }).node : path;
    if (isSdPath(fs.getPath(node))) assertSdProjectedSize(fs, node, length);
    return originalTruncate(path, length);
  };

  const originalAllocate = fs.allocate.bind(fs);
  fs.allocate = (stream, offset, length) => {
    if (isSdPath(fs.getPath(stream.node))) {
      const currentSize = stream.node.usedBytes ?? 0;
      assertSdProjectedSize(fs, stream.node, Math.max(currentSize, offset + length));
    }
    return originalAllocate(stream, offset, length);
  };

  const originalMsync = fs.msync.bind(fs);
  fs.msync = (stream, buffer, offset, length, flags) => {
    if (isSdPath(fs.getPath(stream.node))) {
      const currentSize = stream.node.usedBytes ?? 0;
      assertSdProjectedSize(fs, stream.node, Math.max(currentSize, offset + length));
    }
    return originalMsync(stream, buffer, offset, length, flags);
  };

  const originalStatfs = fs.statfs.bind(fs);
  fs.statfs = path => {
    const stats = originalStatfs(path);
    const node = typeof path === 'string' ? fs.lookupPath(path, { follow: true }).node : path;
    if (!isSdPath(fs.getPath(node))) return stats;
    const storage = sdStorage(fs);
    const blockSize = 4096;
    return { ...stats, bsize: blockSize, frsize: blockSize,
      blocks: Math.floor(SD_CAPACITY_BYTES / blockSize),
      bfree: Math.floor(storage.freeBytes / blockSize),
      bavail: Math.floor(storage.freeBytes / blockSize) };
  };
}
function flushQr() {
  if (!runtimeReady || !qrQueue.length) return;
  const fs = Module.FS;
  if (fs.analyzePath('/bridge/qr.bin').exists) return;
  const bytes = qrQueue.shift();
  const framed = new Uint8Array(bytes.length + 2);
  framed.set(bytes);
  framed.set([13, 10], bytes.length);
  fs.writeFile('/bridge/qr.bin', framed);
  send('qr-delivered', { size: bytes.length });
}
function pollScanner() {
  if (!runtimeReady) return;
  const active = Module.FS.analyzePath('/bridge/scan-active').exists;
  if (active !== scannerActive) {
    scannerActive = active;
    send('scanner-state', { active });
  }
}
function cardSlot(value) {
  if (![1, 2, 3].includes(value)) throw new Error('Invalid Smartcard slot');
  return value;
}
function cardInfo(fs) {
  const active = fs.analyzePath('/bridge/card-slot').exists
    ? fs.readFile('/bridge/card-slot')[0] : null;
  const slots = [1, 2, 3].map(slot => ({ slot,
    initialized: fs.analyzePath(`/state/cards/${slot}/private.key`).exists }));
  send('card-state', { active, slots });
}
function createCard(fs, slot) {
  const root = `/state/cards/${slot}`;
  mkdirs(fs, root);
  for (const name of fs.readdir(root)) {
    if (name !== '.' && name !== '..') fs.unlink(`${root}/${name}`);
  }
  const key = new Uint8Array(32);
  crypto.getRandomValues(key);
  fs.writeFile(`${root}/private.key`, key);
  fs.writeFile(`${root}/attempts`, new Uint8Array([10]));
}
function handle(data) {
  const fs = Module.FS;
  try {
    if (data.type === 'pointer') {
      if (Module._browser_pointer) Module._browser_pointer(data.x, data.y, data.down);
      else throw new Error('Pointer bridge unavailable');
    } else if (data.type === 'sd-insert') {
      fs.writeFile('/bridge/sd-inserted', new Uint8Array([1]));
      send('sd-state', { inserted: true });
    } else if (data.type === 'sd-eject') {
      try { fs.unlink('/bridge/sd-inserted'); } catch { /* already ejected */ }
      send('sd-state', { inserted: false });
    } else if (data.type === 'sd-import') {
      const name = relativePath(data.name);
      const path = `/state/sd/${name}`;
      mkdirs(fs, path.substring(0, path.lastIndexOf('/')));
      fs.writeFile(path, new Uint8Array(data.bytes));
      sendSdList(fs);
    } else if (data.type === 'sd-delete') {
      fs.unlink(`/state/sd/${relativePath(data.name)}`);
      sendSdList(fs);
    } else if (data.type === 'sd-clear') {
      for (const { path } of walk(fs, '/state/sd')) fs.unlink(`/state/sd/${path}`);
      sendSdList(fs);
    } else if (data.type === 'sd-list') {
      sendSdList(fs);
    } else if (data.type === 'state-import') {
      for (const file of data.files || []) {
        const name = relativePath(file.path);
        if (!name.startsWith('sd/') && !/^cards\/[123]\//.test(name)) throw new Error('Invalid peripheral path');
        const path = `/state/${name}`;
        mkdirs(fs, path.substring(0, path.lastIndexOf('/')));
        fs.writeFile(path, new Uint8Array(file.bytes));
      }
      sendSdList(fs);
      cardInfo(fs);
    } else if (data.type === 'state-remove-prefix') {
      const prefix = data.prefix;
      if (prefix !== 'sd/' && !/^cards\/[123]\/$/.test(prefix)) throw new Error('Invalid peripheral prefix');
      for (const { path } of walk(fs, '/state')) {
        if (path.startsWith(prefix)) fs.unlink(`/state/${path}`);
      }
      sendSdList(fs);
      cardInfo(fs);
    } else if (data.type === 'sd-export') {
      const name = relativePath(data.name);
      const bytes = fs.readFile(`/state/sd/${name}`);
      send('sd-file', { name, bytes });
    } else if (data.type === 'qr') {
      const bytes = new Uint8Array(data.bytes);
      if (bytes.length > 4094) throw new Error('QR payload exceeds scanner buffer');
      if (qrQueue.length >= 16) qrQueue.shift();
      qrQueue.push(bytes);
      flushQr();
    } else if (data.type === 'card-insert') {
      const slot = cardSlot(data.slot);
      if (!fs.analyzePath(`/state/cards/${slot}/private.key`).exists) createCard(fs, slot);
      fs.writeFile('/bridge/card-slot', new Uint8Array([slot]));
      cardInfo(fs);
    } else if (data.type === 'card-remove') {
      if (fs.analyzePath('/bridge/card-slot').exists) fs.unlink('/bridge/card-slot');
      cardInfo(fs);
    } else if (data.type === 'card-reset') {
      const slot = cardSlot(data.slot);
      createCard(fs, slot);
      if (fs.analyzePath('/bridge/card-slot').exists && fs.readFile('/bridge/card-slot')[0] === slot) {
        fs.unlink('/bridge/card-slot');
      }
      cardInfo(fs);
    } else if (data.type === 'card-list') {
      cardInfo(fs);
    } else if (data.type === 'snapshot') {
      const files = [];
      for (const { path } of walk(fs, '/state')) {
        if (path.startsWith('ramdisk/')) continue;
        files.push({ path, bytes: fs.readFile(`/state/${path}`) });
      }
      if (program === 'mockui') {
        for (const { path } of walk(fs, '/flash')) {
          files.push({ path: `flash/${path}`, bytes: fs.readFile(`/flash/${path}`) });
        }
      }
      send('snapshot', { requestId: data.requestId, files });
    }
  } catch (error) {
    const storage = error?.code === 'ENOSPC' ? sdStorage(fs) : {};
    send('operation-error', { operation: data.type, name: error?.name, code: error?.code,
      message: error?.message || String(error), ...storage });
  }
}
onmessage = async ({ data }) => {
  if (data.type !== 'start') {
    if (runtimeReady) handle(data); else pending.push(data);
    return;
  }
  try {
    const canvas = data.canvas;
    program = data.program === 'mockui' ? 'mockui' : 'wallet';
    const headlessDisplay = Boolean(data.headlessDisplay);
    const assetSuffix = data.version ? `?v=${encodeURIComponent(data.version)}` : '';
    send('diagnostic', { event: 'start-received', workerRevision, build: data.build, version: data.version,
      display: headlessDisplay ? 'Canvas-Pixelbridge' : 'OffscreenCanvas', canvasGetContext: typeof canvas?.getContext });
    if (canvas) {
      canvas.style = {};
      canvas.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0,
        width: canvas.width, height: canvas.height });
    }
    const noop = () => {};
    self.document = {
      addEventListener: noop, removeEventListener: noop,
      getElementById: id => id === 'screen' || id === 'canvas' ? canvas : null,
      querySelector: selector => selector === '#screen' || selector === '#canvas' ? canvas : null,
      body: { addEventListener: noop, removeEventListener: noop, style: {} },
      documentElement: { addEventListener: noop, removeEventListener: noop, style: {} },
    };
    self.window = self;
    self.Module = {
      canvas,
      headlessDisplay,
      // Real Specter DIY hardware runs everything (firmware + wallet state) in
      // 16MB total RAM. 64M here was simulator-only headroom, not a firmware
      // requirement - with 3 instances running at once in the gallery, it was
      // the single biggest avoidable memory cost (192MB of GC heap alone).
      // (Tried 32M for the MockUI variants specifically to reduce GC pauses;
      // reverted - reports of the MockUI simulators freezing appeared right
      // after that change shipped, so back to the size proven stable here.)
      arguments: ['-X', 'heapsize=16M', data.sdProbe ? '/browser/sd-probe.py' : data.qrProbe ? '/browser/qr-probe.py' : data.cardProbe ? '/browser/card-probe.py' : data.diag ? '/browser/diagnose.py' : data.program === 'mockui' ? '/browser/mockui-boot.py' : '/browser/boot.py', '/state'],
      monitorRunDependencies: remaining => send('loading-progress', { remaining }),
      locateFile: path => data.build + path + assetSuffix,
      preRun: [() => {
        const fs = Module.FS;
        mkdirs(fs, '/state');
        mkdirs(fs, '/state/sd');
        mkdirs(fs, '/state/cards');
        mkdirs(fs, '/bridge');
        installSdQuota(fs);
        for (const file of data.stateFiles || []) {
          if (!file.path || file.path.startsWith('ramdisk/')) continue;
          const name = relativePath(file.path);
          const path = program === 'mockui' && name.startsWith('flash/') ? `/${name}` : `/state/${name}`;
          mkdirs(fs, path.substring(0, path.lastIndexOf('/')));
          fs.writeFile(path, new Uint8Array(file.bytes));
        }
        if (data.sdInserted) fs.writeFile('/bridge/sd-inserted', new Uint8Array([1]));
        if (data.cardSlot) fs.writeFile('/bridge/card-slot', new Uint8Array([cardSlot(data.cardSlot)]));
      }],
      print: message => {
        send('log', { message });
        if (message === 'SPECTER_MAIN_IMPORTED' || message === 'MOCKUI_READY' || message === 'DIAG_SPECTER_CREATED' || message === 'QR_PROBE_READY' || message === 'SD_PROBE_WRITTEN' || message === 'CARD_PROBE_READY') {
          runtimeReady = true;
          for (const item of pending.splice(0)) handle(item);
          setInterval(flushQr, 50);
          setInterval(pollScanner, 80);
          pollScanner();
          setTimeout(() => send('running'), 500);
        }
      },
      printErr: message => send('debug', { message }),
      onAbort: reason => {
        if (!fatalReported) {
          fatalReported = true;
          send('abort', { message: String(reason), stack: new Error(String(reason)).stack });
        }
      },
      onRuntimeInitialized: () => send('wasm-ready', { memoryBytes: Module.HEAPU8?.buffer.byteLength }),
    };
    // importScripts does not expose HTTP headers. HEAD checks the script without
    // buffering another copy or altering classic-worker script execution.
    await self.fetch(data.build + 'micropython.js' + assetSuffix, { method: 'HEAD' });
    importScripts(data.build + 'micropython.js' + assetSuffix);
  } catch (error) {
    reportError(error, 'runtime-start');
  }
};
