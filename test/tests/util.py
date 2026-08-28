import sys

from keystore.ram import RAMKeyStore
from app import BaseApp
from apps.wallets import App as WalletsApp
import platform

TEST_DIR = "testdir"


def describe_environment():
    """
    Returns the lines describing where this test run is actually
    executing. Several of these tests behave differently on the simulator
    than on a real device (no SD block device, no flash to overwrite, a
    /sd that is just a host directory), so a test log has to say which of
    the two produced it - a green run on the simulator does not prove the
    same code path works on hardware.
    """
    impl = getattr(sys, "implementation", None)
    runtime = getattr(impl, "name", "unknown")
    version = getattr(impl, "version", None)
    if version:
        runtime = "%s %s" % (
            runtime, ".".join(str(part) for part in version[:3])
        )
    if platform.simulator:
        target = "simulator (not real hardware)"
    else:
        target = "device hardware"
    try:
        build_type = platform.get_build_type()
    except Exception:
        build_type = "unknown"
    try:
        if platform.sdcard.has_block_device:
            sd = "real SD block device"
        else:
            sd = "no SD block device - /sd is a plain directory"
    except Exception:
        sd = "unknown"
    return [
        "runtime:     %s (sys.platform=%s)" % (runtime, sys.platform),
        "target:      %s" % target,
        "build type:  %s" % build_type,
        "sd card:     %s" % sd,
        "storage:     %r" % getattr(platform.config, "storage_root", "?"),
    ]


def print_environment():
    """Prints describe_environment() as a banner above the test output."""
    print("=" * 60)
    print("test environment")
    for line in describe_environment():
        print("  " + line)
    print("=" * 60)

def check_sigs(psbt1, psbt2):
    return [inp.partial_sigs for inp in psbt1.inputs] == [inp.partial_sigs for inp in psbt2.inputs]

def clear_testdir():
    try:
        platform.delete_recursively(TEST_DIR, include_self=True)
    except:
        pass

def show_loader(*args, **kwargs):
    """Dummy show_loader function that does nothing"""
    pass

async def show(*args, **kwargs):
    """Dummy show function that always cancels (returns None)"""
    return None

async def communicate(*args, **kwargs):
    """Dummy cross-app comunicate function that always cancels"""
    return None

def get_keystore(mnemonic="ability "*11+"acid", password=""):
    """Returns a dummy keystore"""
    platform.maybe_mkdir(TEST_DIR)
    platform.maybe_mkdir(TEST_DIR+"/keystore")
    ks = RAMKeyStore()
    ks.path = TEST_DIR+"/keystore"
    ks.show_loader = show_loader
    ks.show = show
    ks.load_secret(ks.path)
    ks.initialized = True
    ks._unlock("1234")
    ks.set_mnemonic(mnemonic, password)
    return ks

def get_wallets_app(keystore, network):
    platform.maybe_mkdir(TEST_DIR)
    platform.maybe_mkdir(TEST_DIR+"/wallets")
    platform.maybe_mkdir(TEST_DIR+"/tmp")
    BaseApp.tempdir = TEST_DIR+"/tmp"
    wapp = WalletsApp(TEST_DIR+"/wallets")
    wapp.init(keystore, network, show_loader, communicate)
    return wapp
