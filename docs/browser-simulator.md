# Browser simulator and PR previews

The browser simulator runs the **actual** Specter DIY `src/` application under
the fork's MicroPython Unix port, compiled with Emscripten 3.1.74. LVGL draws
the display in a Web Worker. The website presents that framebuffer inside the
physical device image and forwards pointer coordinates to LVGL. Browser shims
replace only device transport: `/state/sd` via `platform.SDCard`, scanner data
via `pyb.UART('YA')`, and MemoryCard APDUs via `uscard.Reader`.

```text
Browser page → Worker → MicroPython/WASM → Specter Python → LVGL → Canvas
                              ↑                   ↑
                      virtual SD/card       QR scanner seam
```

**Experimental development build. Never enter a real seed phrase or use real
funds.** The browser and any automatically built firmware lack the security
assurances of an official release. Use test seeds and dedicated test hardware.
Imported SD files, scanned QR payloads, and virtual card data stay in the tab;
the shell fetches static assets only. Runtime sockets and SSL are disabled.
Reloading discards simulated state. Normal restart retains simulated flash and
peripheral files; factory reset wipes flash separately. The webcam requires
HTTPS or localhost and browser permission. Some browser versions need the
local Unix simulator; physical-device camera, secure element, air-gap,
STM32 timing, battery, and physical card properties are not simulated.

## Build locally

From a recursive checkout of this repository on Linux or WSL:

1. Install and activate [Emscripten SDK](https://emscripten.org/docs/getting_started/downloads.html)
   **3.1.74** (`emcc --version` must report it). Native build dependencies are
   documented in [build.md](build.md).
2. Run `bash web/browser/build-browser.sh` from the repository root.
3. Run `python3 web/browser/verify_build.py` and
   `python3 web/tests/test-publisher.py`.
4. Run `npm ci --prefix web`, `npx --prefix web playwright install chromium`,
   then `python3 -m http.server 8765 --directory web` in one shell and
   `CI=true npm run test:browser --prefix web` in another. Run
   `npm run test:compat --prefix web` after installing Firefox and WebKit with
   Playwright for additional engine coverage.

The build output is `web/builds/<owner>/<repo>/<source-sha>/` with
`micropython.js`, `.wasm`, `.data`, and `build-info.json`. The manifest records
the source repository/commit, Emscripten version, build time, and SHA256 of
each artifact. `web/browser/current.json` points to the build. Both generated
directories are ignored by Git. `SPECTER_SOURCE_REPOSITORY=owner/repo` can
override the origin URL when building a fork or a PR checkout.

The manifest also records `firmware_version`, decoded from Specter's own
`boot/main/boot.py` version marker. PR and manually dispatched builds include
`pr_number` from the trusted workflow event. The browser displays these fields
with the short source revision, for example `v1.10.5 · PR #40 · Commit 9940d41`,
and the link opens the exact full commit URL.

The build script applies only browser compatibility changes to the checked-out
MicroPython/LVGL C submodules. It freezes the wallet's `src/` tree without
changing wallet screens or logic. Browser-specific Python, JS, and source
patching stay under `web/browser/`. The existing Unix simulator and hardware
firmware build remain separate.

For newer board revisions, the browser freeze uses the board's curated
`f469-disco/manifests/common.py`, including embit from its `src/` package path.
Older revisions without that manifest retain the original flat-library freeze.
The build checks the resulting module list before compiling WebAssembly, so
CPython-only embit examples and tests cannot enter a current browser build.

## CI and Pages

The existing `Build` workflow now runs native tests, builds Unix and STM32
firmware, builds the browser simulator, and runs browser/QR/SD/Smartcard smoke
tests. It checks out the exact PR head SHA. The browser and firmware artifacts
carry separate `source.json` records. The build workflow has **read-only**
repository permissions and no deployment secret.

A separate `Publish browser simulator` workflow runs from the trusted default
branch after `Build` completes. It verifies that the browser manifest, its
artifact hashes, the firmware hashes, and both provenance records identify the
same still-current PR head. It never executes the downloaded build. A passing
default-branch build updates the stable Pages root; a passing PR build updates
`/pr/<number>/` and a single PR comment with links to the simulator, firmware
artifact, and build log. A failed current PR build removes its stale preview
and replaces that one comment with a failure notice, even when it uploaded no
artifacts. Missing or invalid artifacts from a nominally successful run also
invalidate its current PR preview. The failure path identifies the PR from the
trusted `workflow_run` event and GitHub's pull-request API; the untrusted
`build-target` artifact is cross-checked only for successful runs. A run
superseded by a newer PR commit cannot replace the current preview. The
publisher keeps an
`gh-pages` branch as static state and uses `actions/deploy-pages` to deploy the
complete tree. PRs receive no write token or deployment credentials.

### Rebuild an older open PR without a commit

Once this workflow is on the default branch, use **Actions → Build → Run
workflow**, select the default branch, and enter the PR number and the first
seven (or more) hexadecimal characters of its current head SHA. The Build job
resolves the prefix against that PR's current full head SHA before checking out
source. If the head changes to a different prefix before publication, the
publisher ignores the stale run. Seven characters are convenient but are not
globally unique; use a longer prefix when comparing closely spaced revisions.
Whitespace around the inputs is ignored. If the SHA does not match the PR's
current head, the target job reports the current prefix and stops the build.
The CLI helper needs only the PR number and reads the SHA itself:

```sh
python3 web/tools/trigger_pr_build.py 123 --repo Schnuartz/specter-diy
```

This starts the existing `Build` workflow; it does not create another Actions
workflow or add a commit to the PR. Manual runs check out the PR's exact head
for Specter source and firmware, but use the current default branch's browser
build tools and website shell. The browser manifest records both the PR source
commit and the tooling (`platform_commit`) commit. The publisher checks both,
and it can remove a failed current manual preview without downloading any
artifact. A very old PR with incompatible MicroPython/LVGL or firmware sources
may still fail to build; its build log will show the concrete incompatibility.
GitHub's manual Run workflow button is unavailable until this workflow file is
present on the repository's default branch.

For this fork, enable **Settings → Pages → Build and deployment → GitHub
Actions** once. Confirm Actions are enabled and allow the publisher workflow
to write to the repository. After the first successful default-branch build,
the stable URL is `https://schnuartz.github.io/specter-diy/`; PR previews
are `https://schnuartz.github.io/specter-diy/pr/<number>/`. The same workflow
uses `GITHUB_REPOSITORY` and works in another fork after its owner enables
Actions and Pages. PR previews are untrusted development code; the warning
is permanent and no wallet secrets should ever be entered.

GitHub Pages does not provide COOP/COEP response headers. This build does not
require SharedArrayBuffer. The DIY display has a Canvas pixel bridge for
browsers without transferable OffscreenCanvas. Chromium is covered by CI;
Firefox and WebKit should be checked when changing the display bridge. Test
on physical mobile devices and hardware before any release claim.
