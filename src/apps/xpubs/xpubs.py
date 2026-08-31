from app import BaseApp, AppError
from gui.screens import Menu, DerivationScreen, NumericScreen, Alert, InputScreen, Prompt
from .screens import XPubScreen
import json
from binascii import hexlify
from embit.liquid.networks import NETWORKS
from embit import bip32
from helpers import is_liquid
from io import BytesIO
import platform
from collections import OrderedDict

# --- Standard single-sig wallet types --------------------------------------
# Each script type is bound to the BIP purpose whose account key it MUST be
# derived from. Older Specter DIY firmware let the wallet-creation menu pair
# the currently displayed key with an arbitrary script wrapper, which made it
# possible to build a Taproot tr() wallet on top of an m/84' (BIP84) account
# key instead of the BIP86 m/86' key (issue #393). Keeping this mapping
# explicit makes that class of mismatch impossible for the standard flow.
# value: (bip_purpose, descriptor_template, human_name)
WALLET_TYPES = OrderedDict([
    ("wpkh",    (84, "wpkh(%s%s/{0,1}/*)",     "Native Segwit")),
    ("nested",  (49, "sh(wpkh(%s%s/{0,1}/*))", "Nested Segwit")),
    ("legacy",  (44, "pkh(%s%s/{0,1}/*)",      "Legacy")),
    ("taproot", (86, "tr(%s%s/{0,1}/*)",       "Taproot")),
])
_PURPOSE_TO_TYPE = {84: "wpkh", 49: "nested", 44: "legacy", 86: "taproot"}

# Legacy Specter Taproot: recovery only. Older Specter DIY versions could
# create P2TR wallets using an m/84' account key. This derivation is kept
# ONLY so those wallets (and any funds on them) stay recoverable. New Taproot
# wallets must use BIP86 (m/86').
LEGACY_SPECTER_TAPROOT = (84, "tr(%s%s/{0,1}/*)", "Legacy Specter Taproot")

_HARDENED = 0x80000000


def _parse_account_path(derivation):
    """Return ``(purpose, coin, account)`` for an ``m/P'/C'/A'`` path, else None."""
    try:
        idxs = bip32.parse_path(derivation)
    except Exception:
        return None
    if len(idxs) != 3 or not all(i >= _HARDENED for i in idxs):
        return None
    return tuple(i - _HARDENED for i in idxs)


