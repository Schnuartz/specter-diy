#!/usr/bin/env python3
"""Apply narrow WebAssembly ABI/GC fixes to the pinned simulator submodules.

This deliberately does not edit Specter's application Python. Source patterns are
checked so an upstream revision cannot silently receive a partial patch.
"""
from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve()


def replace(path, old, new, expected):
    target = source / path
    data = target.read_text()
    count = data.count(old)
    if count == 0 and data.count(new) >= expected:
        return
    if count != expected:
        raise RuntimeError(f"Unexpected source at {target}: {count} matches, expected {expected}")
    target.write_text(data.replace(old, new))


# This older MicroPython uses an unsigned sizeof expression as an iterator
# slot count. Expressions such as sp[-MP_OBJ_ITER_BUF_NSLOTS + 1] consequently
# become huge positive offsets in wasm instead of indexing backwards.
replace(
    "f469-disco/micropython/py/obj.h",
    "#define MP_OBJ_ITER_BUF_NSLOTS ((sizeof(mp_obj_iter_buf_t) + sizeof(mp_obj_t) - 1) / sizeof(mp_obj_t))",
    "#define MP_OBJ_ITER_BUF_NSLOTS ((int)((sizeof(mp_obj_iter_buf_t) + sizeof(mp_obj_t) - 1) / sizeof(mp_obj_t)))",
    1,
)

# Specter's asyncio GUI calls udisplay.update() itself. In a dedicated worker
# that loop intentionally does not return to the JS event loop, so Emscripten's
# scheduled SDL refresh callback cannot run. Present LVGL's own framebuffer at
# that existing display update seam instead.
display_file = source / "f469-disco/usermods/udisplay_f469/display_unix.c"
display_source = display_file.read_text()
display_old = "    lv_task_handler();\n    return mp_const_none;"
display_new = """    lv_task_handler();
#ifdef __EMSCRIPTEN__
    monitor_sdl_refr_core();
    emscripten_sleep(1);
#endif
    return mp_const_none;"""
if display_old in display_source:
    display_source = display_source.replace(display_old, display_new, 1)
elif display_new not in display_source:
    raise RuntimeError(f"Unexpected display source at {display_file}")
include = '#ifdef __EMSCRIPTEN__\n#include "lv_sdl_hal/SDL/SDL_monitor.h"\n#endif\n'
include = '#ifdef __EMSCRIPTEN__\n#include "lv_sdl_hal/SDL/SDL_monitor.h"\n#include <emscripten.h>\n#endif\n'
if include not in display_source:
    display_source = display_source.replace('#include "lvgl.h"\n', '#include "lvgl.h"\n' + include, 1)
display_file.write_text(display_source)

# Feed LVGL's existing SDL mouse driver directly from normalized browser
# pointer coordinates. This does not synthesize wallet navigation in HTML.
mouse_file = source / "f469-disco/usermods/udisplay_f469/lv_sdl_hal/SDL/SDL_mouse.c"
mouse_source = mouse_file.read_text()
mouse_anchor = "/**\n * It will be called from the main SDL thread\n */"
mouse_bridge = """#ifdef __EMSCRIPTEN__
#include <emscripten.h>
EMSCRIPTEN_KEEPALIVE void browser_pointer(int x, int y, int down) {
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x >= MONITOR_HOR_RES) x = MONITOR_HOR_RES - 1;
    if (y >= MONITOR_VER_RES) y = MONITOR_VER_RES - 1;
    last_x = x;
    last_y = y;
    if (down == 1) {
        left_button_down = true;
        first_x = x;
        first_y = y;
        mouse_was_read = false;
    } else if (down == 0) {
        left_button_down = false;
        if (!mouse_was_read) cached_clicks += 2;
    }
}
#endif

"""
if mouse_bridge not in mouse_source:
    if mouse_anchor not in mouse_source:
        raise RuntimeError(f"Unexpected mouse source at {mouse_file}")
    mouse_file.write_text(mouse_source.replace(mouse_anchor, mouse_bridge + mouse_anchor, 1))

# Safari/WebKit currently has no OffscreenCanvas in some releases. Keep the
# same LVGL framebuffer and worker execution, but stream dirty pixels to the
# page Canvas when SDL cannot acquire an OffscreenCanvas in the worker.
monitor_file = source / "f469-disco/usermods/udisplay_f469/lv_sdl_hal/SDL/SDL_monitor.c"
monitor_source = monitor_file.read_text()
monitor_bridge = """#ifdef MONITOR_EMSCRIPTEN
#include <emscripten.h>
EM_JS(int, browser_headless_display, (), {
    return Module['headlessDisplay'] ? 1 : 0;
});
EM_JS(void, browser_emit_frame, (const uint32_t *frame), {
    var ptr = frame >>> 0;
    var pixels = HEAPU8.slice(ptr, ptr + 480 * 800 * 4);
    postMessage({type: 'frame', pixels: pixels}, [pixels.buffer]);
});
static double browser_last_frame = 0;
#endif

"""
monitor_anchor = "static int quit_filter(void * userdata, SDL_Event * event);"
if monitor_bridge not in monitor_source:
    if monitor_anchor not in monitor_source:
        raise RuntimeError(f"Unexpected monitor source at {monitor_file}")
    monitor_source = monitor_source.replace(monitor_anchor, monitor_bridge + monitor_anchor, 1)
headless_init = """#ifdef MONITOR_EMSCRIPTEN
    if (browser_headless_display()) {
        memset(tft_fb, 77, MONITOR_HOR_RES * MONITOR_VER_RES * sizeof(uint32_t));
        sdl_refr_qry = true;
        sdl_inited = true;
        return;
    }
#endif

"""
if headless_init not in monitor_source:
    monitor_source = monitor_source.replace("    /*Initialize the SDL*/", headless_init + "    /*Initialize the SDL*/", 1)
