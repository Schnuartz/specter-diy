// Execute the real classic browser worker and WASM in a V8 worker with a
// genuinely small native stack. Chromium --js-flags=--stack-size does not
// reliably constrain its dedicated workers. No firmware function is mocked.
import assert from 'node:assert/strict';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { readFileSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInThisContext } from 'node:vm';

const root = fileURLToPath(new URL('../', import.meta.url));
if (isMainThread) {
  const pointer = JSON.parse(readFileSync(resolve(root, 'browser/current.json')));
  const directory = process.env.TEST_BUILD_DIR || resolve(root, pointer.build.replace(/^\//, ''));
  for (const stackSizeMb of [0.5, 0.375]) {
    await new Promise((done, reject) => {
      const worker = new Worker(new URL(import.meta.url), {
        resourceLimits: { stackSizeMb },
        workerData: { directory, version: pointer.version, wasm: process.env.TEST_WASM },
      });
      const logs = [];
      let frames = 0;
      let running = false;
      let settled = false;
      const finish = async error => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        await worker.terminate();
        if (error) reject(error); else done();
      };
      const timer = setTimeout(() => finish(new Error(`Native stack ${stackSizeMb} MB timed out: ${logs.join('\n')}`)), 30000);
      worker.on('error', finish);
      worker.on('message', data => {
        if (data.type === 'log') logs.push(data.message);
        if (data.type === 'frame') frames++;
        if (data.type === 'abort' || data.type === 'worker-error') {
          finish(new Error(`${stackSizeMb} MB: ${data.stack || data.message}\n${logs.join('\n')}`));
        }
        if (data.type === 'running') {
          running = true;
          // Keep running through multiple Asyncify suspend/resume cycles and
          // real LVGL pointer handling, not just the boot acknowledgement.
          worker.postMessage({ type: 'pointer', x: 80, y: 280, down: 1 });
          setTimeout(() => worker.postMessage({ type: 'pointer', x: 80, y: 280, down: 0 }), 50);
          setTimeout(() => {
            if (settled) return;
            try {
              assert.ok(running && frames > 1, 'LVGL did not produce frames');
              assert.ok(logs.includes('SPECTER_MAIN_IMPORTED'));
              console.log(JSON.stringify({ stackSizeMb, result: 'pass', frames, firmware: 'real WASM' }));
              finish();
            } catch (error) { finish(error); }
          }, 2000);
        }
      });
    });
  }
} else {
  const nodeProcess = process;
  globalThis.self = globalThis;
  globalThis.WorkerGlobalScope = function WorkerGlobalScope() {};
  globalThis.location = new URL('https://native-stack.test/browser/runtime-worker.js');
  globalThis.postMessage = data => parentPort.postMessage(data.type === 'frame' ? { type: 'frame' } : data);
  globalThis.addEventListener = (type, callback) => {
    if (type === 'unhandledrejection') nodeProcess.on('unhandledRejection', reason => callback({ reason, preventDefault() {} }));
    if (type === 'error') nodeProcess.on('uncaughtException', error => callback({ error, message: error.message, preventDefault() {} }));
  };
  // Only the static network transport and browser event plumbing are adapted.
  globalThis.fetch = async (input, options) => {
    const name = basename(new URL(String(input), location).pathname);
    assert.ok(['micropython.js', 'micropython.wasm', 'micropython.data'].includes(name));
    const bytes = readFileSync(name === 'micropython.wasm' && workerData.wasm
      ? workerData.wasm : resolve(workerData.directory, name));
    return new Response(options?.method === 'HEAD' ? null : bytes, {
      headers: { 'Content-Type': name.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream' },
    });
  };
  globalThis.importScripts = () => runInThisContext(readFileSync(resolve(workerData.directory, 'micropython.js'), 'utf8'));
  globalThis.process = undefined; // Select Emscripten's browser-worker backend.
  runInThisContext(readFileSync(resolve(root, 'browser/runtime-worker.js'), 'utf8'));
  parentPort.on('message', data => globalThis.onmessage({ data }));
  globalThis.onmessage({ data: { type: 'start', build: 'https://native-stack.test/build/',
    version: workerData.version, headlessDisplay: true, stateFiles: [] } });
}
