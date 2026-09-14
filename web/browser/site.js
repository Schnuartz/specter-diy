const $ = selector => document.querySelector(selector);
const siteRoot = new URL('../', import.meta.url);
const params = new URLSearchParams(location.search);
const embedded = params.get('embedded') === '1' && window.parent !== window;
const gallery = embedded && params.get('gallery') === '1';
const variant = 'diy';
const diagnosticQrProbe = params.get('probe') === 'qr' &&
  ['127.0.0.1', 'localhost'].includes(location.hostname);
if (embedded) document.documentElement.classList.add('embedded');
if (gallery) document.documentElement.classList.add('gallery');
const notifyParent = message => { if (embedded) parent.postMessage(message, location.origin); };
const canvasBox = $('#screen-overlay');
const status = $('#st');
const dot = $('#dot');
const loading = $('#loading');
const debug = $('#debug-log');
const fileList = $('#sd-files');
const picker = $('#sd-picker');
const video = $('#camera-preview');
const screenVideo = $('#camera-screen-video');
const screenCamera = $('#camera-screen');
const cameraSelect = $('#camera-select');
let worker;
let softwareContext;
let softwareFrame;
let build;
let version;
let program = 'wallet';
let stateFiles = [];
let inserted = false;
let activeCard = null;
let cameraStream;
let cameraLoop;
let scannerActive = false;
let scannerStopTimer;
let backupEnabled = false;
let cameraRequest = 0;
let lastQr = '';
let lastQrAt = 0;
let lastScanFrameAt = 0;
let startupTimer;
let startupStage = 'waiting for the browser worker';
let requestId = 0;
const snapshots = new Map();

