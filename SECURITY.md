# Security Policy

This document describes how to report security vulnerabilities in
Specter-DIY and how we handle them. For a description of the security
architecture itself (threat model, PIN protection, storage modes,
firmware verification), see [docs/security-model.md](docs/security-model.md).

## Reporting a Vulnerability

Please report security vulnerabilities by e-mail to:

**association@specter.solutions**

This address reaches the project maintainers, including the release
signers (k9ert and miketlk).

If you want to encrypt your report (recommended for sensitive findings),
use the GPG keys of the release signers — the same people who sign the
firmware releases:

| Person | GPG key | Key location |
|--------|---------|--------------|
| k9ert | `28B358A8843B0109` | <https://github.com/k9ert.gpg> |
| Mike Tolkachev (@miketlk) | `DD5C1264EBD645BE` | <https://github.com/miketlk.gpg> |

The release hash manifests (`sha256.signed.txt`) are signed with the
"Specter Signer 2026" key, controlled by k9ert, fingerprint
`9DC3 3CA8 3058 9DE3 B322 5C26 EEF5 756B 2EA4 2349`
([Ubuntu keyserver](http://keyserver.ubuntu.com/pks/lookup?op=get&search=0x9dc33ca830589de3b3225c26eef5756b2ea42349)).

## Scope

In scope:

- The Specter-DIY firmware (this repository)
- The secure bootloader ([specter-bootloader](https://github.com/cryptoadvance/specter-bootloader))
- The smartcard applets
- The build and release pipeline

Out of scope:

- Attack classes we explicitly do not defend against — see
  [docs/security-model.md](docs/security-model.md), e.g. lab-grade
  physical attacks on the main MCU without the smartcard
- Vulnerabilities in upstream dependencies (secp256k1, embit,
  MicroPython, zbar-wasm, …) — please report them upstream; CC us if
  they directly affect Specter-DIY
- Issues that require user error (weak PINs, mishandled recovery phrase
  backups)

## Process

1. **Report** — send us the affected version, reproduction steps and
   impact. Encrypted reports are welcome (see keys above).
2. **Acknowledgement** — we aim to acknowledge your report within 7
   days.
3. **Coordinated disclosure** — please give us up to 90 days to analyze,
   fix and release before publishing any details. We keep you informed
   about the progress.
4. **Credit** — we credit reporters in the release notes, unless you
   prefer to remain anonymous.

We will not pursue legal action against researchers who report in good
faith and follow this process.
