// The Worker owns Emscripten MEMFS and the Specter Python application.
let runtimeReady = false;
const pending = [];
const qrQueue = [];
let scannerActive = false;
let program = 'wallet';
self.screen = { width: 480, height: 800 };
const send = (type, details = {}) => postMessage({ type, ...details });

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
      send('sd-list', { files: walk(fs, '/state/sd') });
    } else if (data.type === 'sd-delete') {
      fs.unlink(`/state/sd/${relativePath(data.name)}`);
      send('sd-list', { files: walk(fs, '/state/sd') });
    } else if (data.type === 'sd-clear') {
      for (const { path } of walk(fs, '/state/sd')) fs.unlink(`/state/sd/${path}`);
      send('sd-list', { files: [] });
    } else if (data.type === 'sd-list') {
      send('sd-list', { files: walk(fs, '/state/sd') });
    } else if (data.type === 'state-import') {
      for (const file of data.files || []) {
        const name = relativePath(file.path);
        if (!name.startsWith('sd/') && !/^cards\/[123]\//.test(name)) throw new Error('Invalid peripheral path');
        const path = `/state/${name}`;
        mkdirs(fs, path.substring(0, path.lastIndexOf('/')));
        fs.writeFile(path, new Uint8Array(file.bytes));
      }
      send('sd-list', { files: walk(fs, '/state/sd') });
      cardInfo(fs);
    } else if (data.type === 'state-remove-prefix') {
      const prefix = data.prefix;
      if (prefix !== 'sd/' && !/^cards\/[123]\/$/.test(prefix)) throw new Error('Invalid peripheral prefix');
      for (const { path } of walk(fs, '/state')) {
        if (path.startsWith(prefix)) fs.unlink(`/state/${path}`);
      }
      send('sd-list', { files: walk(fs, '/state/sd') });
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
    send('operation-error', { operation: data.type, message: String(error) });
  }
}
onmessage = ({ data }) => {
  if (data.type !== 'start') {
    if (runtimeReady) handle(data); else pending.push(data);
    return;
  }
  const canvas = data.canvas;
  program = data.program === 'mockui' ? 'mockui' : 'wallet';
  const headlessDisplay = Boolean(data.headlessDisplay);
  const assetSuffix = data.version ? `?v=${encodeURIComponent(data.version)}` : '';
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
    arguments: ['-X', 'heapsize=64M', data.sdProbe ? '/browser/sd-probe.py' : data.qrProbe ? '/browser/qr-probe.py' : data.cardProbe ? '/browser/card-probe.py' : data.diag ? '/browser/diagnose.py' : data.program === 'mockui' ? '/browser/mockui-boot.py' : '/browser/boot.py', '/state'],
    locateFile: path => data.build + path + assetSuffix,
    preRun: [() => {
      const fs = Module.FS;
      mkdirs(fs, '/state');
      mkdirs(fs, '/state/sd');
      mkdirs(fs, '/state/cards');
      mkdirs(fs, '/bridge');
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
    onAbort: reason => send('abort', { message: String(reason) }),
    onRuntimeInitialized: () => send('wasm-ready'),
  };
  importScripts(data.build + 'micropython.js' + assetSuffix);
};
