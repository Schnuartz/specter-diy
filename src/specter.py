import sys
import gc
import json
from io import BytesIO
import asyncio

from platform import (
    CriticalErrorWipeImmediately,
    reboot,
    maybe_mkdir,
    wipe,
    get_version,
    get_git_info,
    get_battery_status,
    get_build_type,
    get_firmware_boot_mode,
    get_flash_read_protection_status,
    get_flash_write_protection_status,
)
from hosts import Host, HostError
from app import BaseApp
from embit import bip39
from embit.liquid.networks import NETWORKS
from gui.screens.settings import HostSettings, SettingsMenu
from gui.screens.mnemonic import MnemonicPrompt

# small helper functions
from helpers import gen_mnemonic, fix_mnemonic
from errors import BaseError


class SpecterError(BaseError):
    NAME = "Specter error"


class Specter:
    """Specter class.
    Call .start() method to register in the event loop
    It will then call the .setup() and .main() functions to display the GUI
    """
    SETTINGS_DIR = None
    # global settings
    GLOBAL = {}

    def __init__(self, gui, keystores, hosts, apps, settings_path, network="main"):
        # so hosts can call methods of Specter
        Host.parent = self
        self.hosts = hosts
        self.keystores = keystores
        self.keystore = None
        if len(keystores) == 1:
            # instantiate the keystore class
            self.keystore = keystores[0]()
        self.network = network
        self.gui = gui
        self.path = settings_path
        self.current_menu = self.initmenu
        self.dev = False
        self.apps = apps

    def _firmware_note(self, include_details=False):
        primary_note = "Firmware version %s" % get_version()

        if not include_details:
            return primary_note

        sections = [primary_note]

        repo, branch, commit = get_git_info()
        repo_details = []
        if repo != "unknown":
            repo_details.append("Repo: %s" % repo)
        if branch != "unknown":
            repo_details.append("Branch: %s" % branch)
        if commit != "unknown":
            repo_details.append("Commit: %s" % commit)
        if repo_details:
            sections.append("\n".join(repo_details))

        def _format_status(value):
            if isinstance(value, str) and value:
                return value[0].upper() + value[1:]
            return value

        boot_mode = get_firmware_boot_mode()
        if boot_mode != "unknown":
            boot_mode_note = "Firmware mode: %s" % _format_status(boot_mode)
        else:
            boot_mode_note = "Firmware mode: Unknown"
        sections.append(boot_mode_note)

        read_protect = get_flash_read_protection_status()
        if read_protect != "unknown":
            read_note = "Read protection: %s" % _format_status(read_protect)
        else:
            read_note = "Read protection: Unknown"
        sections.append(read_note)

        write_protect = get_flash_write_protection_status()
        if write_protect != "unknown":
            write_note = "Write protection: %s" % _format_status(write_protect)
        else:
            write_note = "Write protection: Unknown"
        sections.append(write_note)

        build_type = get_build_type()
        if build_type == "unknown":
            build_note = "Build type: Unknown"
        else:
            build_note = "Build type: %s" % _format_status(build_type)
        sections.append(build_note)

        return "\n\n".join(sections)

    def start(self):
        # register battery monitor (runs every 3 seconds)
        self.gui.set_battery_callback(get_battery_status, 3000)
        # start the GUI
        self.gui.start()
        # register coroutines for all hosts
        for host in self.hosts:
            host.start(self)
        asyncio.run(self.setup())

    async def handle_exception(self, exception, next_fn):
        """
        Handle exception, show proper error message
        and return next function to call and await
        """
        self.gui.hide_loader()
        try:
            raise exception
        except CriticalErrorWipeImmediately as e:
            # show error
            await self.gui.error(
                "Critical error, the device will be wiped.\n\n%s" % e,
                button_text="Wipe Specter Device",
            )
            self.gui.show_loader(title="Wiping the device...")
            # wipe everything and reboot
            self.wipe()
        # catch an expected error
        except BaseError as e:
            # show error
            requires_card_removal = getattr(e, "requires_card_removal", False)
            button_text = "Remove card" if requires_card_removal else "OK"
            await self.gui.alert(e.NAME, "%s" % e, button_text=button_text)
            if requires_card_removal:
                wait_for_removal = getattr(self.keystore, "wait_for_card_removal", None)
                if wait_for_removal is not None:
                    self.gui.show_loader(
                        title="Remove the locked card",
                        text="Waiting for the smartcard to be removed...",
                    )
                    await wait_for_removal()
                    self.gui.hide_loader()
                    self.keystore = None
            # restart
            return next_fn
        # show trace for unexpected errors
        except Exception as e:
            print(e)
            b = BytesIO()
            sys.print_exception(e, b)
            errmsg = "Something unexpected happened...\n\n"
            errmsg += b.getvalue().decode()
            await self.gui.error(errmsg)
            # restart
            return next_fn

    async def select_keystore(self):
        # if we have fixed keystore - just use it
        if len(self.keystores) == 1:
            self.keystore = self.keystores[0]()
            return
        # checking the first available keystore
        keystore_cls = None
        # TODO: show some screen here if none are available
        while keystore_cls is None:
            for keystore in self.keystores:
                if keystore.is_available():
                    keystore_cls = keystore
                    break
            # if none are available just wait for it
            # if keystore_cls is None:
            #     await asyncio.sleep_ms(50)
        self.keystore = keystore_cls()

    async def setup(self):
        try:
            # check if the user already selected the keystore class
            if self.keystore is None:
                await self.select_keystore()

            if self.keystore is not None:
                self.load_network(self.path, self.network)

            # load secrets
            await self.keystore.init(self.gui.show_screen(), self.gui.show_loader)
            # unlock with PIN or set up the PIN code
            await self.unlock()
        except Exception as e:
            next_fn = await self.handle_exception(e, self.setup)
            await next_fn()

        await self.main()

    async def host_exception_handler(self, e):
        try:
            raise e
        except HostError as ex:
            msg = "%s" % ex
        except:
            b = BytesIO()
            sys.print_exception(e, b)
            msg = b.getvalue().decode()
        await self.gui.error(msg, popup=True)

    async def main(self):
        while True:
            try:
                # trigger garbage collector
                gc.collect()
                # show init menu and wait for the next menu
                # any menu returns next menu or
                # None if the same menu should be used
                next_menu = await self.current_menu()
                if next_menu is not None:
                    self.current_menu = next_menu

            except Exception as e:
                next_fn = await self.handle_exception(e, self.setup)
                await next_fn()

    def init_apps(self):
        for app in self.apps:
            app.specter = self
            app.init(self.keystore, self.network, self.gui.show_loader, self.cross_app_communicate)

    def _interface_status_note(self):
        """Compact status line for the playground-style settings landing page."""
        status = []
        for host in self.hosts:
            if host.button is None:
                continue
            state = "on" if host.is_enabled else "off"
            status.append("%s %s" % (host.button, state))
        if hasattr(self.keystore, "connection"):
            try:
                card = ("Smartcard inserted" if self.keystore.connection.isCardInserted()
                        else "Smartcard not inserted")
            except Exception:
                card = "Smartcard unavailable"
            status.append(card)
        return "Interfaces: " + "  |  ".join(status) if status else "Interfaces"

    def _settings_interface_status(self):
        """Return compact status entries for the playground-style status card."""
        entries = []
        seen = set()
        names = {
            "QR scanner": "QR",
            "USB communication": "USB",
            "SD card": "SD",
        }
        for host in self.hosts:
            name = names.get(host.settings_button)
            if name is None or name in seen:
                continue
            seen.add(name)
            active = host.is_enabled
            if name == "SD":
                try:
                    active = active and __import__("platform").sdcard.is_present
                except Exception:
                    pass
            entries.append((name, active))
        if hasattr(self.keystore, "connection"):
            try:
                active = self.keystore.connection.isCardInserted()
            except Exception:
                active = False
            entries.append(("SC", active))
        return entries

    async def cross_app_communicate(self, stream, app:str=None, show_fn=None):
        if app == "": # root
            data = stream.read()
            if data.startswith(b"set_mnemonic "):
                mnemonic = data[len("set_mnemonic "):].decode()
                confirm = await self.gui.prompt(
                        "Load new mnemonic?",
                        "\nApp requested to load new mnemonic\n"
                        "Do you want to continue?\n\n"
                        "You will need to reboot the device to get back"
                        " to your current mnemonic.",
                )
                if confirm:
                    return self.set_mnemonic(mnemonic)
                else:
                    return True
            raise SpecterError("Invalid command '%s'" % data)
        return await self.process_host_request(stream, popup=False, appname=app, show_fn=show_fn)

    async def initmenu(self):
        # only enable passive hosts
        for host in self.hosts:
            if host.button:
                await host.enable()
        # for every button we use an ID
        # to avoid mistakes when editing strings
        # If ID is None - it is a section title, not a button
        buttons = [
            # id, text
            (None, "Key management"),
            (0, "Generate new key"),
            (1, "Enter recovery phrase"),
            (777, "Import recovery phrase"),
        ]
        if self.keystore.is_key_saved and self.keystore.load_button:
            buttons.append((2, self.keystore.load_button))
        buttons += [(None, "Settings"), (3, "Device settings")]
        # wait for menu selection
        menuitem = await self.gui.menu(
            buttons,
            title="Home",
            warning=self.keystore.temporary_seed,
            warning_value=778,
        )

        # process the menu button:
        if menuitem == 0:
            mnemonic = await self.gui.new_mnemonic(gen_mnemonic, bip39.WORDLIST, fix_mnemonic)
            if mnemonic is not None:
                return await self._activate_generated_seed(mnemonic)
        # recover
        elif menuitem == 1:
            mnemonic = await self.gui.recover(
                bip39.mnemonic_is_valid, bip39.find_candidates, fix_mnemonic
            )
            if mnemonic is not None:
                # load keys using mnemonic and empty password
                return self.set_mnemonic(mnemonic, "")
        elif menuitem == 2:
            # try to load key, if user cancels -> return
            res = await self.keystore.load_mnemonic()
            if not res:
                return
            await self.gui.alert("Success!", "Key is loaded!")
            self.init_apps()
            return self.mainmenu
        elif menuitem == 3:
            await self.update_devsettings()
        elif menuitem == 777:
            return await self.import_mnemonic()
        elif menuitem == 778:
            await self.gui.alert(
                "Temporary seed mode",
                "The green exclamation mark means this seed is loaded in memory only.\n\n"
                "It is not a persistent seed file and will be cleared when the device restarts.",
            )
            return self.mainmenu
        # lock device
        elif menuitem == 5:
            await self.lock()
            # go to PIN setup screen
            await self.unlock()
        else:
            print(menuitem, "menu is not implemented yet")
            raise SpecterError("Not implemented")

    async def import_mnemonic(self):
        host = await self.gui.menu(title="What to use for import?", note="\n",
            buttons=[(host, host.button) for host in self.hosts if host.is_enabled],
            last=(255, None))
        if host == 255:
            return
        stream = await host.get_data()
        if not stream:
            return
        data = stream.read()
        # digital mnemonic
        if len(data) >= 4*12 and len(data) <= 4*24 and len(data) % 12 == 0 and (b" " not in data):
            mnemonic = " ".join([bip39.WORDLIST[int(data[4*i:4*i+4])] for i in range(len(data)//4)])
        # binary mnemonic
        elif len(data) >= 16 and len(data) <= 32:
            mnemonic = bip39.mnemonic_from_bytes(data)
        # text mnemonic
        else:
            mnemonic = data.decode()
            # split on \n and \r to avoid double-scan
            mnemonic = mnemonic.split("\r")[0].split("\n")[0]
            if not bip39.mnemonic_is_valid(mnemonic):
                raise SpecterError("Invalid data: %r" % mnemonic)
        scr = MnemonicPrompt(title="Imported mnemonic:", mnemonic=mnemonic)
        # confirm mnemonic
        if not await self.gui.show_screen()(scr):
            return
        return self.set_mnemonic(mnemonic, "")

    async def _activate_generated_seed(self, mnemonic):
        """Choose persistence policy immediately after creating a seed."""
        mode = await self.gui.menu(
            [
                (None, "Seed storage mode"),
                ("save", "Save seed files"),
                ("temporary", "Temporary seed mode", True, 0x123D2A),
            ],
            title="How should this seed be used?",
            note="Would you like to save the seed files or use temporary seed mode?",
            last=(255, None),
        )
        if mode == 255:
            return self.mainmenu

        self.set_mnemonic(mnemonic, "", temporary=(mode == "temporary"))
        if mode == "temporary":
            await self.gui.alert(
                "Temporary seed mode",
                "This recovery phrase is loaded in memory only.\n\n"
                "It will be shown in green with a green exclamation mark and "
                "will not survive a reboot.",
            )
            return self.mainmenu

        await self._save_generated_seed()
        return self.mainmenu

    async def _save_generated_seed(self):
        """Show the recommended storage hierarchy and explain each target."""
        target = await self.gui.menu(
            [
                (None, "Recommend Smartcard"),
                ("smartcard", "Smartcard"),
                (None, "Not Rec"),
                ("sd", "SD Card"),
                (None, "Advanced"),
                ("device", "Device storage"),
            ],
            title="Save seed files",
            note="Smartcard is recommended for storing a recovery phrase.",
            last=(255, None),
        )
        if target == 255:
            return

        explanations = {
            "smartcard": (
                "Smartcard (recommended)",
                "The seed is stored on a PIN-protected Smartcard and is not "
                "left as a seed file on the device."
            ),
            "sd": (
                "SD Card — Not Rec",
                "The seed is stored as an encrypted file on the SD Card. "
                "Anyone who gets the card can still attempt to attack the file; "
                "Smartcard storage is recommended."
            ),
            "device": (
                "Device storage — Advanced",
                "The seed is stored in the device's internal encrypted storage. "
                "This is convenient, but it ties the backup to this device; "
                "Smartcard storage is recommended."
            ),
        }
        title, message = explanations[target]
        if not await self.gui.prompt(title, message + "\n\nContinue?"):
            return

        try:
            if target == "smartcard":
                if not hasattr(self.keystore, "save_mnemonic") or self.keystore.NAME.lower() != "smartcard":
                    await self.gui.alert(
                        "Smartcard unavailable",
                        "Insert and select a Smartcard keystore before saving to a Smartcard.",
                    )
                    return
                saved = await self.keystore.save_mnemonic()
            else:
                path = (
                    self.keystore.sdpath
                    if target == "sd" and hasattr(self.keystore, "sdpath")
                    else self.keystore.flashpath
                    if hasattr(self.keystore, "flashpath") else None
                )
                if path is None:
                    raise SpecterError("Selected storage is not available")
                if target == "sd" and not getattr(__import__("platform"), "sdcard").is_present:
                    raise SpecterError("Please insert an SD card")
                saved = await self.keystore.save_mnemonic_to(path)
            if saved:
                self.keystore.temporary_seed = False
        except Exception as e:
            await self.gui.alert("Seed was not saved", "%s" % e)

    def set_mnemonic(self, mnemonic, password="", temporary=False):
        self.keystore.set_mnemonic(
            mnemonic.strip(), password, temporary=temporary
        )
        self.keystore.temporary_seed = temporary
        self.init_apps()
        self.current_menu = self.mainmenu
        return self.mainmenu

    async def mainmenu(self):
        # interactive hosts are enabled later
        for host in self.hosts:
            if not host.button:
                await host.enable()
        # buttons defined by host classes
        # only added if there is a GUI-triggered communication
        host_buttons = [
            (host, host.button) for host in self.hosts if host.button is not None and host.is_enabled
        ]
        # buttons defined by app classes
        app_buttons = [(app, app.button) for app in self.apps if app.button is not None]
        # for every button we use an ID
        # to avoid mistakes when editing strings
        # If ID is None - it is a section title, not a button
        buttons = (
            [
                # id, text
                (None, "Applications")
            ]
            + app_buttons
            + [(None, "Communication")]
            + host_buttons
            + [(None, "More")]  # delimiter
        )
        if hasattr(self.keystore, "lock"):
            buttons += [(2, "Lock device")]
        buttons += [(3, "Settings")]
        # wait for menu selection
        menuitem = await self.gui.menu(buttons)

        # process the menu button:
        # lock device
        if menuitem == 2 and hasattr(self.keystore, "lock"):
            await self.lock()
            # go to the unlock screen
            await self.unlock()
        elif menuitem == 3:
            return await self.settingsmenu()
        elif isinstance(menuitem, BaseApp) and hasattr(menuitem, "menu"):
            app = menuitem
            # stay in this menu while something is returned
            while await app.menu(self.gui.show_screen()):
                pass
        # if it's a host
        elif isinstance(menuitem, Host) and hasattr(menuitem, "get_data"):
            host = menuitem
            stream = await host.get_data()
            # probably user cancelled
            if stream is not None:
                # check against all apps
                res = await self.process_host_request(stream, popup=False)
                if res not in [True, False, None]:
                    await host.send_data(*res)
        else:
            print(menuitem)
            raise SpecterError("Not implemented")

    async def settingsmenu(self):
        try:
            battery_available = get_battery_status()[0] is not None
        except Exception:
            battery_available = False
        has_sd = any(host.settings_button == "SD card" for host in self.hosts)
        has_smartcard = hasattr(self.keystore, "connection")
        menuitem = await self.gui.show_screen()(SettingsMenu(
            self._settings_interface_status(),
            has_sd=has_sd,
            has_smartcard=has_smartcard,
            battery_available=battery_available,
            can_lock=hasattr(self.keystore, "lock"),
        ))

        # process the menu button:
        # back button
        if menuitem == 255:
            return self.mainmenu
        elif menuitem == 0:
            await self.security_settings()
        elif menuitem == 1:
            if self.keystore.storage_button is None:
                await self.gui.alert("Manage Storage", "No removable seed storage is available.")
                res = False
            else:
                res = await self.keystore.storage_menu()
            # storage_menu returns True if app reinit is required
            if res:
                self.init_apps()
        elif menuitem == 2:
            await self.gui.alert(
                "Theme",
                "Theme selection is not available in this firmware build.",
            )
        elif menuitem == 3:
            await self.gui.alert(
                "Language",
                "Language selection is not available in this firmware build.",
            )
        elif menuitem == 4:
            await self.communication_settings()
        elif menuitem == 8:
            await self.lock()
            await self.unlock()
            return self.mainmenu
        else:
            print(menuitem)
            raise SpecterError("Not implemented")
        return self.settingsmenu

    async def security_settings(self):
        buttons = [(None, "Security Settings")]
        if hasattr(self.keystore, "change_pin"):
            buttons.append((1, "Change PIN code"))
        if hasattr(self.keystore, "show_mnemonic"):
            buttons.append((2, "Show recovery phrase"))
        buttons.append((3, "Lock device"))
        choice = await self.gui.menu(buttons, last=(255, None))
        if choice == 1:
            await self.keystore.change_pin()
        elif choice == 2:
            await self.keystore.show_mnemonic()
        elif choice == 3:
            await self.lock()

    async def preferences_settings(self):
        net = NETWORKS[self.network]["name"]
        choice = await self.gui.menu(
            [
                (None, "Manage Preferences"),
                (1, "Switch network (%s)" % net),
                (2, "Device settings"),
                (3, "Communication settings"),
            ],
            last=(255, None),
        )
        if choice == 1:
            await self.select_network()
        elif choice == 2:
            await self.update_devsettings()
        elif choice == 3:
            await self.communication_settings()

    async def select_network(self):
        buttons = [
            (None, "Production"),
            ("main", "Bitcoin Mainnet"),
            ("liquidv1", "Liquid Mainnet"),
            (None, "Testnets"),
            ("test", "Testnet"),
            ("signet", "Signet"),
            ("regtest", "Regtest"),
            ("liquidtestnet", "Liquid Testnet"),
            ("elementsregtest", "Liquid Regtest"),
        ]
        # wait for menu selection
        menuitem = await self.gui.menu(buttons, last=(255, None))
        if menuitem != 255:
            self.set_network(menuitem)

    def set_network(self, net):
        if net not in NETWORKS:
            net = 'main'
        self.network = net
        self.gui.set_network(net)
        # save
        with open(self.path + "/network", "w") as f:
            f.write(net)
        if self.keystore.is_ready:
            # load wallets for this network
            self.init_apps()

    def load_network(self, path, network="main"):
        try:
            with open(path + "/network", "r") as f:
                network = f.read()
        except:
            pass
        self.set_network(network)

    async def show_about(self):
        await self.gui.alert(
            "About this device",
            self._firmware_note(include_details=True),
            button_text="Close",
        )

    async def communication_settings(self):
        buttons = [
            (None, "Communication channels")
        ] + [
            (host, host.settings_button)
            for host in self.hosts
            if host.settings_button is not None
        ]
        while True:
            menuitem = await self.gui.menu(buttons,
                                      title="Communication settings",
                                      note=self._firmware_note(),
                                      last=(255, None)
            )
            if menuitem == 255:
                return
            elif isinstance(menuitem, Host):
                reboot_required = await menuitem.settings_menu(self.gui.show_screen(), self.keystore)
                if reboot_required:
                    if await self.gui.prompt(
                        "Reboot required!",
                        "Settings have been updated and will become active after reboot.\n\n"
                        "Do you want to reboot now?",
                    ):
                        reboot()
            else:
                print(menuitem)
                raise SpecterError("Not implemented")

    @property
    def settings_fname(self):
        return self.SETTINGS_DIR+"/global.settings"

    def load_settings(self, fname=None):
        settings = {}
        try:
            if fname is None:
                fname = self.settings_fname
            adata, _ = self.keystore.load_aead(fname, key=self.keystore.settings_key)
            settings = json.loads(adata.decode())
        except Exception as e:
            print(e)
        return settings

    def save_settings(self, settings, fname=None):
        maybe_mkdir(self.SETTINGS_DIR)
        if fname is None:
            fname = self.settings_fname
        self.keystore.save_aead(fname,
                           adata=json.dumps(settings).encode(),
                           key=self.keystore.settings_key
        )

    async def experimental_settings(self):

        controls = [{
            "label": "Taproot",
            "hint": "Taproot support only for single-key wallets\nwithout tap script trees",
            "value": self.GLOBAL.get("experimental", {}).get("taproot", False)
        }]

        scr = HostSettings(
            controls,
            title="Experimental features",
            note="Experimental features are unstable,\n"
            "only enable them if you really want to try.\n"
            "Report developers in case of any issues.",
        )
        res = await self.gui.show_screen()(scr)
        if res is None:
            return
        taproot, *_ = res
        # for now only experimental, can be extended
        settings = {
            "experimental": {
                "taproot": taproot,
            }
        }
        self.GLOBAL = settings
        BaseApp.GLOBAL = settings
        self.save_settings(settings)

    async def update_devsettings(self):
        buttons = [
            (None, "Categories")
        ] + [
            (1, "Communication"),
            # (2, "Applications"),
            # (3, "Experimental"),
        ] + [
            (None, "Global settings"),
            (42, "About this device"),
        ]
        if hasattr(self.keystore, "lock"):
            buttons.extend([(777, "Change PIN code")])
        buttons += [
            (456, "Reboot"),
            (123, "Wipe the device", True, 0x951E2D),
        ]
        while True:
            menuitem = await self.gui.menu(buttons,
                                      title="Device settings",
                                      note=self._firmware_note(),
                                      last=(255, None)
            )
            if menuitem == 255:
                return
            elif menuitem == 3:
                await self.experimental_settings()
            elif menuitem == 456:
                if await self.gui.prompt(
                    "Reboot the device?",
                    "\n\nAre you sure?",
                ):
                    reboot()
                return
            # WIPE
            elif menuitem == 123:
                if await self.gui.prompt(
                    "Wiping the device will erase everything in the internal storage!",
                    "This includes multisig wallet files, keys, apps data etc.\n\n"
                    "But it doesn't include files stored on SD card or smartcard.\n\n"
                    "Are you sure?",
                ):
                    self.wipe()
                return
            elif menuitem == 777:
                await self.keystore.change_pin()
                return
            elif menuitem == 42:
                await self.show_about()
                return
            elif menuitem == 1:
                await self.communication_settings()
            else:
                print(menuitem)
                raise SpecterError("Not implemented")

    @property
    def fingerprint(self):
        return self.keystore.fingerprint

    def wipe(self):
        # TODO: wipe the smartcard as well?
        # platform.wipe
        wipe()

    async def lock(self):
        # lock the keystore
        if hasattr(self.keystore, "lock"):
            self.keystore.lock()
        # disable hosts
        for host in self.hosts:
            await host.disable()

    async def unlock(self):
        """
        - setup PIN if not set
        - enter PIN if set
        """
        await self.keystore.unlock()
        # now keystore is unlocked - we can load hosts configs
        for host in self.hosts:
            host.load_settings(self.keystore)
        settings = self.load_settings()
        self.GLOBAL = settings
        BaseApp.GLOBAL = settings

    async def maybe_import_mnemonic(self, stream, popup=False, show_fn=None):
        if show_fn is None:
            show_fn = self.gui.show_screen(popup)
        data = stream.read(240) # one word is at most 8 chars, so total len is < 240 even if it has prefix of some kind (for future)
        mnemonic_type = ""
        # digital mnemonic
        d = data.strip()
        if len(d) >= 4*12 and len(d) <= 4*24 and len(d) % 12 == 0 and (b" " not in d):
            mnemonic = " ".join([bip39.WORDLIST[int(d[4*i:4*i+4])] for i in range(len(d)//4)])
            mnemonic_type = "digital"
        # binary mnemonic
        elif len(data) >= 16 and len(data) <= 32:
            mnemonic = bip39.mnemonic_from_bytes(data)
            mnemonic_type = "binary"
        # text mnemonic
        else:
            mnemonic = data.decode()
            # split on \n and \r to avoid double-scan
            mnemonic = mnemonic.split("\r")[0].split("\n")[0]
            if not bip39.mnemonic_is_valid(mnemonic):
                raise SpecterError("Invalid data: %r" % mnemonic)
            mnemonic_type = "text"
        scr = MnemonicPrompt(title="Imported mnemonic:", mnemonic=mnemonic, note="Data looks like a %s mnemonic.\nDo you want to use it?" % mnemonic_type)
        # confirm mnemonic
        if not await show_fn(scr):
            return
        self.set_mnemonic(mnemonic, "")

    async def process_host_request(self, stream, popup=True, appname=None, show_fn=None):
        """
        This method is called whenever we got data from the host.
        It tries to find a proper app and pass the stream with data to it.
        """
        self.gui.show_loader(title="Processing host data...")
        res = None
        if show_fn is None:
            show_fn = self.gui.show_screen(popup)
        try:
            matching_apps = []
            if appname is not None:
                for app in self.apps:
                    if app.name == appname:
                        matching_apps.append(app)
            else:
                for app in self.apps:
                    stream.seek(0)
                    # check if the app can process this stream
                    if app.can_process(stream):
                        matching_apps.append(app)
            if len(matching_apps) == 0:
                stream.seek(0)
                try:
                    await self.maybe_import_mnemonic(stream, popup, show_fn)
                    return
                except Exception as e:
                    print(e)
                    raise HostError("Can't find matching app for this request:\n\n %r" % stream.read(100))
            # TODO: if more than one - ask which one to use
            if len(matching_apps) > 1:
                raise HostError(
                    "Not sure what app to use...\n\nThere are %d" % len(matching_apps)
                )
            stream.seek(0)
            app = matching_apps[0]
            res = await app.process_host_command(stream, show_fn)
        except Exception as e:
            if isinstance(e, BaseError):
                # error that has a meaningfull message, will be sent to the host
                raise HostError(str(e))
            else:
                # converted to "unknown error" on the host
                raise e
        finally:
            self.gui.hide_loader()
        return res
