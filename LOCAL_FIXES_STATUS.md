# ESP32-P4 audit fixes: local review notes

## Follow-up review of the 23 commits

The fork's `master` at `f8020bfe` and upstream `cryptoadvance/master` at
`bec1ab10` were compared on 2026-11-09. Shared fixes are prepared separately
against the fork's `master`; they must not be interpreted as merged upstream.
The P4 branch retains the protections while those changes are under review.

| Commit | Disposition |
| --- | --- |
| `486ecc21` | P4 startup and console; port-specific. |
| `f82954f1` | P4 companion radio; port-specific. |
| `72abb16b` | P4 NFC backups; extended to warn for nonblank unfamiliar data. |
| `833d3a3a` | P4 NFC backups; port-specific UID check. |
| `9c5e674c` | Common smartcard secure-channel fix; prepared in `codex/smartcard-session-signing-hardening`. |
| `603f99df` | Common smartcard PIN status validation; prepared in the same smartcard branch. |
| `914615c6` | P4 NFC smartcard; extended so a PIN failure disables hosts. Common locked-signing protections are in the smartcard branch. |
| `d1586468` | P4 camera format validation; port-specific. |
| `46f79a61` | P4 camera buffer validation; port-specific. |
| `b61cfac1` | P4 camera preview bounds; port-specific. |
| `f9ff7fb2` | Common keystore wait; prepared in `codex/global-settings-recovery`. |
| `170ace10` | Common settings handling; recovery and retry fixes prepared in the settings branch. |
| `003f6bbb` | Common SD mount cleanup; prepared in the settings branch. |
| `f00e5405` | P4 RAM disk bounds; port-specific. |
| `f4adbbc1` | KEF support exists in this port, not current upstream. |
| `c9f68761` | KEF CPython deflate validation; port-specific. MicroPython remains subject to the limitation below. |
| `017da71b` | LVGL 9 QR style lifecycle; P4 port's UI implementation. |
| `0b558973` | QR navigation guard also applies to upstream's older UI; prepared in `codex/legacy-ui-state-guards`. |
| `17272dd9` | Optional mnemonic fixer guard also applies to upstream's older UI; prepared in the UI branch. |
| `059f653c` | LVGL 9 dialog ownership; P4 port's UI implementation. |
| `ac29297a` | LVGL 9 label layout; P4 port's UI implementation. |
| `391867cd` | P4 bootloader dependency; port-specific. |
| `f174d3b5` | P4 security documentation; port-specific. |

Review follow-up also rejects host requests and signing while locked, retries
startup without recursion, and offers to preserve unreadable settings in a
numbered backup before restoring defaults. No P4 hardware verification has been
performed for these changes.

Base: `sandman21vs/specter-diy` branch `esp32-p4-port`, commit `dffd80748543f21b294462e4c1aa3c27bd5e2bb6`. This is an incomplete local response to the audit, not a security sign-off. Do not use real seeds or funds.

## Changes for independent review

- Disabled the board UART and USB Serial/JTAG REPL settings, and request disabling native USB CDC, detachment of dupterm and reset of the C6 at the start of `main.py`. TinyUSB still builds its native CDC REPL, creating a pre-application exposure. Neither the earliest boot behavior nor the radio's physical state has been verified. The companion radio may be active before application startup.
- Fail closed on unfamiliar NFC record headers and read errors; verify the selected UID after overwrite confirmation and before readback. UID matching is not authentication and does not cover every individual block access.
- Discard secure-channel session keys after failed handshakes, malformed/authentication failures, or ambiguous request delivery; validate smartcard PIN status and clear the local unlocked state on PIN errors.
- Reject unsupported camera formats, inconsistent layout, invalid buffer indices and lengths, and undersized RGB565 frames. Limit preview dimensions. These C changes need a P4 build and physical camera tests.
- Avoid silently replacing corrupted global settings, yield while waiting for a keystore, fail when no keystores remain, and release SD power after a failed mount.
- Bounds-check the P4 RAM block device, validate PKCS#7 padding and CPython deflate completion, pin the P4 bootloader dependency, and add clarifications to the security documentation.
- Guard a missing mnemonic fixer and QR navigation state; avoid repeatedly adding a QR style and force menu label layout before measuring it.

## Additional follow-up fixes

- USB requests are limited to 128 KiB and oversized lines are discarded through their terminator. USB short writes and P4 disconnects/timeouts are reported as errors. Larger requests require a different transport; the limit has not been tested on hardware.
- A lost ISO-DEP WTX response is retried with its extended timeout. Frame waits use the exact carrier-cycle calculation rounded up to milliseconds.
- Failed QR scan startup closes the progress popup and releases the camera; hardware camera lifecycle testing remains outstanding.
- SEC1210 exchanges, responses, WTX counts and serial read deadlines are bounded, and UART failures are translated to smartcard errors. No physical SEC1210 testing has been performed.
- KEF envelopes above 500,000 PBKDF2 iterations are rejected before derivation. This is a compatibility change; use a trusted compatible tool for such backups. The bound has not been timed on P4 hardware.
- After a lost SET_PIN response, the NFC smartcard must accept the selected PIN before the setup is treated as successful. A PIN-change rejection also locks the device and invokes the PIN-error handler.

The SFGT finding in the original report incorrectly asserts that positive SFGI values use a different multiplier from FWT: ISO/IEC 14443-4:2018 section 5.3.5 specifies the same carrier-cycle expression. The old implementation did, however, round that expression too early for large values; that rounding has been corrected.

## Known open work

The report contains many issues not resolved here. In particular, secure erase, Secure Boot/flash encryption/readout protection, a verified radio-off state from power-on, signed release/flasher verification, P4 CI and hardware smoke tests remain open. Full NFC card-swap protection, display recovery and shared I2C lifetimes, unauthenticated card pinning, and plaintext smartcard storage also remain to be reviewed or fixed. The new resource limits and lifecycle changes still require hardware validation. A pinned dependency alone does not make a reproducible or authenticated release. The MicroPython deflate implementation does not expose the same EOF checks used in the CPython branch.

Security and reproducibility claims in upstream documentation must not be interpreted as applying to the P4 development build. The original report's individual findings should be independently verified; the suggested fixes are not assumed correct merely because they are in the report.

## Verification

- Follow-up native suite: 488 tests passed, two skipped, in WSL before the final PIN retry and UART short-write changes. The new PIN retry was then tested with each of the three simulated NFC readers; the PIN-change and UART cases also passed targeted tests.
- The additional RAM block-device bounds regression and the 13-test KEF suite (including two new padding/deflate cases) passed separately.
- Changed Python modules passed `compileall`.
- The P4 firmware and camera C code compiled and linked in WSL against the pinned dependencies. The image occupies `0x356d60` bytes of its `0x400000`-byte app partition (17% free). The first build hit Linux's per-command length limit; the successful retry used a temporary generated-build-file workaround. This does not establish that the unmodified build script works in this environment.
- No hardware tests or verification of physical security behavior were performed.