class XpubApp(BaseApp):
    """
    WalletManager class manages your wallets.
    It stores public information about the wallets
    in the folder and signs it with keystore's id key
    """

    export_coldcard = "ckcc"
    export_generic_json = "json"
    export_specter_diy = "specter-diy"
    button = "Master public keys"
    prefixes = [b"fingerprint", b"xpub"]
    name = "xpub"

    def __init__(self, path):
        self.account = 0
        pass

    async def menu(self, show_screen, show_all=False):
        net = NETWORKS[self.network]
        coin = net["bip32"]
        if not show_all:
            buttons = [
                (None, "Recommended"),
                ("m/84h/%dh/%dh" % (coin, self.account), "Single key"),
                ("m/48h/%dh/%dh/2h" % (coin, self.account), "Multisig"),
                (None, "Other keys"),
                (0, "Show more keys"),
                (2, "Change account number"),
                (1, "Enter custom derivation"),
                (3, "Export all keys for this account"),
                (4, "Export multiple accounts"),
            ]
        else:
            buttons = [
                (None, "Recommended"),
                (
                    "m/84h/%dh/%dh" % (coin, self.account),
                    "Single Native Segwit\nm/84h/%dh/%dh" % (coin, self.account)
                ),
                (
                    "m/48h/%dh/%dh/2h" % (coin, self.account),
                    "Multisig Native Segwit\nm/48h/%dh/%dh/2h" % (coin, self.account),
                ),
                (None, "Other keys"),
                (
                    "m/86h/%dh/%dh" % (coin, self.account),
                    "Single Taproot\nm/86h/%dh/%dh" % (coin, self.account)
                ),
                (
                    "m/49h/%dh/%dh" % (coin, self.account),
                    "Single Nested Segwit\nm/49h/%dh/%dh" % (coin, self.account)
                ),
                (
                    "m/48h/%dh/%dh/1h" % (coin, self.account),
                    "Multisig Nested Segwit\nm/48h/%dh/%dh/1h" % (coin, self.account),
                ),
            ]
        # wait for menu selection
        menuitem = await show_screen(
            Menu(
                buttons,
                title="Select the key",
                note="Current account number: %d" % self.account,
                last=(255, None),
            )
        )

        # process the menu button:
        # back button
        if menuitem == 255:
            return False
        elif menuitem == 0:
            return await self.menu(show_screen, show_all=True)
        elif menuitem == 1:
            der = await show_screen(DerivationScreen())
            if der is not None:
                await self.show_xpub(der, show_screen)
                return True
        elif menuitem == 2:
            account = await show_screen(NumericScreen(current_val=str(self.account)))
            if account and int(account) >= 0x80000000:
                raise AppError("Account number too large")
            try:
                self.account = int(account)
            except:
                self.account = 0
            return await self.menu(show_screen)
        elif menuitem == 3:
            file_format = await self.save_menu(show_screen)
            if file_format:
                filename = await self.save_all_to_sd(file_format, self.account, show_screen)
                if filename is not None:
                    await show_screen(
                        Alert("Saved!",
                              "Public keys are saved to the file:\n\n%s" % filename,
                              button_text="Close")
                    )
        elif menuitem == 4:
            from_account = await show_screen(
                NumericScreen(
                    title="Enter START account number",
                    current_val=str(self.account)
                )
            )
            to_account = await show_screen(
                NumericScreen(
                    title="Enter END account number",
                    current_val=str(self.account)
                )
            )
            if from_account is None or to_account is None:
                return
            if from_account == "":
                from_account = self.account
            if to_account == "":
                to_account = self.account
            from_account = int(from_account)
            to_account = int(to_account)
            file_format = await self.save_menu(show_screen)
            await self.export_multiple_accounts_xpubs(
                from_account, to_account, file_format, show_screen
            )
        else:
            await self.show_xpub(menuitem, show_screen)
            return True
        return False

    async def save_all_to_sd(self, file_format, account, show_screen):

        fingerprint = hexlify(self.keystore.fingerprint).decode()

        extension = "txt" if file_format == self.export_specter_diy else "json"
        filename = "%s-%s-%d-all.%s" % (
            file_format, fingerprint, account, extension,
        )

        if not platform.sdcard.is_present:
            raise AppError("Please insert SD card")

        with platform.sdcard as sd:
            if sd.file_exists(filename):
                confirm = await show_screen(Prompt("Overwrite?", message="File %s already exists on the SD card. Overwrite?" % filename))
                if not confirm:
                    return
            with sd.open(filename, "w") as f:
                self._dump_account(f, file_format, account)

        return filename

    async def export_multiple_accounts_xpubs(
        self,
        from_account,
        to_account,
        file_format,
        show_screen,
    ):
        if from_account > to_account:
            from_account, to_account = to_account, from_account
        if to_account >= 0x80000000:
            raise AppError('Account number too large')
        fingerprint = hexlify(self.keystore.fingerprint).decode()
        if file_format == self.export_specter_diy:
            # in our format we can dump any number of accounts in one file
            filename = "%s-%s-%d-%d.txt" % (
                file_format, fingerprint, from_account, to_account
            )
            with platform.sdcard as sd:
                if sd.file_exists(filename):
                    confirm = await show_screen(Prompt("Overwrite?", message="File %s already exists on the SD card. Overwrite?" % filename))
                    if not confirm:
                        return
                with sd.open(filename, "w") as f:
                    for account in range(from_account, to_account+1):
                        self.show_loader(title="Exporting account %d..." % account)
                        self._dump_account(f, file_format, account)
            await show_screen(
                Alert(
                    "Success!",
                    "File was successfully saved under:\n\n%s" % filename,
                    button_text="OK",
                )
            )
        else: # cc format - one file per account
            for account in range(from_account, to_account+1):
                self.show_loader(title="Exporting account %d..." % account)
                await self.save_all_to_sd(file_format, account, show_screen)
            await show_screen(
                Alert(
                    "Success!",
                    "All accounts are saved to corresponding files.",
                    button_text="OK",
                )
            )

    def _dump_account(self, f, file_format, account):
        """dump all keys of one account to a file"""
        coin = NETWORKS[self.network]["bip32"]
        derivations = [
            ('bip84', "p2wpkh", "m/84'/%d'/%d'" % (coin, account)),
            ('bip86', "p2tr", "m/86'/%d'/%d'" % (coin, account)),
            ('bip49', "p2sh-p2wpkh", "m/49'/%d'/%d'" % (coin, account)),
            ('bip44', "p2pkh", "m/44'/%d'/%d'" % (coin, account)),
            ('bip48_1', "p2sh-p2wsh", "m/48'/%d'/%d'/1'" % (coin, account)),
            ('bip48_2', "p2wsh", "m/48'/%d'/%d'/2'" % (coin, account)),
        ]
        fingerprint = hexlify(self.keystore.fingerprint).decode()

        if file_format == self.export_specter_diy:
            for keytype, scripttype, der in derivations:
                xpub = self.keystore.get_xpub(der)
                f.write("[%s/%s]%s\n" % (
                    fingerprint,
                    der.replace("m/", "").replace("'","h"),
                    xpub.to_base58(NETWORKS[self.network]["xpub"]),
                ))
        else:
            # coldcard generic json format
            m = self.keystore.get_xpub("m")
            data = {
                "xpub": m.to_base58(NETWORKS[self.network]["xpub"]),
                "xfp": fingerprint,
                "account": account,
                "chain": "BTC" if self.network == "main" else "XTN"
            }

            for keytype, scripttype, der in derivations:
                xpub = self.keystore.get_xpub(der)

                data[keytype] = {
                    "name": scripttype,
                    "deriv": der,
                    "xpub": xpub.to_base58(NETWORKS[self.network]["xpub"]),
                    "_pub": xpub.to_base58(
                        bip32.detect_version(
                            der,
                            default="xpub",
                            network=NETWORKS[self.network]
                        )
                    )
                }

            json.dump(data, f)

    async def process_host_command(self, stream, show_screen):
        if self.keystore.is_locked:
            raise AppError("Device is locked")
        # reads prefix from the stream (until first space)
        prefix = self.get_prefix(stream)
        # get device fingerprint, data is ignored
        if prefix == b"fingerprint":
            return BytesIO(hexlify(self.keystore.fingerprint)), {}
        # get xpub,
        # data: derivation path in human-readable form like m/44h/1h/0
        elif prefix == b"xpub":
            try:
                path = stream.read().strip()
                # convert to list of indexes
                path = bip32.parse_path(path.decode())
            except:
                raise AppError('Invalid path: "%s"' % path.decode())
            # get xpub
            xpub = self.keystore.get_xpub(bip32.path_to_str(path))
            # send back as base58
            return BytesIO(xpub.to_base58(NETWORKS[self.network]["xpub"]).encode()), {}
        raise AppError("Unknown command")

    async def show_xpub(self, derivation, show_screen):
        self.show_loader(title="Deriving the key...")
        derivation = derivation.rstrip("/")
        net = NETWORKS[self.network]
        xpub = self.keystore.get_xpub(derivation)
        ver = bip32.detect_version(derivation, default="xpub", network=net)
        canonical = xpub.to_base58(net["xpub"])
        slip132 = xpub.to_base58(ver)
        if slip132 == canonical:
            slip132 = None
        fingerprint = hexlify(self.keystore.fingerprint).decode()
        prefix = "[%s%s]" % (
            fingerprint,
            derivation[1:],
        )
        res = await show_screen(
            XPubScreen(xpub=canonical, slip132=slip132, prefix=prefix)
        )
        if res == XPubScreen.CREATE_WALLET:
            await self.create_wallet(derivation, canonical, prefix, show_screen)
        elif res:
            filename = "%s-%s.txt" % (fingerprint, derivation[2:].replace("/", "-"))
            with platform.sdcard as sd:
                with sd.open(filename, "w") as f:
                    f.write(res)
            await show_screen(
                Alert("Saved!",
                      "Extended public key is saved to the file:\n\n%s" % filename,
                      button_text="Close")
            )

    async def create_wallet(self, derivation, xpub, prefix, show_screen):
        """Shows a wallet-creation menu and passes a descriptor to the wallets app.

        The wallet type the user picks determines the derivation: standard
        wallets are always (re-)derived from the BIP purpose that matches their
        script type (see ``WALLET_TYPES``), so the descriptor key-origin and the
        actual signing key can never disagree. ``m/84' + tr()`` is only reachable
        by picking Taproot from an m/84' key and choosing recovery in the
        follow-up dialog.
        """
        net = NETWORKS[self.network]
        parsed = _parse_account_path(derivation)
        recommended = _PURPOSE_TO_TYPE.get(parsed[0]) if parsed else None

        buttons = []
        if recommended:
            buttons.append((None, "Recommended"))
            buttons.append((recommended, WALLET_TYPES[recommended][2]))
        buttons.append((None, "Other"))
        for key in WALLET_TYPES:
            if key != recommended:
                buttons.append((key, WALLET_TYPES[key][2]))

        menuitem = await show_screen(Menu(
            buttons, last=(255, None),
            title="Select wallet type to create",
        ))
        if menuitem == 255 or menuitem is None or menuitem not in WALLET_TYPES:
            return

        legacy = False

        # Repeating the historical "Single key -> Create Wallet -> Taproot" flow
        # ("Single key" is an m/84' key) must not silently create a legacy
        # wallet - offer an explicit standard/recovery choice instead. This is
        # also the only way to reach the legacy m/84' + tr() derivation.
        if menuitem == "taproot" and parsed is not None and parsed[0] == 84:
            choice = await show_screen(Menu(
                [
                    ("standard", "Standard Taproot\nm/86h (BIP86)"),
                    ("legacy", "Recover legacy Specter Taproot\nm/84h"),
                ],
                last=(255, None),
                title="Taproot derivation",
                note=("Standard Taproot wallets use BIP86 (m/86h). Older "
                      "Specter DIY versions could create Taproot wallets "
                      "using m/84h."),
            ))
            if choice == 255 or choice is None:
                return
            legacy = (choice == "legacy")

        if legacy:
            purpose, template, type_name = LEGACY_SPECTER_TAPROOT
            confirm = await show_screen(Prompt(
                "Legacy Specter Taproot",
                "This recreates a Taproot wallet from the non-standard m/84h "
                "derivation used by older Specter DIY firmware.\n\n"
                "Use it only to recover existing funds. New Taproot wallets "
                "must use BIP86 (m/86h).\n\nContinue?",
                warning="Recovery only - not for new wallets",
            ))
            if not confirm:
                return
            # Recovery must reproduce the displayed m/84' path *exactly*, incl.
            # a non-standard coin_type from a custom derivation - that is what
            # the older firmware signed with. `legacy` is only ever set when
            # `parsed` is a hardened m/84'/coin'/account' path.
            coin, account = parsed[1], parsed[2]
        else:
            purpose, template, type_name = WALLET_TYPES[menuitem]
            # Standard wallets follow the BIP44 structure: coin_type is fixed by
            # the active network (0 mainnet / 1 testnet), never taken from the
            # displayed key. Only the account index is carried over.
            coin = net["bip32"]
            account = parsed[2] if parsed is not None else self.account

        target = "m/%dh/%dh/%dh" % (purpose, coin, account)
        if _parse_account_path(target) == parsed:
            key_prefix, key_xpub = prefix, xpub
        else:
            self.show_loader(title="Deriving %s key..." % type_name)
            hdkey = self.keystore.get_xpub(target)
            key_xpub = hdkey.to_base58(net["xpub"])
            fingerprint = hexlify(self.keystore.fingerprint).decode()
            key_prefix = "[%s/%s]" % (fingerprint, target[2:])

        desc = template % (key_prefix, key_xpub)

        # get wallet names from the wallets app
        s, _ = await self.communicate(BytesIO(b"listwallets"), app="wallets")
        names = json.load(s)
        sel = "legacy_taproot" if legacy else menuitem
        name_bases = {
            "wpkh": "Native", "nested": "Nested", "legacy": "Legacy",
            "taproot": "Taproot", "legacy_taproot": "Legacy Taproot",
        }
        nn = "%s %d" % (name_bases.get(sel, "Wallet"), account)
        name_suggestion = nn
        i = 1
        # make sure we don't suggest an existing name
        while name_suggestion in names:
            name_suggestion = "%s (%d)" % (nn, i)
            i += 1
        name = await show_screen(InputScreen(title="Name your wallet",
                note="",
                suggestion=name_suggestion,
                min_length=1, strip=True
        ))
        if not name:
            return
        # add blinding key on liquid
        if is_liquid(self.network):
            desc = "blinded(slip77(%s),%s)" % (self.keystore.slip77_key, desc)
        data = "addwallet %s&%s" % (name, desc)
        await self.communicate(BytesIO(data.encode()), app="wallets")


    async def save_menu(self, show_screen):
        buttons = [(0, "Specter-DIY (plaintext)"), (1, "Cold Card (json)")]
        # wait for menu selection
        menuitem = await show_screen(Menu(buttons, last=(255, None),
                                          title="Select a format"))

        # process the menu button:
        # back button
        if menuitem == 255:
            return None
        elif menuitem == 0:
            return self.export_specter_diy
        elif menuitem == 1:
            return self.export_coldcard
        return None


    def wipe(self):
        # nothing to delete
        pass
