"""Serialize configuration and battery transactions across app processes."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]

@contextmanager
def device_session():
    handle = kernel.CreateMutexW(None, False, 'Local\\DeviceBatteryViewer.HidTransactions')
    if not handle:
        raise OSError('无法建立设备通信锁')
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 5000) in (0, 0x80)
        if not acquired:
            raise TimeoutError('设备正在读取，请稍后重试')
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
