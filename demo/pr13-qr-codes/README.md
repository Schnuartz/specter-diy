# PR #13 demo transactions

Test fixtures for https://github.com/Schnuartz/specter-diy/pull/13 - see the PR
comment for the full explanation, expected result per scenario, and how to
test them (simulator TCP `sign` command or QR scan into a device).

All PSBTs use the well-known public test mnemonic
`abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about`
(fingerprint `73c5da0a`), the same one already used by this repo's own
`test/integration` test suite. No real funds, no real seed.

- `*.psbt` - base64-encoded unsigned PSBTs
- `*.svg` - scannable QR codes for the same PSBTs (plain base64 payload,
  matches Specter's own `B64PSBT_PREFIX` PSBT auto-detection)

Scenarios 1-3 were generated with
[psbt_faker](https://github.com/3rdIteration/psbt_faker) against this
wallet's account xpub. Scenarios 4-5 were hand-crafted with `embit` to cover
cases `psbt_faker` can't produce (genuine branch-1 change, forged branch-1
metadata with a script mismatch).