function log(message) {
  debug.textContent = `${String(message)}\n${debug.textContent}`.slice(0, 7000);
}
function setStatus(message, running = false) {
  status.textContent = message;
  dot.classList.toggle('on', running);
}
function bootStage(stage) {
  startupStage = stage;
  clearTimeout(startupTimer);
  startupTimer = setTimeout(() => failure(
    `Specter did not finish loading after ${startupStage}. Open Technical details for the runtime log or use Legacy mode.`
  ), 45000);
}
function failure(message) {
  clearTimeout(startupTimer);
  stopCamera();
  scannerActive = false;
  backupEnabled = false;
  screenCamera.hidden = true;
  worker?.terminate();
  worker = undefined;
  setStatus('Simulator error');
  loading.style.display = 'flex';
  loading.replaceChildren();
  const text = document.createElement('p');
  text.textContent = message;
  loading.append(text);
  log(message);
  notifyParent({ type: 'simulator-error', variant, message });
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
function renderFiles(files) {
  fileList.replaceChildren();
  if (!files.length) {
    const empty = document.createElement('li');
    empty.textContent = 'No files on the virtual card';
    fileList.append(empty);
    return;
  }
  for (const file of files) {
    const row = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = `${file.path} (${file.size} B)`;
    const download = document.createElement('button');
    download.textContent = 'Download';
    download.title = `Download ${file.path}`;
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
  const tray = $('#card-slots');
  tray.replaceChildren();
  for (const { slot, initialized } of slots) {
    const row = document.createElement('div');
    row.className = activeCard === slot ? 'inserted' : '';
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'smartcard-graphic';
    const label = document.createElement('strong');
    label.textContent = `MemoryCard ${slot}`;
    const cardStatus = document.createElement('small');
    cardStatus.textContent = activeCard === slot ? 'Inserted' : initialized ? 'MemoryCard' : 'Blank';
    card.append(label, cardStatus);
    card.title = `Click to ${activeCard === slot ? 'remove' : 'insert'} card ${slot}; right-click to reset it`;
    card.setAttribute('aria-label', `MemoryCard ${slot}, ${cardStatus.textContent}. Click to ${activeCard === slot ? 'remove' : 'insert'}; right-click to reset.`);
    card.onclick = () => send(activeCard === slot ? { type: 'card-remove' } : { type: 'card-insert', slot });
    card.oncontextmenu = event => {
      event.preventDefault();
      if (confirm(`Reset MemoryCard ${slot}? Its simulated keys and PIN will be wiped.`)) {
        send({ type: 'card-reset', slot });
      }
    };
    const hint = document.createElement('small');
    hint.className = 'card-hint';
    hint.textContent = activeCard === slot ? 'Click to remove' : 'Click to insert';
    row.append(card, hint);
    tray.append(row);
  }
}
function onWorkerMessage({ data }) {
  if (data.type === 'running') {
    clearTimeout(startupTimer);
    loading.style.display = 'none';
    setStatus('Running locally', true);
    send({ type: 'sd-list' });
    send({ type: 'card-list' });
    notifyParent({ type: 'simulator-running', variant });
  } else if (data.type === 'log') {
    log(data.message);
    if (data.message === 'SPECTER_BROWSER_BOOT') bootStage('the firmware entry point');
    else if (data.message === 'SPECTER_IMPORTS_DONE') bootStage('firmware imports');
    else if (data.message === 'SPECTER_MAIN_IMPORTED') bootStage('loading Specter');
  } else if (data.type === 'wasm-ready') {
    bootStage('initializing WebAssembly');
  } else if (data.type === 'debug') {
    if (!data.message.includes('registerOrRemoveHandler')) log(data.message);
  } else if (data.type === 'abort') {
    failure(data.message);
  } else if (data.type === 'operation-error') {
    log(`${data.operation}: ${data.message}`);
    if (data.operation.startsWith('sd-')) $('#sd-state').textContent = `SD error: ${data.message}`;
  } else if (data.type === 'sd-state') {
    inserted = data.inserted;
    $('#sd-state').textContent = inserted ? 'Inserted' : 'Ejected';
    $('#sd-toggle').textContent = inserted ? 'Eject SD card' : 'Insert SD card';
    $('#sd-stage').classList.toggle('inserted', inserted);
    notifyParent({ type: 'peripheral-state', variant, sdInserted: inserted, cardSlot: activeCard });
  } else if (data.type === 'sd-list') {
    renderFiles(data.files);
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
    notifyParent({ type: 'peripheral-state', variant, sdInserted: inserted, cardSlot: activeCard });
  } else if (data.type === 'frame') {
    drawFrame(data.pixels);
  } else if (data.type === 'snapshot') {
    const resolve = snapshots.get(data.requestId);
    if (resolve) { snapshots.delete(data.requestId); resolve(data.files); }
  } else if (data.type === 'qr-delivered') {
    $('#camera-state').textContent = `QR delivered locally (${data.size} bytes)`;
  } else if (data.type === 'scanner-state') {
    scannerActive = data.active;
    clearTimeout(scannerStopTimer);
    if (scannerActive) {
      log('Specter scanner active');
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
        if (!backupEnabled) stopCamera();
      }, 250);
    }
  }
}
async function start() {
  if (!('Worker' in window)) {
    failure('This browser needs Web Workers to run Specter locally.');
    return;
  }
  const canvas = newCanvas();
  const transferable = Boolean(canvas.transferControlToOffscreen);
  if (program === 'mockui' && !transferable) {
    failure('This browser needs OffscreenCanvas to render this simulator build.');
    return;
  }
  softwareContext = transferable ? undefined : canvas.getContext('2d');
  softwareFrame = undefined;
  if (!transferable && !softwareContext) {
    failure('This browser cannot create a 2D display canvas.');
    return;
  }
  loading.style.display = 'flex';
  loading.innerHTML = '<div class="spinner"></div><span>Starting Specter on this device…</span>';
  setStatus('Starting locally');
  const workerUrl = new URL('runtime-worker.js', import.meta.url);
  workerUrl.searchParams.set('v', version);
  worker = new Worker(workerUrl, { name: 'Specter DIY' });
  worker.onmessage = onWorkerMessage;
  worker.onerror = event => failure(`Worker crashed: ${event.message || 'unknown error'}`);
  worker.onmessageerror = () => failure('Worker communication failed');
  bootStage('starting the browser worker');
  const offscreen = transferable ? canvas.transferControlToOffscreen() : undefined;
  send({ type: 'start', build, version, program, canvas: offscreen, headlessDisplay: !transferable,
    stateFiles, sdInserted: inserted, cardSlot: activeCard, qrProbe: diagnosticQrProbe }, offscreen ? [offscreen] : []);
}
function snapshot() {
  if (!worker) return Promise.resolve(stateFiles);
  return new Promise(resolve => {
    const id = ++requestId;
    const timeout = setTimeout(() => { snapshots.delete(id); resolve(stateFiles); }, 3000);
    snapshots.set(id, files => { clearTimeout(timeout); resolve(files); });
    send({ type: 'snapshot', requestId: id });
  });
}
async function restart(factory = false) {
  stateFiles = await snapshot();
  if (factory) stateFiles = stateFiles.filter(file => file.path.startsWith('sd/') || file.path.startsWith('cards/'));
  stopCamera();
  scannerActive = false;
  backupEnabled = false;
  screenCamera.hidden = true;
  worker?.terminate();
  worker = undefined;
  await start();
}
let restoreResolve;
addEventListener('message', async event => {
  if (!embedded || event.source !== parent || event.origin !== location.origin) return;
  if (event.data?.type === 'peripherals-provide' && restoreResolve) {
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
    restart(false);
  }
});
function awaitPeripherals() {
  if (!embedded) return Promise.resolve([]);
  return new Promise(resolve => {
    restoreResolve = resolve;
    notifyParent({ type: 'child-awaiting-peripherals', variant });
  });
}
if (embedded) {
  new ResizeObserver(() => notifyParent({ type: 'child-height', height: document.body.scrollHeight })).observe(document.body);
}
async function importFiles(files) {
  for (const file of files) {
    try {
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
  $('#camera-toggle').textContent = 'Show backup preview';
  $('#camera-state').textContent = 'Camera off';
}
async function startCamera(deviceId) {
  const request = cameraRequest + 1;
  stopCamera();
  cameraRequest = request;
  if (!navigator.mediaDevices?.getUserMedia) {
    $('#camera-state').textContent = 'Camera unavailable: secure context required';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } },
    });
    if (request !== cameraRequest) { stream.getTracks().forEach(track => track.stop()); return; }
    cameraStream = stream;
    log('Browser camera opened locally');
    video.srcObject = cameraStream;
    screenVideo.srcObject = cameraStream;
    video.hidden = !backupEnabled;
    screenVideo.hidden = !scannerActive;
    await video.play();
    if (scannerActive) { await screenVideo.play(); screenCamera.classList.add('active'); }
    $('#camera-toggle').textContent = backupEnabled ? 'Hide backup preview' : 'Show backup preview';
    $('#camera-state').textContent = 'Camera active — frames stay on this device';
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
    $('#camera-state').textContent = error.name === 'NotAllowedError'
      ? 'Camera permission denied' : `Camera unavailable: ${error.message}`;
    screenCamera.classList.remove('active');
  }
}
const scanCanvas = document.createElement('canvas');
const scanContext = scanCanvas.getContext('2d', { willReadFrequently: true });
function scanFrame() {
  if (!cameraStream) return;
  const now = performance.now();
  if (scannerActive && now - lastScanFrameAt >= 100 && video.readyState >= 2 && video.videoWidth) {
    lastScanFrameAt = now;
    const scale = Math.min(1, 640 / video.videoWidth);
    scanCanvas.width = Math.max(1, Math.floor(video.videoWidth * scale));
    scanCanvas.height = Math.max(1, Math.floor(video.videoHeight * scale));
    scanContext.drawImage(video, 0, 0, scanCanvas.width, scanCanvas.height);
    const pixels = scanContext.getImageData(0, 0, scanCanvas.width, scanCanvas.height);
    const qr = window.jsQR?.(pixels.data, pixels.width, pixels.height, { inversionAttempts: 'attemptBoth' });
    if (qr?.binaryData?.length) {
      const key = Array.from(qr.binaryData).join(',');
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

$('#restart-btn').onclick = () => restart(false);
$('#factory-btn').onclick = () => restart(true);
$('#sd-toggle').onclick = () => send({ type: inserted ? 'sd-eject' : 'sd-insert' });
$('#sd-clear').onclick = () => send({ type: 'sd-clear' });
$('#sd-add').onclick = () => picker.click();
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
    $('#camera-toggle').textContent = 'Hide backup preview';
    if (!cameraStream) startCamera();
  } else {
    video.hidden = true;
    $('#camera-toggle').textContent = 'Show backup preview';
    if (!scannerActive) stopCamera();
  }
};
$('#camera-screen-start').onclick = () => startCamera();
$('#camera-screen-back').onclick = () => { screenCamera.hidden = true; };
cameraSelect.onchange = () => startCamera(cameraSelect.value);
addEventListener('pagehide', () => { stopCamera(); worker?.terminate(); });

try {
  stateFiles = await awaitPeripherals();
  const pointerPath = new URL('browser/current.json', siteRoot);
  const pointer = await (await fetch(pointerPath, { cache: 'no-store' })).json();
  build = new URL(pointer.build, siteRoot).href;
  version = pointer.version;
  if (!/^builds\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/[a-f0-9]{40}\/$/.test(pointer.build) ||
      new URL(build).origin !== location.origin) throw new Error('Invalid build pointer');
  if (!/^[a-f0-9]{16}$/.test(version)) throw new Error('Invalid artifact version');
  const manifest = await (await fetch(`${build}build-info.json`, { cache: 'no-store' })).json();
  if (!build.includes(manifest.commit) || manifest.artifact_set_sha256?.slice(0, 16) !== version) {
    throw new Error('Build manifest mismatch');
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(manifest.repository) ||
      !pointer.build.includes(`/${manifest.repository}/${manifest.commit}/`)) {
    throw new Error('Wrong source repository in build manifest');
  }
  if (!/^[a-f0-9]{40}$/.test(manifest.commit)) throw new Error('Invalid source commit in build manifest');
  program = manifest.entrypoint === 'mockui' ? 'mockui' : 'wallet';
  $('#build-label').textContent = `${manifest.repository} · ${manifest.commit.slice(0, 7)} · Browser / WASM`;
  const commitUrl = `https://github.com/${manifest.repository}/commit/${manifest.commit}`;
  $('#source-commit-link').href = commitUrl;
  $('#source-commit-link').textContent = `GitHub · ${manifest.commit.slice(0, 7)}`;
  $('#build-link').href = commitUrl;
  $('#build-link').textContent = manifest.commit.slice(0, 12);
  $('#build-details').textContent = JSON.stringify(manifest, null, 2);
  $('#card-panel').hidden = !manifest.capabilities?.smartcard;
  // The browser build does not require SharedArrayBuffer. GitHub Pages cannot
  // set COOP/COEP headers, and its absence is not a simulator error.
  await start();
} catch (error) {
  if (!$('#source-commit-link').hasAttribute('href')) $('#source-commit-link').textContent = 'GitHub · unavailable';
  failure(`Browser build failed to load: ${error.message}`);
}