headless_deinit = """#ifdef MONITOR_EMSCRIPTEN
    if (browser_headless_display()) {
        sdl_inited = false;
        return;
    }
#endif
"""
if headless_deinit not in monitor_source:
    monitor_source = monitor_source.replace("    sdl_quit_qry = true;\n    SDL_DestroyTexture(texture);",
                                            "    sdl_quit_qry = true;\n" + headless_deinit + "    SDL_DestroyTexture(texture);", 1)
headless_refresh = """#ifdef MONITOR_EMSCRIPTEN
    if (browser_headless_display()) {
        if (sdl_refr_qry && emscripten_get_now() - browser_last_frame >= 50) {
            sdl_refr_qry = false;
            browser_last_frame = emscripten_get_now();
            browser_emit_frame(tft_fb);
        }
        return;
    }
#endif
"""
if headless_refresh not in monitor_source:
    monitor_source = monitor_source.replace("void monitor_sdl_refr_core(void)\n{\n",
                                            "void monitor_sdl_refr_core(void)\n{\n" + headless_refresh, 1)
monitor_file.write_text(monitor_source)


# The pinned C extensions declared MicroPython varargs callbacks with mp_uint_t;
# the runtime typedef is size_t. Clang rejects the mismatched function pointers.
replace(
    "f469-disco/usermods/secp256k1/mpy/libsecp256k1.c",
    "mp_uint_t n_args", "size_t n_args", 15,
)
replace(
    "f469-disco/usermods/uhashlib/hashlib.c",
    "mp_uint_t n_args", "size_t n_args", 1,
)

# On 32-bit wasm the custom rangeproof header's uint64_t length disagrees
# with its intptr_t implementation, which would otherwise link with a wasm
# function-signature mismatch and trap if invoked.
replace(
    "f469-disco/usermods/secp256k1/mpy/config/rangeproof_preallocated/rangeproof_preallocated.h",
    "void * preallocated_ptr, uint64_t allocated_len);",
    "void * preallocated_ptr, intptr_t allocated_len);", 2,
)

# Specter requires ucryptolib AES, while the networked SSL implementation is
# deliberately disabled. Link only axTLS's local AES primitive.
crypto_make = source / "f469-disco/usermods/uhashlib/micropython.mk"
crypto_text = crypto_make.read_text()
aes_line = "SRC_USERMOD += $(TOP)/lib/axtls/crypto/aes.c\n"
if aes_line not in crypto_text:
    crypto_make.write_text(crypto_text + "\n# Browser-only AES backend for ucryptolib\n" + aes_line)
crypto_text = crypto_make.read_text()
aes_includes = "CFLAGS_USERMOD += -I$(TOP)/lib/axtls/ssl -I$(TOP)/lib/axtls/crypto -I$(TOP)/extmod/axtls-include\n"
if aes_includes not in crypto_text:
    crypto_make.write_text(crypto_text + aes_includes)

# WebAssembly keeps live pointers in wasm locals/registers. setjmp does not
# reliably expose those to MicroPython's conservative garbage collector.
gc_file = source / "f469-disco/micropython/ports/unix/gccollect.c"
gc_source = gc_file.read_text()
old = """MP_NOINLINE void gc_collect_regs_and_stack(void) {
    regs_t regs;
    gc_helper_get_regs(regs);
    // GC stack (and regs because we captured them)
    void **regs_ptr = (void**)(void*)&regs;
    gc_collect_root(regs_ptr, ((uintptr_t)MP_STATE_THREAD(stack_top) - (uintptr_t)&regs) / sizeof(uintptr_t));
}
"""
new = """#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
STATIC void gc_collect_wasm_range(void *start, void *end) {
    gc_collect_root(start, ((uintptr_t)end - (uintptr_t)start) / sizeof(uintptr_t));
}
#endif

MP_NOINLINE void gc_collect_regs_and_stack(void) {
#ifdef __EMSCRIPTEN__
    emscripten_scan_registers(gc_collect_wasm_range);
    emscripten_scan_stack(gc_collect_wasm_range);
#else
    regs_t regs;
    gc_helper_get_regs(regs);
    // GC stack (and regs because we captured them)
    void **regs_ptr = (void**)(void*)&regs;
    gc_collect_root(regs_ptr, ((uintptr_t)MP_STATE_THREAD(stack_top) - (uintptr_t)&regs) / sizeof(uintptr_t));
#endif
}
"""
if old in gc_source:
    gc_file.write_text(gc_source.replace(old, new, 1))
elif new not in gc_source:
    raise RuntimeError(f"Unexpected GC source at {gc_file}")

# Emscripten has an explicit 8 MB wasm stack. The Unix default's 40 KB
# MicroPython guard rejects Specter's deep but legitimate import graph.
replace(
    "f469-disco/micropython/ports/unix/main.c",
    "    mp_stack_set_limit(40000 * (BYTES_PER_WORD / 4));",
    """#ifdef __EMSCRIPTEN__
    mp_stack_set_limit(1024 * 1024);
#else
    mp_stack_set_limit(40000 * (BYTES_PER_WORD / 4));
#endif""",
    1,
)

# The indirect-label VM dispatch is not safe on this older wasm toolchain;
# use MicroPython's normal switch dispatcher in the browser build.
replace(
    "f469-disco/micropython/ports/unix/mpconfigport.h",
    "#define MICROPY_OPT_COMPUTED_GOTO   (1)",
    """#ifdef __EMSCRIPTEN__
#define MICROPY_OPT_COMPUTED_GOTO   (0)
#else
#define MICROPY_OPT_COMPUTED_GOTO   (1)
#endif""",
    1,
)
