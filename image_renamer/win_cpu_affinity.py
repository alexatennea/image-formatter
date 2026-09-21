"""Windows-only mitigation for a suspected hybrid-CPU (Performance/
Efficiency core) numeric-correctness bug in onnxruntime.

On a very new Intel chip (Core Ultra 200-series, "Lunar Lake" -- a hybrid
P-core/E-core design), OCR output was observed to come out as near-random
characters on some fields of a batch but not others, with no crash and no
error -- consistent with individual inference calls landing on an
Efficiency core whose instruction-set support differs subtly from the
Performance cores, similar to the well-documented AVX-512 issue that led
Intel to disable it entirely on Alder Lake's launch (software assumed
uniform instruction support across cores; the E-cores didn't have it;
results silently corrupted rather than crashing).

This restricts the whole process to Performance-class cores only, using
the CPU Set APIs Windows exposes specifically for hybrid-CPU-aware
scheduling (available since Windows 10 2004 / Windows 11). Every step is
defensive: any failure (unsupported Windows version, unexpected struct
layout, a non-hybrid CPU with only one efficiency class, etc.) leaves the
process unrestricted -- exactly today's behaviour -- rather than raising.

This has not been verified against real hybrid-CPU hardware (developed
and tested only for "does it run without error", not "does it fix the
actual bug", since the affected machine isn't accessible for testing).
"""
from __future__ import annotations

import ctypes
import sys


def pin_to_performance_cores() -> bool:
    """Best-effort; returns True if a restriction was actually applied."""
    if sys.platform != "win32":
        return False
    try:
        return _pin_to_performance_cores_impl()
    except Exception:  # noqa: BLE001 - never let this mitigation crash the app
        return False


def _pin_to_performance_cores_impl() -> bool:
    from ctypes import wintypes

    class _SystemCpuSetInformation(ctypes.Structure):
        _fields_ = [
            ("Size", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("Id", wintypes.DWORD),
            ("Group", wintypes.WORD),
            ("LogicalProcessorIndex", ctypes.c_ubyte),
            ("CoreIndex", ctypes.c_ubyte),
            ("LastLevelCacheIndex", ctypes.c_ubyte),
            ("NumaNodeIndex", ctypes.c_ubyte),
            ("EfficiencyClass", ctypes.c_ubyte),
            ("AllFlags", ctypes.c_ubyte),
            ("SchedulingClass", wintypes.DWORD),
            ("AllocationTag", ctypes.c_uint64),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    get_info = kernel32.GetSystemCpuSetInformation
    get_info.argtypes = [
        ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        wintypes.HANDLE, wintypes.ULONG,
    ]
    get_info.restype = wintypes.BOOL

    process = kernel32.GetCurrentProcess()

    # Standard two-call pattern: first call with no buffer to learn the
    # required size, then call again with a buffer of that size.
    needed = wintypes.ULONG(0)
    get_info(None, 0, ctypes.byref(needed), process, 0)
    if needed.value == 0:
        return False

    buf = ctypes.create_string_buffer(needed.value)
    ok = get_info(buf, needed.value, ctypes.byref(needed), process, 0)
    if not ok:
        return False

    # The buffer is a packed sequence of variable-sized entries; each
    # entry's own Size field says how far to advance to the next one.
    entries: list[_SystemCpuSetInformation] = []
    offset = 0
    while offset < needed.value:
        entry = _SystemCpuSetInformation.from_buffer_copy(buf, offset)
        entries.append(entry)
        if entry.Size == 0:
            break
        offset += entry.Size

    efficiency_classes = {e.EfficiencyClass for e in entries}
    if len(efficiency_classes) < 2:
        # Not a hybrid CPU (or we couldn't tell) -- nothing to restrict.
        return False

    best = max(efficiency_classes)
    performance_core_ids = [e.Id for e in entries if e.EfficiencyClass == best]
    if not performance_core_ids:
        return False

    set_default = kernel32.SetProcessDefaultCpuSets
    set_default.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
    set_default.restype = wintypes.BOOL

    id_array = (wintypes.ULONG * len(performance_core_ids))(*performance_core_ids)
    return bool(set_default(process, id_array, len(performance_core_ids)))
