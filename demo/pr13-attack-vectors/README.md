# PR #13 attack vectors

These PSBTs exercise forged change metadata and unknown-input handling.

The companion seed QR uses the public test mnemonic `ability` x11 + `acid`.

0. `0_seed_qr_ability_acid.svg`: public test seed for reproducing the cases.
1. `1_forged_taproot_change.psbt`: forged Taproot `/1/7` claim.
2. `2_two_forged_change_outputs.psbt`: forged `/1/8` and `/1/9` claims.
3. `3_unknown_input_with_genuine_change.psbt`: unknown input with genuine `/1/10` output.
