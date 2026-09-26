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
    return _NUMBER.sub(MASK, text or "")


# ---------------------------------------------------------------- idle time
_IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def parse_idle(output):
    """Seconds since the last keyboard or mouse input, from ioreg's output."""
    match = _IDLE.search(output or "")
    return int(match.group(1)) / 1e9 if match else None


def idle_seconds(run=subprocess.run):
    """Seconds since the last input, or None when it can't be read."""
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

    def __init__(self, manual=False, auto_hide=True,
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
    """
    if sys.platform != "darwin" or not view_id:
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p)(address)
        send_ulong = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_ulong)(address)
        window = send(int(view_id), objc.sel_registerName(b"window"))
        if not window:
            return False
        send_ulong(window, objc.sel_registerName(b"setSharingType:"),
                   _SHARING_NONE if excluded else _SHARING_READ_ONLY)
        return True
    except (OSError, AttributeError, ValueError):
        return False
