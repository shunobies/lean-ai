"""Suppress ALSA error messages on Linux during PyAudio initialization.

ALSA emits non-fatal errors to stderr when PyAudio enumerates audio devices
(unknown PCM cards, unable to open slave, can't connect to server socket).
This module redirects the ALSA error handler to a no-op via ctypes.
"""

import logging
import platform

logger = logging.getLogger(__name__)

_installed = False


def suppress_alsa_errors() -> None:
    """Redirect ALSA error handler to suppress noisy startup messages.

    Safe to call multiple times and on non-Linux platforms (no-op).
    """
    global _installed
    if _installed or platform.system() != "Linux":
        return

    try:
        import ctypes

        error_handler_func = ctypes.CFUNCTYPE(
            None,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
        )

        def _noop(filename, line, function, err, fmt):
            pass

        _handler = error_handler_func(_noop)
        asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        asound.snd_lib_error_set_handler(_handler)
        # Keep reference alive so the callback isn't garbage-collected
        suppress_alsa_errors._handler = _handler  # type: ignore[attr-defined]
        _installed = True
        logger.debug("ALSA error suppression installed")
    except Exception as exc:
        logger.debug("Could not install ALSA error handler: %s", exc)
