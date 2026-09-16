const $ = selector => document.querySelector(selector);
const siteRoot = new URL('../', import.meta.url);
const params = new URLSearchParams(location.search);
const embedded = params.get('embedded') === '1' && window.parent !== window;
const gallery = embedded && params.get('gallery') === '1';
const variant = ['diy', 'play', 'schnuartz'].includes(params.get('variant')) ? params.get('variant') : 'diy';
const diagnosticQrProbe = params.get('probe') === 'qr' &&
  ['127.0.0.1', 'localhost', 'try.clavastack.com'].includes(location.hostname);
if (embedded) document.documentElement.classList.add('embedded');
if (gallery) document.documentElement.classList.add('gallery');
const notifyParent = message => { if (embedded) parent.postMessage(message, location.origin); };
const canvasBox = $('#screen-overlay');
const status = $('#st');
const dot = $('#dot');
const loading = $('#loading');
const loadingTitle = loading.querySelector('[data-loading-title]');
const loadingLabel = loading.querySelector('[data-loading-label]');
const loadingBar = loading.querySelector('[data-loading-bar]');
const loadingTimer = loading.querySelector('[data-loading-timer]');
const loadingActions = loading.querySelector('[data-loading-actions]');
const loadingSteps = [...loading.querySelectorAll('[data-loading-step]')];
const debug = $('#debug-log');
const fileList = $('#sd-files');
const picker = $('#sd-picker');
const video = $('#camera-preview');
const screenVideo = $('#camera-screen-video');
const screenCamera = $('#camera-screen');
const cameraPanel = $('#camera-panel');
const cameraSelect = $('#camera-select');
const cameraToggle = $('#camera-toggle');
const cameraStatusDot = $('#camera-status-dot');
let worker;
let softwareContext;
let softwareFrame;
let build;
let version;
let program = 'wallet';
let stateFiles = [];
let inserted = false;
const SD_CAPACITY_BYTES = 8_000_000_000;
let sdUsedBytes = 0;
let sdFileSizes = new Map();
let activeCard = null;
let cardSlots = [];
const demoCardMetadata = new Map();
let cameraStream;
let cameraLoop;
let scannerActive = false;
let scannerStopTimer;
let backupEnabled = false;
let cameraRequest = 0;
let lastQr = '';
let lastQrAt = 0;
let startupTimer;
let requestId = 0;
let workerDependencyCount = null;
let forceCanvasBridge = false;
let recoveryTimer;
let startupPhase = 'manifest';
let displayMode = 'unselected';
const workerRevision = '2026-09-15.2';
let runGeneration = 0;
let restartPromise;
let startupStartedAt;
let startupTicker;
const snapshots = new Map();
let demoImportBusy = false;

const startupTimeoutMs = 60000;

function log(message) {
  debug.textContent += `[${new Date().toISOString()}] ${String(message)}\n`;
}
log(JSON.stringify({ userAgent: navigator.userAgent, crossOriginIsolated,
  hardwareConcurrency: navigator.hardwareConcurrency, deviceMemory: navigator.deviceMemory ?? 'unavailable',
  devicePixelRatio, touch: navigator.maxTouchPoints, Worker: typeof Worker, WebAssembly: typeof WebAssembly,
  OffscreenCanvas: typeof OffscreenCanvas, transferControlToOffscreen: typeof HTMLCanvasElement.prototype.transferControlToOffscreen,
  canvas2D: Boolean(document.createElement('canvas').getContext('2d')), workerRevision }));
