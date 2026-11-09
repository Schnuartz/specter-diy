# Firmware targets

This integration branch uses the application in `src/` for both the STM32F469
and ESP32-P4 builds. Wallet, signing and keystore changes therefore start in
the same source tree. Board drivers, display and hardware configuration remain
target-specific; a change is only supported on both targets after both builds
and the relevant device tests pass.

The P4 target uses the ESP32 MicroPython port and ESP-IDF. Its pinned
MicroPython revision has P4 support; the F469 target uses the STM32 port in
`f469-disco`. They are distinct runtime revisions, not interchangeable firmware
images. The integration uses the LVGL 9 source and MicroPython binding from the
same `f469-disco` revision for the application UI and the P4 display module.
Upgrading either runtime or LVGL requires validating both targets together.

P4 is experimental. It has no verified signed release chain, secure boot,
flash encryption or secure wipe. Do not use real seeds or funds on it. See
`ports/esp32p4/README.md` and `LOCAL_FIXES_STATUS.md` for the current limits.
