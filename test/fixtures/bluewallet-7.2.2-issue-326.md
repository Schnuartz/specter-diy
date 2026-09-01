# bluewallet-7.2.2-issue-326.psbt

Unsigned PSBT produced by **BlueWallet v7.2.2** code
(tag `v7.2.2`, commit `839a1a6595f3115c5e5fa4cf63aca5556c81202b`).

## How it was generated

A v7.2.2 checkout, jest unit test (`tests/unit/issue326-repro.test.ts`):

1. `HDTaprootWallet` from the Specter native-test seed (test-only):
   `ability ability ability ability ability ability ability ability ability ability ability acid`
   at `m/86'/0'/0'` -> fingerprint `fb7c1f11`,
   xpub `xpub6D8vj8gswy7YLx8w3gUEbAvR5JQufcuG16jTEWRCircRPHzaiSUThbFhoq3VhqsoVFS9bpbau6xvHcM7tdXcwDXKKqeNyTvtB1rm2cpT2rF`.
2. `const wo = new WatchOnlyWallet(); wo.setSecret("[fb7c1f11/86h/0h/0h]xpub6D8vj8g...")`
   — the exact string Specter DIY's *Master Public Keys -> Show more keys ->
   Single Taproot* screen encodes into its QR (`XPubScreen`: `prefix + xpub`).
3. `wo.init()` -> `HDTaprootWallet`, `wo.getMasterFingerprint()` = `0x111f7cfb`
   (byte-reversed `fb7c1f11`), `getMasterFingerprintHex()` = `fb7c1f11`.
4. `wo.createTransaction([utxo@ext#0 100000], [{recipient p2tr, 40000}], 5, changeAddr=int#0)`
   — the watch-only / external-signer PSBT path
   (`WatchOnlyWallet.createTransaction` -> `HDTaprootWallet.createTransaction`,
   `skipSigning = true`, `masterFingerprint` passed through).

## What BlueWallet actually wrote

    input 0  tapBip32Derivation.masterFingerprint = fb7c1f11   path m/86'/0'/0'/0/0
    output 1 tapBip32Derivation.masterFingerprint = fb7c1f11   path m/86'/0'/0'/1/0   (BIP86 change)
    output 0 = external recipient, no derivation

i.e. the **real Specter fingerprint**, NOT `00000000`.

The only BlueWallet import strings that yield a zero fingerprint
(`tr(xpub...)` with no `[origin]`, or a bare `xpub`) do **not** produce a
BIP86 wallet — they fall back to `HDLegacyP2PKHWallet` at `m/44'/0'/0'`.