addEventListener('error', event => log(`Page error: ${event.error?.stack || event.message}`));
addEventListener('unhandledrejection', event => log(`Page unhandledrejection: ${event.reason?.stack || event.reason}`));
function setStatus(message, running = false) {
  status.textContent = message;
  dot.classList.toggle('on', running);
}
function clearStartupTimer() {
  if (startupTimer !== undefined) clearTimeout(startupTimer);
  startupTimer = undefined;
}
function stopLoadingClock() {
  if (startupTicker !== undefined) clearInterval(startupTicker);
  startupTicker = undefined;
}
function updateLoadingClock() {
  if (startupStartedAt === undefined) return;
  loadingTimer.textContent = `Elapsed ${((performance.now() - startupStartedAt) / 1000).toFixed(1)} s`;
}
function startLoadingClock() {
  stopLoadingClock();
  startupStartedAt = performance.now();
  updateLoadingClock();
  startupTicker = setInterval(updateLoadingClock, 100);
}
function showLoading(stage = 'runtime', message = 'Preparing the browser runtime…', progress = 12) {
  loading.classList.remove('error');
  loadingTitle.textContent = 'Starting Specter Simulator';
  loadingLabel.textContent = message;
  loadingActions.hidden = true;
  loadingBar.style.width = `${progress}%`;
  loadingBar.parentElement.setAttribute('aria-valuenow', String(progress));
  const order = { runtime: 0, firmware: 1, display: 2 };
  const active = order[stage] ?? 0;
  loadingSteps.forEach((step, index) => {
    step.classList.toggle('active', index === active);
    step.classList.toggle('done', index < active);
  });
  loading.style.display = 'flex';
  startLoadingClock();
}
function showLoadingError(message) {
  stopLoadingClock();
  loading.classList.add('error');
  loadingTitle.textContent = String(message).split('\n', 1)[0]
    .replace(/(?:https?:\/\/|\/builds\/)[^\s)]+/g, url => {
      try { return new URL(url, location.href).pathname.split('/').pop(); } catch { return url; }
    });
  loadingLabel.textContent = `Phase: ${startupPhase} · Display: ${displayMode} · Worker: ${workerRevision} · Build: ${version || 'unknown'}`;
  loadingBar.style.width = '100%';
  loadingBar.parentElement.setAttribute('aria-valuenow', '100');
  loadingActions.hidden = false;
  loadingSteps.forEach(step => { step.classList.remove('active'); step.classList.add('done'); });
  loading.style.display = 'flex';
}
function failure(message, generation = runGeneration) {
  if (generation !== runGeneration) return;
  runGeneration++;
  clearTimeout(recoveryTimer);
  clearStartupTimer();
  stopCamera();
  clearTimeout(scannerStopTimer);
  scannerActive = false;
  screenCamera.hidden = true;
  const failedWorker = worker;
  worker = undefined;
  failedWorker?.terminate();
  setStatus('Simulator error');
  showLoadingError(message);
  log(message);
  notifyParent({ type: 'simulator-error', variant, message });
}
// A failed OffscreenCanvas run gets one fresh worker using the LVGL pixel bridge.
function crashRecover(message, generation = runGeneration) {
  if (generation !== runGeneration) return;
  log(`${message}\nPhase: ${startupPhase}; display: ${displayMode}; generation: ${generation}`);
  clearStartupTimer();
  clearTimeout(recoveryTimer);
  stopCamera();
  clearTimeout(scannerStopTimer);
  scannerActive = false;
  screenCamera.hidden = true;
  worker?.terminate();
  worker = undefined;
  // Invalidate callbacks immediately, including duplicate error/abort events.
  const recoveryGeneration = ++runGeneration;
  if (program === 'wallet' && displayMode === 'OffscreenCanvas' && !forceCanvasBridge) {
    forceCanvasBridge = true;
    log('Retry 2/2: Canvas-Pixelbridge');
    setStatus('Switching display mode…');
    showLoading('display', 'Switching to Canvas-Pixelbridge…', 20);
    recoveryTimer = setTimeout(() => {
      if (recoveryGeneration === runGeneration) start();
    }, 250);
    return;
  }
  failure(message);
}
function send(message, transfer = []) {
  if (worker) worker.postMessage(message, transfer);
}
function pointer(event, down) {
  event.preventDefault();
  const rect = event.currentTarget.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const x = Math.max(0, Math.min(479, Math.floor((event.clientX - rect.left) * 480 / rect.width)));
  const y = Math.max(0, Math.min(799, Math.floor((event.clientY - rect.top) * 800 / rect.height)));
  send({ type: 'pointer', x, y, down });
}
function newCanvas() {
  canvasBox.querySelector('canvas')?.remove();
  const canvas = document.createElement('canvas');
  canvas.id = 'screen';
  canvas.width = 480;
  canvas.height = 800;
  canvas.style.cssText = 'width:100%;height:100%;display:block;touch-action:none;';
  canvas.addEventListener('pointerdown', event => {
    canvas.setPointerCapture(event.pointerId);
    pointer(event, 1);
  });
  canvas.addEventListener('pointermove', event => {
    if (event.buttons) pointer(event, 2);
  });
  canvas.addEventListener('pointerup', event => pointer(event, 0));
  canvas.addEventListener('pointercancel', event => pointer(event, 0));
  canvasBox.prepend(canvas);
  return canvas;
}
function drawFrame(pixels) {
  if (!softwareContext || pixels.length !== 480 * 800 * 4) return;
  if (!softwareFrame) softwareFrame = softwareContext.createImageData(480, 800);
  const output = softwareFrame.data;
  for (let i = 0; i < pixels.length; i += 4) {
    output[i] = pixels[i + 2];
    output[i + 1] = pixels[i + 1];
    output[i + 2] = pixels[i];
    output[i + 3] = 255;
  }
  softwareContext.putImageData(softwareFrame, 0, 0);
}
function renderFiles(files, capacityBytes = SD_CAPACITY_BYTES, usedBytes) {
  sdFileSizes = new Map(files.map(file => [file.path, file.size]));
  sdUsedBytes = Number.isFinite(usedBytes) ? usedBytes : files.reduce((total, file) => total + file.size, 0);
  fileList.replaceChildren();
  if (!files.length) return;
  const group = path => {
    const name = path.toLowerCase();
    if (name.endsWith('.psbt') && (/\.signed(?:\.[^.]+)?\.psbt$/.test(name) || name.includes('.completed.'))) {
      return { rank: 0, label: 'Signed transactions' };
    }
    if (name.endsWith('.psbt')) return { rank: 1, label: 'PSBT' };
    if (name.endsWith('.txt')) return { rank: 2, label: 'Text' };
    if (name.endsWith('.json')) return { rank: 3, label: 'JSON' };
    return { rank: 4, label: 'Other files' };
  };
  const sorted = [...files].sort((a, b) => group(a.path).rank - group(b.path).rank ||
    a.path.localeCompare(b.path, undefined, { sensitivity: 'base' }));
  let previousGroup;
  for (const file of sorted) {
    const currentGroup = group(file.path);
    if (currentGroup.label !== previousGroup) {
      const heading = document.createElement('li');
      heading.className = 'sd-group';
      heading.textContent = currentGroup.label;
      fileList.append(heading);
      previousGroup = currentGroup.label;
    }
    const row = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = file.path;
    const download = document.createElement('button');
    download.textContent = 'Download';
    download.title = `Download ${file.path} (${file.size} B)`;
    download.onclick = () => send({ type: 'sd-export', name: file.path });
    const remove = document.createElement('button');
    remove.textContent = 'Delete';
    remove.title = `Delete ${file.path}`;
    remove.onclick = () => send({ type: 'sd-delete', name: file.path });
    row.append(label, download, remove);
    fileList.append(row);
  }
}
function renderCards(slots) {
  cardSlots = slots;
  const tray = $('#card-slots');
  tray.replaceChildren();
  for (const { slot } of slots) {
    const row = document.createElement('div');
    row.dataset.slot = String(slot);
    row.className = activeCard === slot ? 'inserted' : '';
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'smartcard-graphic';
    const cardImage = document.createElement('img');
    cardImage.className = 'smartcard-photo';
    cardImage.src = new URL('assets/specter-smartcard-blank.png', siteRoot).href;
    cardImage.alt = '';
    const label = document.createElement('strong');
    label.textContent = `MemoryCard ${slot}`;
    const cardStatus = document.createElement('small');
    const insertedHere = activeCard === slot;
    cardStatus.textContent = insertedHere ? 'Inserted' : '';
    cardStatus.hidden = !insertedHere;
    card.append(cardImage, label, cardStatus);
    card.title = `Click to ${activeCard === slot ? 'remove' : 'insert'} card ${slot}; right-click to reset it`;
    card.setAttribute('aria-label', `MemoryCard ${slot}, ${insertedHere ? 'Inserted' : 'Not inserted'}. Click to ${activeCard === slot ? 'remove' : 'insert'}; right-click to reset.`);
    card.onclick = () => send(activeCard === slot ? { type: 'card-remove' } : { type: 'card-insert', slot });
    card.oncontextmenu = event => {
      event.preventDefault();
      if (confirm(`Reset MemoryCard ${slot}? Its simulated keys and PIN will be wiped.`)) {
        demoCardMetadata.delete(slot);
        renderCards(cardSlots);
        send({ type: 'card-reset', slot });
      }
    };
    row.append(card);
    const hint = document.createElement('small');
    hint.className = 'card-hint';
    hint.textContent = activeCard === slot ? 'Click to remove' : 'Click to insert';
    row.append(hint);
    const demo = demoCardMetadata.get(slot);
    if (demo) {
      const details = document.createElement('small');
      details.className = 'card-details';
      details.textContent = `PIN: ${demo.pin}\n${demo.seed}`;
      row.append(details);
    }
    tray.append(row);
  }
}
async function importDemoData() {
  const button = $('#demo-load');
  if (demoImportBusy) return;
  if (startupPhase !== 'running' || !worker) {
    log('Demo import requested before Specter finished starting.');
    return;
  }
  demoImportBusy = true;
  button.disabled = true;
  try {
    const demoUrl = new URL('browser/demo-data.js', siteRoot);
    demoUrl.searchParams.set('v', '20260916-multisig-psbt');
    const { createDemoFiles } = await import(demoUrl.href);
    const demo = createDemoFiles();
    let projected = sdUsedBytes;
    for (const file of demo.files) {
      projected += file.bytes.byteLength - (sdFileSizes.get(file.name) || 0);
      if (projected > SD_CAPACITY_BYTES) throw new Error('Virtual SD card is full');
    }
    for (const file of demo.files) send({ type: 'sd-import', name: file.name, bytes: file.bytes });
    if (!inserted) send({ type: 'sd-insert' });
    stateFiles = await snapshot();
    const hasCard = slot => stateFiles.some(file => file.path === `cards/${slot}/private.key`);
    for (const slot of [1, 2]) {
      if (hasCard(slot)) continue;
      send({ type: 'card-insert', slot });
      stateFiles = await snapshot();
      send({ type: 'card-remove' });
      stateFiles = await snapshot();
    }
    const occupied = slot => stateFiles.some(file => file.path === `cards/${slot}/secret.bin` && file.bytes.byteLength);
    for (const card of demo.cards) {
      if (occupied(card.slot)) continue;
      send({ type: 'state-import', files: [
        { path: `cards/${card.slot}/secret.bin`, bytes: card.secret },
        { path: `cards/${card.slot}/pin.bin`, bytes: card.pinDigest },
        { path: `cards/${card.slot}/attempts`, bytes: new Uint8Array([10]) },
      ] });
    }
    for (const card of demo.cards) {
      demoCardMetadata.set(card.slot, {
        label: card.label,
        pin: card.pin,
        seed: `${card.id}-seed`,
      });
    }
    send({ type: 'card-insert', slot: 1 });
    stateFiles = await snapshot();
    renderCards(cardSlots);
    button.textContent = 'Import Demo Data Again';
  } catch (error) {
    log(`Demo import error: ${error.message}`);
  } finally {
    demoImportBusy = false;
    button.disabled = false;
  }
}
function setLoadingMessage(message) {
  loadingLabel.textContent = message;
}
function onWorkerMessage({ data }, generation = runGeneration) {
  if (generation !== runGeneration) return;
  if (data.type === 'loading-progress') {
    workerDependencyCount = data.remaining;
    const steps = data.remaining === 1 ? 'startup step' : 'startup steps';
    setLoadingMessage(data.remaining > 0
      ? `Loading Specter runtime… (${data.remaining} ${steps} remaining)`
      : 'Starting Specter firmware…');
    loadingBar.style.width = data.remaining > 0 ? '28%' : '48%';
    loadingBar.parentElement.setAttribute('aria-valuenow', data.remaining > 0 ? '28' : '48');
    loadingSteps.forEach((step, index) => { step.classList.toggle('active', index === 0); step.classList.toggle('done', false); });
    setStatus('Loading runtime');
  } else if (data.type === 'wasm-ready') {
    startupPhase = 'firmware';
    log(`wasm-ready; memoryBytes: ${data.memoryBytes ?? 'unavailable'}`);
    setLoadingMessage('Starting Specter firmware…');
    loadingBar.style.width = '48%';
    loadingSteps.forEach((step, index) => { step.classList.toggle('active', index === 1); step.classList.toggle('done', index < 1); });
  } else if (data.type === 'running') {
    clearStartupTimer();
    stopLoadingClock();
    loading.style.display = 'none';
    setStatus('Running locally', true);
    startupPhase = 'running';
    log('running');
    send({ type: 'sd-list' });
    send({ type: 'card-list' });
    notifyParent({ type: 'simulator-running', variant });
  } else if (data.type === 'log') {
    if (/^(SPECTER_|MOCKUI_)/.test(data.message)) startupPhase = data.message;
    if (data.message === 'SPECTER_IMPORTS_DONE' || data.message === 'SPECTER_MAIN_IMPORTED') {
      loadingBar.style.width = '85%';
      setLoadingMessage('Drawing the Specter display…');
      loadingSteps.forEach((step, index) => { step.classList.toggle('active', index === 2); step.classList.toggle('done', index < 2); });
    }
    log(data.message);
  } else if (data.type === 'diagnostic') {
    if (data.event === 'worker-created') startupPhase = 'worker-ready';
    if (data.event === 'asset-request') startupPhase = `loading ${new URL(data.url, location.href).pathname.split('/').pop()}`;
    log(JSON.stringify(data));
  } else if (data.type === 'debug') {
    if (!data.message.includes('registerOrRemoveHandler')) log(data.message);
  } else if (data.type === 'worker-error') {
    const location = data.filename ? ` (${data.filename}:${data.lineno || 0}:${data.colno || 0})` : '';
    const detail = `${data.message}${location}${data.url ? ` (${data.url})` : ''}\nSource: ${data.source || data.type}\n${data.stack || ''}`;
    crashRecover(`${data.name || 'WorkerError'}: ${detail}`, generation);
  } else if (data.type === 'abort') {
    crashRecover(`WebAssembly.Abort: ${data.message}\n${data.stack || ''}`, generation);
  } else if (data.type === 'operation-error') {
    log(`${data.operation}: ${data.message}`);
    if (data.operation.startsWith('sd-')) {
      $('#sd-state').textContent = data.code === 'ENOSPC' ? 'SD full (8 GB)' : `SD error: ${data.message}`;
      if (Number.isFinite(data.usedBytes)) renderFiles(data.files || [], data.capacityBytes, data.usedBytes);
    }
  } else if (data.type === 'sd-state') {
    inserted = data.inserted;
    $('#sd-state').textContent = inserted ? 'Inserted' : 'Ejected';
    $('#sd-toggle').setAttribute('aria-label', inserted ? 'Remove SD card' : 'Insert SD card');
    $('#sd-toggle').setAttribute('aria-pressed', String(inserted));
    $('#sd-toggle').title = inserted ? 'Click to remove SD card' : 'Click to insert SD card';
    $('#sd-hint').textContent = inserted ? 'Click to remove' : 'Click to insert';
    $('#sd-stage').classList.toggle('inserted', inserted);
    notifyParent({ type: 'peripheral-state', variant, sdInserted: inserted, cardSlot: activeCard });
  } else if (data.type === 'sd-list') {
    renderFiles(data.files, data.capacityBytes, data.usedBytes);
    if ($('#sd-state').textContent.startsWith('SD ')) $('#sd-state').textContent = inserted ? 'Inserted' : 'Ejected';
  } else if (data.type === 'sd-file') {
    const blob = new Blob([data.bytes]);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = data.name.split('/').pop();
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } else if (data.type === 'card-state') {
    activeCard = data.active;
    renderCards(data.slots);
    snapshot().then(files => {
      stateFiles = files;
      renderCards(cardSlots);
    });
    notifyParent({ type: 'peripheral-state', variant, sdInserted: inserted, cardSlot: activeCard });
  } else if (data.type === 'frame') {
    drawFrame(data.pixels);
  } else if (data.type === 'snapshot') {
    const resolve = snapshots.get(data.requestId);
    if (resolve) { snapshots.delete(data.requestId); resolve(data.files); }
  } else if (data.type === 'qr-delivered') {
    log(`QR delivered locally (${data.size} bytes)`);
  } else if (data.type === 'scanner-state') {
    scannerActive = data.active;
    clearTimeout(scannerStopTimer);
    if (scannerActive) {
      log('Specter scanner active');
      cameraPanel.hidden = false;
      screenCamera.hidden = false;
      if (cameraStream) {
        screenVideo.hidden = false;
        screenCamera.classList.add('active');
      } else {
        startCamera();
      }
    } else {
      scannerStopTimer = setTimeout(() => {
        if (scannerActive) return;
        screenCamera.hidden = true;
        screenCamera.classList.remove('active');
        lastQr = '';
        backupEnabled = false;
        stopCamera();
        cameraPanel.hidden = true;
      }, 250);
    }
  }
}
async function start() {
  clearTimeout(recoveryTimer);
  clearStartupTimer();
  worker?.terminate();
  worker = undefined;
  const generation = ++runGeneration;
  try {
    if (!('Worker' in window)) {
      failure('This browser needs Web Workers to run Specter locally.');
      return;
    }
    const canvas = newCanvas();
    const transferable = !forceCanvasBridge && Boolean(canvas.transferControlToOffscreen);
    displayMode = transferable ? 'OffscreenCanvas' : 'Canvas-Pixelbridge';
    startupPhase = 'worker-create';
    if (program === 'mockui' && !transferable) {
      failure('This browser cannot run the Playground display because it lacks OffscreenCanvas support.');
      return;
    }
    softwareContext = transferable ? undefined : canvas.getContext('2d');
    softwareFrame = undefined;
    if (!transferable && !softwareContext) {
      failure('This browser cannot create a 2D display canvas.');
      return;
    }
    showLoading('runtime', 'Starting Specter on this device…', 12);
    setStatus('Starting locally');
    clearStartupTimer();
    const startedAt = performance.now();
    workerDependencyCount = null;
    // Mobile browsers may retain a worker script independently of the page
    // shell. Tie it to the verified artifact set so a new deployment cannot
    // combine an old worker with the current firmware manifest.
    const workerUrl = new URL('browser/runtime-worker.js', siteRoot);
    if (version) workerUrl.searchParams.set('v', version);
    workerUrl.searchParams.set('worker', workerRevision);
    log(`Starting ${workerUrl.href}; display: ${displayMode}; generation: ${generation}`);
    worker = new Worker(workerUrl, { name: 'Specter DIY' });
    worker.onmessage = event => {
      if (generation === runGeneration) onWorkerMessage(event, generation);
    };
    worker.onerror = event => {
      event.preventDefault();
      crashRecover(`WorkerError: ${event.message || 'Worker script failed to load or process terminated'} (${event.filename || workerUrl.href}:${event.lineno || 0}:${event.colno || 0})\n${event.error?.stack || ''}`, generation);
    };
    worker.onmessageerror = () => {
      crashRecover('DataCloneError: worker.onmessageerror could not deserialize a worker message', generation);
    };
    startupTimer = setTimeout(() => {
      if (generation !== runGeneration) return;
      const elapsed = Math.round((performance.now() - startedAt) / 1000);
      const detail = workerDependencyCount > 0
        ? `The WebAssembly runtime is still loading (${workerDependencyCount} startup ${workerDependencyCount === 1 ? 'step' : 'steps'} pending).`
        : `The WebAssembly runtime did not report ready after ${elapsed} seconds.`;
      crashRecover(`StartupTimeout: ${detail} Last phase: ${startupPhase}.`, generation);
    }, startupTimeoutMs);
    startupPhase = 'canvas-transfer';
    const offscreen = transferable ? canvas.transferControlToOffscreen() : undefined;
    send({ type: 'start', build, version, program, canvas: offscreen, headlessDisplay: !transferable,
      stateFiles, sdInserted: inserted, cardSlot: activeCard, qrProbe: diagnosticQrProbe }, offscreen ? [offscreen] : []);
    startupPhase = 'runtime-assets';
  } catch (error) {
    crashRecover(`${error.name}: ${error.message}\n${error.stack || ''}`, generation);
  }
}
function snapshot(generation = runGeneration) {
  if (!worker || generation !== runGeneration) return Promise.resolve(stateFiles);
  return new Promise(resolve => {
    const id = ++requestId;
    const timeout = setTimeout(() => { snapshots.delete(id); resolve(stateFiles); }, 3000);
    snapshots.set(id, files => {
      clearTimeout(timeout);
      resolve(generation === runGeneration ? files : stateFiles);
    });
    send({ type: 'snapshot', requestId: id });
  });
}
async function restart(factory = false) {
  if (restartPromise) return restartPromise;
  restartPromise = (async () => {
    stopCamera();
    clearTimeout(scannerStopTimer);
    scannerActive = false;
    screenCamera.hidden = true;
    const generation = runGeneration;
    const previousWorker = worker;
    const files = await snapshot(generation);
    if (generation !== runGeneration) return;
    stateFiles = factory ? files.filter(file => file.path.startsWith('sd/') || file.path.startsWith('cards/')) : files;
    clearStartupTimer();
    runGeneration++;
    worker = undefined;
    previousWorker?.terminate();
    forceCanvasBridge = false;
    await start();
  })().finally(() => { restartPromise = undefined; });
  return restartPromise;
}
let restoreResolve;
let peripheralRetryTimer;
addEventListener('message', async event => {
  if (!embedded || event.source !== parent || event.origin !== location.origin) return;
  if (gallery && event.data?.type === 'gallery-parent-ready') {
    if (restoreResolve) notifyParent({ type: 'child-awaiting-peripherals', variant });
    if (status.textContent === 'Running locally') notifyParent({ type: 'simulator-running', variant });
  } else if (event.data?.type === 'peripherals-provide' && restoreResolve) {
    clearInterval(peripheralRetryTimer);
    restoreResolve(event.data.files || []);
    restoreResolve = undefined;
  } else if (event.data?.type === 'peripherals-export') {
    notifyParent({ type: 'peripherals-snapshot', variant,
      requestId: event.data.requestId, files: await snapshot() });
  } else if (gallery && event.data?.type === 'peripheral-command') {
    const command = event.data.command;
    if (['sd-insert', 'sd-eject', 'sd-import', 'sd-clear', 'sd-delete', 'card-insert',
      'card-remove', 'card-reset', 'state-import', 'state-remove-prefix'].includes(command?.type)) {
      send(command);
    }
  } else if (gallery && event.data?.type === 'runtime-restart') {
    restart(false).catch(error => failure(`Restart failed: ${error.stack || error}`, runGeneration));
  }
});
function awaitPeripherals() {
  if (!embedded) return Promise.resolve([]);
  return new Promise(resolve => {
    restoreResolve = resolve;
    const announce = () => notifyParent({ type: 'child-awaiting-peripherals', variant });
    announce();
    clearInterval(peripheralRetryTimer);
    peripheralRetryTimer = setInterval(() => {
      if (!restoreResolve) { clearInterval(peripheralRetryTimer); return; }
      announce();
    }, 250);
  });
}
if (embedded) {
  new ResizeObserver(() => notifyParent({ type: 'child-height', height: document.body.scrollHeight })).observe(document.body);
}
async function importFiles(files) {
  let projected = sdUsedBytes;
  const projectedSizes = new Map(sdFileSizes);
  for (const file of files) {
    try {
      const name = file.name;
      projected = projected - (projectedSizes.get(name) || 0) + file.size;
      if (projected > SD_CAPACITY_BYTES) {
        throw new Error(`Virtual SD card is full: imported files would exceed its 8 GB capacity`);
      }
      projectedSizes.set(name, file.size);
      const bytes = await file.arrayBuffer();
      send({ type: 'sd-import', name: file.name, bytes }, [bytes]);
    } catch (error) {
      $('#sd-state').textContent = `SD import error: ${error}`;
      log(`SD import error: ${error}`);
    }
  }
}
function stopCamera() {
  cameraRequest++;
  cancelAnimationFrame(cameraLoop);
  cameraStream?.getTracks().forEach(track => track.stop());
  cameraStream = undefined;
  video.srcObject = null;
  screenVideo.srcObject = null;
  video.hidden = true;
  screenVideo.hidden = true;
  screenCamera.classList.remove('active');
  cameraToggle.hidden = true;
  cameraToggle.textContent = 'Show backup preview';
  cameraSelect.hidden = true;
  cameraStatusDot.hidden = true;
  cameraPanel.hidden = true;
}
async function startCamera(deviceId) {
  const request = cameraRequest + 1;
  stopCamera();
  cameraRequest = request;
  if (!navigator.mediaDevices?.getUserMedia) {
    log('Camera unavailable: secure context required');
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } },
    });
    if (request !== cameraRequest) { stream.getTracks().forEach(track => track.stop()); return; }
    cameraStream = stream;
    cameraPanel.hidden = false;
    log('Browser camera opened locally');
    video.srcObject = cameraStream;
    screenVideo.srcObject = cameraStream;
    video.hidden = !backupEnabled;
    screenVideo.hidden = !scannerActive;
    await video.play();
    if (scannerActive) { await screenVideo.play(); screenCamera.classList.add('active'); }
    cameraStatusDot.hidden = false;
    cameraToggle.hidden = false;
    cameraToggle.textContent = backupEnabled ? 'Hide backup preview' : 'Show backup preview';
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'videoinput');
    cameraSelect.replaceChildren();
    for (const device of devices) {
      const option = document.createElement('option');
      option.value = device.deviceId;
      option.textContent = device.label || `Camera ${cameraSelect.length + 1}`;
      cameraSelect.append(option);
    }
    cameraSelect.hidden = devices.length < 2;
    const current = cameraStream.getVideoTracks()[0].getSettings().deviceId;
    if (current) cameraSelect.value = current;
    scanFrame();
  } catch (error) {
    stopCamera();
    log(error.name === 'NotAllowedError'
      ? 'Camera permission denied' : `Camera unavailable: ${error.message}`);
    screenCamera.classList.remove('active');
  }
}
const scanCanvas = document.createElement('canvas');
const scanContext = scanCanvas.getContext('2d', { willReadFrequently: true });
function scanFrame() {
  if (!cameraStream) return;
  if (video.readyState >= 2 && video.videoWidth) {
    const scale = Math.min(1, 640 / video.videoWidth);
    scanCanvas.width = Math.max(1, Math.floor(video.videoWidth * scale));
    scanCanvas.height = Math.max(1, Math.floor(video.videoHeight * scale));
    scanContext.drawImage(video, 0, 0, scanCanvas.width, scanCanvas.height);
    const pixels = scanContext.getImageData(0, 0, scanCanvas.width, scanCanvas.height);
    const qr = window.jsQR?.(pixels.data, pixels.width, pixels.height, { inversionAttempts: 'attemptBoth' });
    if (scannerActive && qr?.binaryData?.length) {
      const key = Array.from(qr.binaryData).join(',');
      const now = performance.now();
      if (key !== lastQr || now - lastQrAt > 1000) {
        const bytes = Uint8Array.from(qr.binaryData);
        send({ type: 'qr', bytes: bytes.buffer }, [bytes.buffer]);
        lastQr = key;
        lastQrAt = now;
      }
    }
  }
  cameraLoop = requestAnimationFrame(scanFrame);
}

