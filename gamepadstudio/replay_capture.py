"""Screen capture used by the continuous replay buffer.

MSS 10 uses CAPTUREBLT on Windows to include additional layered windows.
At video frame rates that operation can make the physical cursor flicker:
https://github.com/BoboTiG/python-mss/issues/179
Keep the replay grabber on SRCCOPY, without changing one-shot screenshots or
mutating MSS module globals shared with other capture threads.
"""
import sys

import mss


class _ReplayGdi:
    def __init__(self, gdi):
        self._gdi = gdi

    def __getattr__(self, name):
        return getattr(self._gdi, name)

    def BitBlt(self, *args):
        return self._gdi.BitBlt(*args[:-1], args[-1] & ~0x40000000)


def create_replay_capture():
    capture = mss.mss()
    if sys.platform == 'win32':
        capture.gdi32 = _ReplayGdi(capture.gdi32)
    return capture
