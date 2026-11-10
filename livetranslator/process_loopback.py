"""Record everything the PC plays EXCEPT this app (Windows 10 2004+ / 11).

Plain loopback also captures our own Hebrew narration, so the subtitles
would have to stop listening whenever we speak and miss what the other side
says meanwhile. Windows' process-loopback mode can exclude a process tree:
we hear Zoom/Chrome/etc. and never ourselves.

API: ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK) with
AUDIOCLIENT_ACTIVATION_PARAMS { PROCESS_LOOPBACK, our PID, EXCLUDE_TREE },
then a normal IAudioClient / IAudioCaptureClient read loop.
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import POINTER, Structure, c_byte, c_int, c_long, c_uint32, c_ulong, c_ulonglong, c_ushort, c_void_p, c_wchar_p

import numpy as np
from comtypes import COMMETHOD, GUID, HRESULT, COMObject, IUnknown

from .audio import FRAME, SAMPLE_RATE

RATE = 48000
_LOOPBACK, _EVENTCALLBACK, _AUTOCONVERT = 0x00020000, 0x00040000, 0x80000000
_SILENT = 0x2


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(c_long), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation")),
    ]


class IAgileObject(IUnknown):  # the completion handler must be agile
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(c_byte)), "ppData"),
                  (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                  (["out"], POINTER(c_ulong), "pdwFlags"),
                  (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
                  (["out"], POINTER(c_ulonglong), "pu64QPCPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
    ]


class _Handler(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, activateOperation):
        self.done.set()
        return 0


class _Params(Structure):
    _fields_ = [("ActivationType", c_int), ("TargetProcessId", c_ulong), ("ProcessLoopbackMode", c_int)]


class _Blob(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]


class _PropVariant(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("blob", _Blob)]


def _activate(pid: int):
    from pycaw.api.audioclient import IAudioClient

    params = _Params(1, pid, 1)  # PROCESS_LOOPBACK, EXCLUDE_TARGET_PROCESS_TREE
    pv = _PropVariant(65, 0, 0, 0, _Blob(ctypes.sizeof(params), ctypes.cast(ctypes.pointer(params), c_void_p)))
    handler = _Handler()
    op = POINTER(IActivateAudioInterfaceAsyncOperation)()
    fn = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
    fn.restype = c_long
    fn.argtypes = [c_wchar_p, POINTER(GUID), POINTER(_PropVariant),
                   POINTER(IActivateAudioInterfaceCompletionHandler),
                   POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
    hr = fn("VAD\\Process_Loopback", ctypes.byref(IAudioClient._iid_), ctypes.byref(pv),
            handler.QueryInterface(IActivateAudioInterfaceCompletionHandler), ctypes.byref(op))
    if hr < 0:
        raise OSError(f"ActivateAudioInterfaceAsync failed: {hr & 0xFFFFFFFF:#x}")
    if not handler.done.wait(5):
        raise TimeoutError("process loopback activation timed out")
    result, unk = op.GetActivateResult()
    if result < 0:
        raise OSError(f"process loopback activation failed: {result & 0xFFFFFFFF:#x}")
    return unk.QueryInterface(IAudioClient)


class ExcludeSelfCapture(threading.Thread):
    """Same interface as audio.LoopbackCapture: 30 ms 16 kHz mono frames."""

    excludes_self = True

    def __init__(self, out: "queue.Queue[np.ndarray]"):
        super().__init__(daemon=True, name="process-loopback")
        self.out = out
        self.stop_event = threading.Event()
        self.error: Exception | None = None
        self.started_ok = threading.Event()

    def run(self) -> None:
        from pycaw.api.audioclient.depend import WAVEFORMATEX

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # multithreaded apartment
        try:
            client = _activate(os.getpid())
            fmt = WAVEFORMATEX(1, 2, RATE, RATE * 4, 4, 16, 0)
            client.Initialize(0, _LOOPBACK | _EVENTCALLBACK | _AUTOCONVERT, 2_000_000, 0, ctypes.byref(fmt), None)
            event = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
            client.SetEventHandle(event)
            capture = client.GetService(ctypes.byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
            client.Start()
            self.started_ok.set()
            pending = np.zeros(0, np.float32)
            while not self.stop_event.is_set():
                ctypes.windll.kernel32.WaitForSingleObject(event, 200)
                while capture.GetNextPacketSize() > 0:
                    data, frames, flags, _, _ = capture.GetBuffer()
                    if flags & _SILENT:
                        pcm = np.zeros(frames, np.float32)
                    else:
                        raw = np.frombuffer(ctypes.string_at(data, frames * 4), np.int16).astype(np.float32)
                        pcm = raw.reshape(-1, 2).mean(axis=1) / 32768
                    capture.ReleaseBuffer(frames)
                    pending = np.concatenate([pending, pcm])
                    usable = len(pending) // (3 * FRAME) * 3 * FRAME  # 48k -> 16k, whole frames
                    if usable:
                        mono16 = pending[:usable].reshape(-1, 3).mean(axis=1)
                        pending = pending[usable:]
                        for i in range(0, len(mono16), FRAME):
                            self.out.put(mono16[i:i + FRAME])
            client.Stop()
        except Exception as e:
            self.error = e
            self.started_ok.set()

    def stop(self) -> None:
        self.stop_event.set()
