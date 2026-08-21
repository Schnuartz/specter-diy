# PR #13 attack vectors

All QR files have a white background and valid wallet key-origin metadata.
The SeedQR is the established public test seed from the original fixtures.

1. `1_forged_taproot_change.psbt`: forged Taproot `/1/7` claim.
2. `2_two_forged_change_outputs.psbt`: forged `/1/8` and `/1/9` claims.
3. `3_unknown_input_with_genuine_change.psbt`: one known and one unknown input; genuine internal `/1/10` output.
