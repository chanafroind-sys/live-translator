"""Live Translator: local, real-time translation of your computer's audio."""

__version__ = "0.1.0"

import sys as _sys

# comtypes (used for per-app volume and process loopback) must join the
# same multithreaded COM apartment that soundcard uses; its default (STA)
# fails with "Cannot change thread mode after it is set".
_sys.coinit_flags = 0
