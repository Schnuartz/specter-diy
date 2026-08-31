# PR #379 screenshots

Simulator captures referenced from the discussion on
cryptoadvance/specter-diy#379. Not part of the PR branch.

* `old_firmware.png` - saving over an existing key when the write fails,
  on the destroy-then-write version the review was about.
* `new_firmware.png` - the same scenario after the fix.
* `sim_save_regression.png` - saving a key on a stock unix simulator
  build, before the strict_sync() fix.