function reportRestartFailure(error) {
  failure(`Restart failed: ${error.stack || error}`);
}
$('#restart-btn').onclick = () => restart(false).catch(reportRestartFailure);
$('#factory-btn').onclick = () => restart(true).catch(reportRestartFailure);
loading.querySelector('[data-loading-retry]').onclick = () => restart(false).catch(reportRestartFailure);
loading.querySelector('[data-loading-details]').onclick = event => {
  event.preventDefault();
  const details = $('#technical-details');
  details.open = true;
  details.scrollIntoView({ behavior: 'smooth', block: 'center' });
};
$('#sd-toggle').onclick = () => send({ type: inserted ? 'sd-eject' : 'sd-insert' });
$('#sd-clear').onclick = () => send({ type: 'sd-clear' });
$('#sd-add').onclick = () => picker.click();
$('#demo-load').onclick = importDemoData;
picker.onchange = () => { importFiles(picker.files); picker.value = ''; };
$('#sd-drop').ondragover = event => { event.preventDefault(); $('#sd-drop').classList.add('dragging'); };
$('#sd-drop').ondragleave = () => $('#sd-drop').classList.remove('dragging');
$('#sd-drop').ondrop = event => {
  event.preventDefault();
  $('#sd-drop').classList.remove('dragging');
  importFiles(event.dataTransfer.files);
};
$('#camera-toggle').onclick = () => {
  backupEnabled = !backupEnabled;
  if (backupEnabled) {
    video.hidden = false;
    cameraToggle.textContent = 'Hide backup preview';
    if (!cameraStream) startCamera();
  } else {
    video.hidden = true;
    cameraToggle.textContent = 'Show backup preview';
    if (!scannerActive) stopCamera();
  }
};
$('#camera-screen-start').onclick = () => startCamera();
$('#camera-screen-back').onclick = () => { screenCamera.hidden = true; };
cameraSelect.onchange = () => startCamera(cameraSelect.value);
addEventListener('pagehide', () => {
  runGeneration++; clearTimeout(recoveryTimer); clearStartupTimer(); stopLoadingClock();
  stopCamera(); worker?.terminate(); worker = undefined;
});

