# ESP32-P4 audit fixes: local review notes

Base: `sandman21vs/specter-diy` branch `esp32-p4-port`, commit `dffd80748543f21b294462e4c1aa3c27bd5e2bb6`. This is an incomplete local response to the audit, not a security sign-off. Do not use real seeds or funds.

## Changes for independent review

- Disabled the board UART and USB Serial/JTAG REPL settings, and request disabling native USB CDC, detachment of dupterm and reset of the C6 at the start of `main.py`. TinyUSB still builds its native CDC REPL, creating a pre-application exposure. Neither the earliest boot behavior nor the radio's physical state has been verified. The companion radio may be active before application startup.
- Fail closed on unfamiliar NFC record headers and read errors; verify the selected UID after overwrite confirmation and before readback. UID matching is not authentication and does not cover every individual block access.
- Discard secure-channel session keys after failed handshakes, malformed/authentication failures, or ambiguous request delivery; validate smartcard PIN status and clear the local unlocked state on PIN errors.
- Reject unsupported camera formats, inconsistent layout, invalid buffer indices and lengths, and undersized RGB565 frames. Limit preview dimensions. These C changes need a P4 build and physical camera tests.
- Avoid silently replacing corrupted global settings, yield while waiting for a keystore, fail when no keystores remain, and release SD power after a failed mount.
- Bounds-check the P4 RAM block device, validate PKCS#7 padding and CPython deflate completion, pin the P4 bootloader dependency, and add clarifications to the security documentation.
- Guard a missing mnemonic fixer and QR navigation state; avoid repeatedly adding a QR style and force menu label layout before measuring it.

## Known open work

The report contains many issues not resolved here. In particular, secure erase, Secure Boot/flash encryption/readout protection, a verified radio-off state from power-on, signed release/flasher verification, P4 CI and hardware smoke tests remain open. The PBKDF2 resource bound, full NFC card-swap protection, smartcard PIN retry/change cases, QR lifecycle, USB limits and short writes, display recovery and shared I2C lifetimes, unauthenticated card pinning, plaintext smartcard storage, and smartcard transport bounds also remain to be reviewed or fixed. A pinned dependency alone does not make a reproducible or authenticated release. The MicroPython deflate implementation does not expose the same EOF checks used in the CPython branch.

Security and reproducibility claims in upstream documentation must not be interpreted as applying to the P4 development build. The original report's individual findings should be independently verified; the suggested fixes are not assumed correct merely because they are in the report.

## Verification

- Native suite: 457 tests passed, two skipped, in WSL after the main changes.
- The additional RAM block-device bounds regression and the 13-test KEF suite (including two new padding/deflate cases) passed separately.
- Changed Python modules passed `compileall`.
- The P4 firmware and camera C code compiled and linked in WSL against the pinned dependencies. The image occupies `0x356d60` bytes of its `0x400000`-byte app partition (17% free). The first build hit Linux's per-command length limit; the successful retry used a temporary generated-build-file workaround. This does not establish that the unmodified build script works in this environment.
- No hardware tests or verification of physical security behavior were performed.
