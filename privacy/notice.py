"""Privacy for a widget that sits on the desktop showing spending.

It stays on screen, on top of everything, so it can be read by whoever is
looking at the screen: someone walking past, or an audience on a screen
share or recording. Three protections live here:

  * masking - replace every figure with a fixed-width placeholder;
  * auto-hide - mask after a stretch of no keyboard or mouse activity, and
    stay masked until the owner deliberately reveals it;
  * capture exclusion - ask macOS to leave the window out of screenshots and
    screen sharing.
"""
import ctypes
import ctypes.util
import re
import subprocess
import sys

MASK = "••••"
DEFAULT_IDLE_LIMIT = 300  # seconds

# Absolute, so a look-alike earlier on PATH cannot stand in for it.
IOREG = "/usr/sbin/ioreg"

# Any figure, with an optional currency sign or percent sign. Every one becomes
# the same MASK, so the placeholder's length can't hint at the amount.
_NUMBER = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?%?")


def mask_numbers(text):
    # re.sub replaces every match of the pattern with MASK - text or ""
    # guards against `text` being None, which .sub() can't handle directly.
    return _NUMBER.sub(MASK, text or "")


# ---------------------------------------------------------------- idle time
# macOS's `ioreg` command prints a tree of hardware info as text; somewhere
# in it is a line like `"HIDIdleTime" = 1234567890` - this regex pulls out
# just that number. HIDIdleTime is reported in nanoseconds, hence /1e9
# below to convert it to seconds (1e9 = 1,000,000,000).
_IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def parse_idle(output):
    """Seconds since the last keyboard or mouse input, from ioreg's output."""
    match = _IDLE.search(output or "")
    return int(match.group(1)) / 1e9 if match else None


def idle_seconds(run=subprocess.run):
    """Seconds since the last input, or None when it can't be read.

    `run` defaults to the real subprocess.run, but accepting it as a
    parameter means tests can pass in a fake instead, without this
    function ever actually launching a process during a test run.
    """
    try:
        result = run([IOREG, "-c", "IOHIDSystem"], capture_output=True,
                     text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_idle(result.stdout)


class PrivacyState:
    """Whether amounts should currently be hidden, and why.

    manual       the owner asked for it and it stays until they turn it off
    idle_locked  set by inactivity, cleared only by reveal()

    Activity alone never clears idle_locked. If it did, anyone who bumped the
    mouse would see the amounts, which is exactly what the lock is for.
    """

    def __init__(self, manual=False, auto_hide=False,
                 idle_limit=DEFAULT_IDLE_LIMIT, idle_fn=idle_seconds):
        self.manual = manual
        self.auto_hide = auto_hide
        self.idle_limit = idle_limit
        self.idle_fn = idle_fn
        self.idle_locked = False

    @property
    def masked(self):
        return self.manual or self.idle_locked

    def check_idle(self):
        """Lock if the machine has been idle long enough. True if this locked it."""
        if not self.auto_hide or self.idle_limit <= 0 or self.idle_locked:
            return False
        idle = self.idle_fn()
        # An unreadable idle time fails open: masking on a guess would leave
        # the widget permanently hidden whenever ioreg misbehaves.
        if idle is not None and idle >= self.idle_limit:
            self.idle_locked = True
            return True
        return False

    def reveal(self):
        """Clear the inactivity lock. Does not touch the manual setting."""
        was_locked = self.idle_locked
        self.idle_locked = False
        return was_locked

    def set_auto_hide(self, enabled):
        self.auto_hide = enabled
        if not enabled:
            self.idle_locked = False


# --------------------------------------------------------- capture exclusion
_SHARING_NONE = 0
_SHARING_READ_ONLY = 1


def set_capture_excluded(view_id, excluded):
    """Leave the window containing this NSView out of screenshots and sharing.

    view_id must be a real NSView pointer, i.e. QWidget.winId() on the cocoa
    platform. Anything else can crash the process, so callers check the
    platform first. Returns True if the setting was applied.

    PyQt5 has no built-in way to call this particular macOS feature, so this
    talks to Objective-C directly through ctypes (Python's foreign-function
    library for calling into C libraries) - effectively hand-rolling the
    two Objective-C method calls `view.window` and
    `window.setSharingType:` that Swift/Objective-C code would just write
    directly.
    """
    if sys.platform != "darwin" or not view_id:
        return False
    try:
        # Load Objective-C's runtime library and look up the two functions
        # (objc_msgSend, sel_registerName) every Objective-C message send
        # goes through under the hood.
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        # objc_msgSend is how every Objective-C method call actually
        # happens, but its real signature varies by call, so it has to be
        # re-cast (via CFUNCTYPE) for each different argument/return shape
        # used below: `send` for a call with no extra arguments (reading
        # `.window`), `send_ulong` for one taking an extra integer
        # argument (the sharing-type call).
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p)(address)
        send_ulong = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_ulong)(address)
        # sel_registerName(b"window") looks up Objective-C's internal id
        # for the "window" method/property; `send` then calls it on the
        # NSView to get its containing NSWindow.
        window = send(int(view_id), objc.sel_registerName(b"window"))
        if not window:
            return False
        # Calls [window setSharingType: NSWindowSharingNone] (or
        # ...ReadOnly to undo it) - this is the actual macOS API that
        # tells the system "leave this window out of screen captures".
        send_ulong(window, objc.sel_registerName(b"setSharingType:"),
                   _SHARING_NONE if excluded else _SHARING_READ_ONLY)
        return True
    except (OSError, AttributeError, ValueError):
        # Anything going wrong talking to a system library this directly
        # is treated as "couldn't apply it" rather than crashing the app.
        return False