function firstBuildValue(...values) {
  return values.find(value => value !== undefined && value !== null && String(value).trim() !== '');
}
function formatPullRequest(value) {
  if (value && typeof value === 'object') value = firstBuildValue(value.number, value.id, value.name, value.title, value.url);
  if (value === undefined || value === null || String(value).trim() === '') return '';
  const text = String(value).trim();
  const match = text.match(/(?:pull\/|pr\s*#?\s*|#)(\d+)/i);
  return match ? `PR #${match[1]}` : text.toLowerCase().startsWith('pr') ? text : `PR ${text}`;
}
function formatBuildPresentation(manifest) {
  const repository = String(manifest.repository || '').trim();
  const repositoryUrl = repository ? `https://github.com/${repository}` : String(manifest.source_url || '').replace(/\/$/, '');
  const commit = String(manifest.commit || '').trim();
  const commitUrl = `${repositoryUrl}/commit/${commit}`;
  const versionLabel = firstBuildValue(manifest.firmware_version, manifest.version, manifest.release_version, manifest.tag_name, manifest.tag);
  const branchPr = String(manifest.branch || '').match(/(?:^|[\/_-])pr[\/_-]?(\d+)/i)?.[1];
  const prLabel = formatPullRequest(firstBuildValue(manifest.pull_request, manifest.pull_request_number, manifest.pr_number, manifest.pr, branchPr));
  const forkValue = firstBuildValue(manifest.fork_repository, manifest.fork_name, manifest.fork_owner,
    typeof manifest.fork === 'object' ? manifest.fork.repository : manifest.fork);
  const isFork = Boolean(forkValue) || manifest.is_fork === true || manifest.fork === true;
  const cleanVersion = versionLabel ? String(versionLabel).replace(/^v/i, '') : '';
  const topLabel = ['GitHub', isFork && 'Fork', cleanVersion && `v${cleanVersion}`, prLabel,
    prLabel && commit.slice(0, 7)].filter(Boolean).join(' · ');
  const context = [
    cleanVersion && `Version: v${cleanVersion}`,
    manifest.branch && `Branch: ${manifest.branch}`,
    isFork && `Fork: ${forkValue === true ? repository : forkValue || repository}`,
    prLabel,
    commit && `Commit: ${commit}`,
  ].filter(Boolean).join(' · ');
  return { repository, repositoryUrl, commit, commitUrl, topLabel, context };
}
function updateBuildMetadata(manifest) {
  const buildPresentation = formatBuildPresentation(manifest);
  const sourceCommitLink = $('#source-commit-link');
  if (sourceCommitLink) {
    sourceCommitLink.href = buildPresentation.repositoryUrl;
    sourceCommitLink.textContent = buildPresentation.topLabel;
  }
  const buildRepositoryLink = $('#build-repository-link');
  if (buildRepositoryLink) {
    buildRepositoryLink.href = buildPresentation.repositoryUrl;
    buildRepositoryLink.textContent = buildPresentation.repository;
  }
  const buildContext = $('#build-context');
  if (buildContext) {
    buildContext.textContent = buildPresentation.context;
    buildContext.hidden = !buildPresentation.context;
  }
  const buildLink = $('#build-link');
  if (buildLink) {
    buildLink.href = buildPresentation.commitUrl;
    buildLink.textContent = buildPresentation.commit.slice(0, 12);
  }
  const buildDetails = $('#build-details');
  if (buildDetails) buildDetails.textContent = JSON.stringify(manifest, null, 2);
  const cardPanel = $('#card-panel');
  if (cardPanel) cardPanel.hidden = !manifest.capabilities?.smartcard;
}

try {
  stateFiles = await awaitPeripherals();
  const pointerPath = variant === 'diy' ? 'browser/current.json' :
    variant === 'play' ? 'browser/variants/specter-playground.json' :
    'browser/variants/specter-playground-schnuartz.json';
  const pointer = await (await fetch(new URL(pointerPath, siteRoot), { cache: 'no-store' })).json();
  const buildPath = String(pointer.build || '').replace(/^\/+/, '');
  version = pointer.version;
  if (!/^builds\/[A-Za-z0-9-]+\/[A-Za-z0-9-]+\/[a-f0-9]{40}\/$/.test(buildPath)) throw new Error('Invalid build pointer');
  build = new URL(buildPath, siteRoot).href;
  if (!/^[a-f0-9]{16}$/.test(version)) throw new Error('Invalid artifact version');
  const manifest = await (await fetch(`${build}build-info.json`, { cache: 'no-store' })).json();
  if (!build.includes(manifest.commit) || manifest.artifact_set_sha256?.slice(0, 16) !== version) {
    throw new Error('Build manifest mismatch');
  }
  const expectedRepos = variant === 'diy' ? ['schnuartz/specter-diy', 'schnuartz-ai/specter-diy'] :
    variant === 'play' ? ['k9ert/specter-playground'] : ['schnuartz/specter-playground'];
  if (!expectedRepos.includes(manifest.repository?.toLowerCase())) throw new Error('Wrong firmware variant in build manifest');
  if (!/^[a-f0-9]{40}$/.test(manifest.commit)) throw new Error('Invalid source commit in build manifest');
  program = manifest.entrypoint === 'mockui' ? 'mockui' : 'wallet';
  log(`Firmware: ${manifest.commit}; build: ${version}; worker: ${workerRevision}`);
  // Build presentation is optional UI. It must never prevent the firmware from starting.
  try {
    updateBuildMetadata(manifest);
  } catch (error) {
    log(`Build metadata display skipped: ${error.stack || error}`);
  }
  if (location.protocol === 'https:' && !window.crossOriginIsolated) {
    const isolationWarning = $('#isolation-warning');
    if (isolationWarning) isolationWarning.hidden = false;
    log('Cross-origin isolation missing: check COOP, COEP and CORP response headers.');
  }
  await start();
} catch (error) {
  failure(`${error.name}: Browser build failed to load: ${error.message}\n${error.stack || ''}`);
}
