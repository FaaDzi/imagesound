"""
Free system RAM, for the low-memory warning on POST /generate.

Measured on the host laptop: loading ACE-Step briefly needs ~6 GB of system
RAM on top of whatever is already open, and moving the DiT off the GPU before
decoding needs a few GB again. With less than that free, Windows starts paging
and the whole machine stalls for a while. Generation still goes ahead -- this
only warns (see routers/generate.py).

Read with ctypes instead of psutil so the backend needs no extra package.
"""

import ctypes
import sys


def available_ram_gb() -> float | None:
    """Physical RAM available right now, in GB, or None if it can't be read."""
    try:
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullAvailPhys / 2**30

        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 2**20  # kB -> GB
    except (OSError, AttributeError, ValueError):
        pass
    return None
