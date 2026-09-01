import os
import sys
import json
import hmac
import hashlib
import platform

from .core import KeyStoreError, PinError
from .ram import RAMKeyStore
from platform import CriticalErrorWipeImmediately
from binascii import hexlify, unhexlify
from rng import get_random_bytes
from embit import ec, bip39, bip32
from helpers import tagged_hash
from gui.screens import (Alert, PinScreen, Menu, MnemonicScreen, InputScreen,
                         Prompt)

# Suffixes for the two scratch names a replacement save uses: the new file
# while it is being written and verified, and the file it replaces while it
# is being retired. Both are prefixed with a dot and derived from the target
# name, so they never match the reckless / specterdiy prefix the key
# listings look for - a leftover from an interrupted save cannot show up as
# a loadable key - and a leftover belonging to one key can never be
# confused with, or destroyed by, a save of another.
SAVE_TMP_SUFFIX = ".tmp"
SAVE_OLD_SUFFIX = ".old"


class FlashKeyStore(RAMKeyStore):
    """
    KeyStore that stores secrets in Flash of the MCU.
    By default the bitcoin secret is not stored in Flash,
    so the device operates in amnesic mode.
    To save the key on the flash
    you need to call `save_mnemonic` method.
    At most one mnemonic can be stored.
    Trezor's security model.
    """

    NAME = "Internal storage"
    NOTE = "Uses internal memory of the microcontroller for all keys."
    # Button to go to storage menu
    # Menu is implemented in async storage_menu function
    storage_button = "Flash storage"
    load_button = "Load key from internal memory"

    def __init__(self):
        super().__init__()
        self._is_locked = True
        # PIN is not the user PIN itself
        # but a hmac of internal secret with user's PIN
        # see _unlock() method for details
        self.pin = None
        self._pin_attempts_max = 10
        self._pin_attempts_left = 10
        # PIN secret derived from PIN and internal secret
        # tagged_hash("pin", self.secret+pin.encode())
        self.pin_secret = None

    def load_state(self):
        """Verify file and load PIN state from it"""
        # If PIN file doesn't exist - create it
        # This can happen if the device was initialized with the smartcard
        if not platform.file_exists(self.path + "/pin"):
            self.create_empty_pin_file()
            return
        try:
            # verify that the pin file is ok
            _, data = self.load_aead(self.path + "/pin", self.secret)
            # load pin object
            data = json.loads(data.decode())
            self.pin = unhexlify(data["pin"]) if data["pin"] is not None else None
            self._pin_attempts_max = data["pin_attempts_max"]
            self._pin_attempts_left = data["pin_attempts_left"]
        except Exception as e:
            # this happens if someone tries to change PIN file
            self.wipe(self.path)
            raise CriticalErrorWipeImmediately(
                "Something went terribly wrong!\nDevice is wiped!\n%s" % e
            )

    def create_empty_pin_file(self):
        self.pin = None
        self._pin_attempts_max = 10
        self._pin_attempts_left = 10
        self.save_state()

    def create_new_secret(self, path):
        """Generate new secret and default PIN config"""
        super().create_new_secret(path)
        # set pin object
        self.create_empty_pin_file()
        return self.secret

    @property
    def is_pin_set(self):
        return self.pin is not None

    @property
    def pin_attempts_left(self):
        return self._pin_attempts_left

    @property
    def pin_attempts_max(self):
        return self._pin_attempts_max

    @property
    def is_locked(self):
        return self.is_pin_set and self._is_locked

    @property
    def is_ready(self):
        return (
            (self.pin_secret is not None)
            and (self.enc_secret is not None)
            and (not self.is_locked)
            and (self.fingerprint is not None)
        )

    def _unlock(self, pin):
        """
        Unlock the keystore, raises PinError if PIN is invalid.
        Raises CriticalErrorWipeImmediately if no attempts left.
        """
        # if anything goes wrong here - wipe
        try:
            # decrease the counter
            self._pin_attempts_left -= 1
            self.save_state()
            # check we have attempts
            if self._pin_attempts_left <= 0:
                self.wipe(self.path)
                raise CriticalErrorWipeImmediately("No more PIN attempts!\nWipe!")
        except Exception as e:
            # convert any error to a critical error to wipe the device
            raise CriticalErrorWipeImmediately(str(e))
        # calculate hmac with entered PIN
        key = tagged_hash("pin", self.secret)
        pin_hmac = hmac.new(key=key, msg=pin.encode(), digestmod="sha256").digest()
        # check hmac is the same
        if pin_hmac != self.pin:
            raise PinError(
                "Invalid PIN!\n%d of %d attempts left..."
                % (self._pin_attempts_left, self._pin_attempts_max)
            )
        self._pin_attempts_left = self._pin_attempts_max
        self._is_locked = False
        self.save_state()
        # derive PIN keys for reckless storage
        self.pin_secret = tagged_hash("pin", self.secret + pin.encode())
        self.load_enc_secret()

    def load_enc_secret(self):
        fpath = self.path + "/enc_secret"
        if platform.file_exists(fpath):
            _, secret = self.load_aead(fpath, self.pin_secret)
        else:
            # create new key if it doesn't exist
            secret = get_random_bytes(32)
            self.save_aead(fpath, plaintext=secret, key=self.pin_secret)
        self.enc_secret = secret

    def lock(self):
        """Locks the keystore, requires PIN to unlock"""
        self._is_locked = True
        return self.is_locked

    def _change_pin(self, old_pin, new_pin):
        self._unlock(old_pin)
        self._set_pin(new_pin)

    def save_state(self):
        """Saves PIN state to flash"""
        pin = hexlify(self.pin).decode() if self.pin is not None else None
        obj = {
            "pin": pin,
            "pin_attempts_max": self._pin_attempts_max,
            "pin_attempts_left": self._pin_attempts_left,
        }
        data = json.dumps(obj).encode()
        self.save_aead(self.path + "/pin", plaintext=data, key=self.secret)
        # check it loads
        self.load_state()

    def _set_pin(self, pin):
        """Saves hmac of the PIN code for verification later"""
        # set up pin
        key = tagged_hash("pin", self.secret)
        self.pin = hmac.new(key=key, msg=pin, digestmod="sha256").digest()
        self.pin_secret = tagged_hash("pin", self.secret + pin.encode())
        self.save_state()
        # update encryption secret
        if self.enc_secret is None:
            self.enc_secret = get_random_bytes(32)
        self.save_aead(
            self.path + "/enc_secret", plaintext=self.enc_secret, key=self.pin_secret
        )
        # call unlock now
        self._unlock(pin)

    @property
    def flashpath(self):
        """Path to store bitcoin key"""
        return self.path

    async def init(self, show_fn, show_loader):
        """
        Waits for keystore media
        and loads internal secret and PIN state
        """
        self.show = show_fn
        self.show_loader = show_loader
        platform.maybe_mkdir(self.path)
        self.load_secret(self.path)
        self.load_state()
        # the rest we can get from parent
        await super().init(show_fn, show_loader)

    def fileprefix(self, path):
        if path == self.flashpath:
            return 'reckless'

        hexid = hexlify(tagged_hash("sdid", self.secret)[:4]).decode()
        return "specterdiy%s" % hexid


    def _scratch_paths(self, fullpath):
        """The two scratch names a replacement save of `fullpath` uses."""
        head, _, base = fullpath.rpartition("/")
        return ("%s/.%s%s" % (head, base, SAVE_TMP_SUFFIX),
                "%s/.%s%s" % (head, base, SAVE_OLD_SUFFIX))

    def _listdir_names(self, path):
        """Collect directory names and close MicroPython's ilistdir handle
        before callers rename, delete or unmount anything."""
        entries = os.ilistdir(path)
        try:
            return [entry[0] for entry in entries]
        finally:
            close = getattr(entries, "close", None)
            if callable(close):
                close()

    def _discard_tmp(self, path):
        """Destroy .tmp when another authoritative copy is intact.
        Errors propagate by default: continuing could truncate the same
        path on the next save and free its old clusters unoverwritten.
        Callers with a more important error already in flight catch the
        cleanup error themselves. .old retirement errors always propagate."""
        if not platform.file_exists(path):
            return
        platform.secure_delete_file(path)

    def reconcile_scratch_dir(self, path):
        """
        Finishes or undoes mnemonic replacements in `path` that a power
        cut left half done. Must run BEFORE any listing, load or save
        decides which keys exist: the interrupted case that matters is
        exactly the one where the target file is missing and only a
        scratch copy survives - a plain directory listing would wrongly
        conclude the key is gone.

        Only leftovers in Specter's own namespace (this path's
        fileprefix) are touched, so a user's own ".something.old" on the
        SD card is not ours to manage. Per key name the target is
        authoritative:

        * target + .old: the swap went through; .old (a complete copy of
          the previous key) is securely overwritten and a failed
          retirement is reported.
        * target + .tmp: the target remains authoritative; .tmp may be
          incomplete or verified, but its state was not persisted, so it
          is discarded (log-only).
        * .old (+.tmp) without target: power cut between the two renames.
          .old is the known pre-replacement copy and is renamed back onto
          the target; only after that rename is synced does .tmp go.
        * .tmp without target and without .old: unreachable from the save
          order (a .tmp only disappears once a .old exists), so this means
          tampering or filesystem corruption. Fail safe: keep the only
          potential copy and report it instead of destroying it.
        """
        # Collect before renaming or deleting: mutating a FAT directory
        # while its ilistdir() iterator is active is unsafe. A listing
        # failure propagates; a save must not proceed from unknown state.
        names = self._listdir_names(path)
        prefix = self.fileprefix(path)
        leftovers = {}
        for name in names:
            if not name.startswith("."):
                continue
            for suffix in (SAVE_OLD_SUFFIX, SAVE_TMP_SUFFIX):
                if name.endswith(suffix):
                    base = name[1:-len(suffix)]
                    if base.startswith(prefix):
                        leftovers.setdefault(base, set()).add(suffix)
                    break
        states = {}
        recovery_error = None
        # Availability first: attempt every restore before any stale-copy
        # cleanup. Otherwise one key's cleanup failure could indefinitely
        # hide another key whose .old file is its only surviving copy.
        for base in sorted(leftovers):
            fullpath = "%s/%s" % (path, base)
            tmppath, oldpath = self._scratch_paths(fullpath)
            has_old = SAVE_OLD_SUFFIX in leftovers[base]
            has_tmp = SAVE_TMP_SUFFIX in leftovers[base]
            has_target = base in names
            if has_old and not has_target:
                # .old is the only surviving copy of the key - put it back.
                try:
                    os.rename(oldpath, fullpath)
                    platform.strict_sync()
                except Exception as e:
                    if recovery_error is None:
                        recovery_error = KeyStoreError(
                            "Failed to recover a key file interrupted by a "
                            "power cut: %s" % e
                        )
                else:
                    has_old = False
                    has_target = True
                    names.append(base)
            states[base] = (has_target, has_old, has_tmp)

        if recovery_error is not None:
            raise recovery_error

        cleanup_error = None
        for base in sorted(states):
            fullpath = "%s/%s" % (path, base)
            tmppath, oldpath = self._scratch_paths(fullpath)
            has_target, has_old, has_tmp = states[base]
            if has_old:
                # The target survived, so .old is a stale complete copy.
                # Failing to retire it leaves the old encrypted phrase
                # recoverable from free space - report that.
                try:
                    platform.secure_delete_file(oldpath)
                except Exception as e:
                    if cleanup_error is None:
                        cleanup_error = KeyStoreError(
                            "A previous copy of the key could not be securely "
                            "removed and may still be recoverable from free "
                            "space: %s" % e
                        )
            if has_tmp and not has_target and not has_old:
                if cleanup_error is None:
                    cleanup_error = KeyStoreError(
                        "Found '%s' without its key file - the storage is in "
                        "an inconsistent state. The suspicious file was kept "
                        "rather than destroyed." % tmppath
                    )
            elif has_tmp:
                try:
                    self._discard_tmp(tmppath)
                except Exception as e:
                    if cleanup_error is None:
                        cleanup_error = KeyStoreError(
                            "A temporary encrypted copy of the key could not "
                            "be securely removed and may still be recoverable "
                            "from free space: %s" % e
                        )

        if cleanup_error is not None:
            raise cleanup_error
        return names

    def _save_key_file(self, fullpath, replacing):
        """
        Writes the encrypted recovery phrase to `fullpath`. For a new
        file that is one strict save. Replacing an existing one swaps in
        the new copy without ever leaving zero valid copies or a
        recoverable old allocation:

            write .tmp -> strict sync -> read back and verify
                       -> rename the target to .old
                       -> rename .tmp onto the target -> sync
                       -> secure-delete .old

        Truncating over the old file ("wb") would free its cluster chain
        unoverwritten; destroying it before the new copy exists would
        risk a power cut leaving the user with no copy at all. The rename
        swap avoids both, and reconcile_scratch_dir() recovers the one
        state where the swap is half done (between the two renames).
        Retiring .old is destructive of the user's previous key, so a
        failure there is reported accurately rather than swallowed.
        """
        plaintext = self.mnemonic.encode()
        if not replacing:
            self.save_aead(fullpath, plaintext=plaintext,
                           key=self.enc_secret, strict=True)
            return

        tmppath, oldpath = self._scratch_paths(fullpath)
        try:
            self.save_aead(tmppath, plaintext=plaintext,
                           key=self.enc_secret, strict=True)
            _, written = self.load_aead(tmppath, self.enc_secret)
            if written != plaintext:
                raise KeyStoreError(
                    "The new file did not read back as what was saved"
                )
        except Exception as e:
            # Nothing has been touched yet: clean up and leave the existing
            # file exactly as it was.
            print(e)
            try:
                self._discard_tmp(tmppath)
            except Exception as cleanup_error:
                print(cleanup_error)
            raise KeyStoreError("Failed to write the new file: %s" % e)

        # Keep the stages separate: each failure has a different known-safe
        # recovery state and therefore a different cleanup rule.
        try:
            os.rename(fullpath, oldpath)
        except Exception as e:
            print(e)
            # The target rename failed, so the original target is intact.
            try:
                self._discard_tmp(tmppath)
            except Exception as cleanup_error:
                print(cleanup_error)
            raise KeyStoreError("Failed to store the key: %s" % e)

        try:
            os.rename(tmppath, fullpath)
        except Exception as swap_error:
            # The target name is absent, but both .old and the verified
            # .tmp exist. Restore .old and sync before retiring .tmp. If
            # rollback fails, keep both scratch files for startup/listing
            # recovery rather than risking the only valid copies.
            try:
                os.rename(oldpath, fullpath)
                platform.strict_sync()
            except Exception as rollback_error:
                print(rollback_error)
                raise KeyStoreError(
                    "Failed to store the key and could not restore its "
                    "original filename. Recovery copies were kept: %s"
                    % swap_error
                )
            try:
                self._discard_tmp(tmppath)
            except Exception as cleanup_error:
                print(cleanup_error)
            raise KeyStoreError("Failed to store the key: %s" % swap_error)

        try:
            platform.strict_sync()
        except Exception as e:
            # New target and .old are both still present. Do not retire the
            # known old copy if the directory swap could not be synced.
            raise KeyStoreError(
                "The replacement was written, but its filename change "
                "could not be synced. The previous copy was kept for "
                "recovery: %s" % e
            )

        # The replacement is in place, so the copy it replaced is now
        # expendable - and must not outlive this call in free space.
        try:
            platform.secure_delete_file(oldpath)
            platform.strict_sync()
        except Exception as e:
            print(e)
            raise KeyStoreError(
                "The recovery phrase was saved, but the file it replaced "
                "could not be overwritten and may still be recoverable "
                "from free space: %s" % e
            )

    async def save_mnemonic(self):
        if self.is_locked:
            raise KeyStoreError("Keystore is locked")
        if self.mnemonic is None:
            raise KeyStoreError("Recovery phrase is not loaded")

        path = self.flashpath
        filename = await self.get_input(suggestion=self.mnemonic.split()[0])
        if filename is None:
            return

        fullpath = "%s/%s.%s" % (path, self.fileprefix(path), filename)

        # Recover interrupted replacements in this directory before asking
        # whether there is anything to replace.
        self.reconcile_scratch_dir(path)

        replacing = False
        if platform.file_exists(fullpath):
            scr = Prompt(
                "\n\nFile already exists: %s\n" % filename,
                "Would you like to overwrite this file?",
            )
            res = await self.show(scr)
            if res is False:
                return
            replacing = True

        self._save_key_file(fullpath, replacing)
        # check it's ok
        await self.load_mnemonic(fullpath)
        # return the full file name incl. prefix if saved to SD card, just the name if on flash
        return filename

    @property
    def is_key_saved(self):
        try:
            names = self.reconcile_scratch_dir(self.flashpath)
            prefix = self.fileprefix(self.flashpath)
            flash_files = [name for name in names
                           if name.lower().startswith(prefix)]
        except Exception as e:
            # Never hide a key because recovery could not finish: an
            # unreconciled leftover may be the only copy. The load/delete
            # flows run reconcile_scratch_dir() again and surface the
            # error properly; this property only decides menu buttons.
            print(e)
            return True
        flash_exists = (len(flash_files) > 0)
        return flash_exists

    async def load_mnemonic(self, file=None):
        if self.is_locked:
            raise KeyStoreError("Keystore is locked")

        if file is None:
            file = await self.select_file()
            if file is None:
                return False

        if not platform.file_exists(file):
            raise KeyStoreError("Key is not saved")
        _, data = self.load_aead(file, self.enc_secret)

        self.set_mnemonic(data.decode(), "")
        return True

    async def select_file(self):

        buttons = [(None, 'Internal storage')]
        buttons += self.load_files(self.flashpath)

        return await self.show(Menu(buttons, title="Select a file", last=(None, "Cancel")))

    def load_files(self, path):
        # An interrupted replacement must not make a key look gone in the
        # load/delete picker: reconcile leftovers before listing.
        names = self.reconcile_scratch_dir(path)
        buttons = []
        prefix = self.fileprefix(path)
        files = [name for name in names
                 if name.startswith(prefix)]

        if len(files) == 0:
            buttons += [(None, 'No files found')]
        else:
            files.sort()
            for file in files:
                displayname = file.replace(self.fileprefix(path), "")
                if displayname == "":
                    displayname = "Default"
                else:
                    displayname = displayname[1:]  # strip first character
                buttons += [("%s/%s" % (path, file), displayname)]
        return buttons

    async def delete_mnemonic(self):
        file = await self.select_file()
        if file is None:
            return False
        if not platform.file_exists(file):
            raise KeyStoreError("File not found.")
        try:
            platform.secure_delete_file(file)
        except Exception as e:
            print(e)
            raise KeyStoreError("Failed to delete file '%s'" % file)
        # NOTE: no `return` in a `finally` block here - a return inside
        # finally executes while an exception is propagating and silently
        # discards it, so a failed delete would still report success.
        return True

    async def get_input(
            self,
            title="Enter a name for this seed",
            note="Naming your seeds allows you to store multiple.\n"
                 "Give each seed a unique name!",
            suggestion="",
    ):
        scr = InputScreen(title, note, suggestion, min_length=1, strip=True)
        await self.show(scr)
        return scr.get_value()


    async def storage_menu(self, title="Manage keys on internal flash"):
        """Manage storage, return True if new key was loaded"""
        buttons = [
            # id, text
            (None, title),
            (0, "Save key"),
            (1, "Load key"),
            (2, "Delete key"),
        ]

        # we stay in this menu until back is pressed
        while True:
            # wait for menu selection
            menuitem = await self.show(Menu(buttons, last=(255, None)))
            # process the menu button:
            # back button
            if menuitem == 255:
                return False
            elif menuitem == 0:
                filename = await self.save_mnemonic()
                if filename:
                    await self.show(
                        Alert("Success!", "Your key is stored now.\n\nName: %s" % filename, button_text="OK")
                    )
            elif menuitem == 1:
                if await self.load_mnemonic():
                    await self.show(
                        Alert("Success!", "Your key is loaded.", button_text="OK")
                    )
                return True
            elif menuitem == 2:
                if await self.delete_mnemonic():
                    await self.show(
                        Alert("Success!", "Your key is deleted.", button_text="OK")
                    )
