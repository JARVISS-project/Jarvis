import pytest

from jarvis.runtime.diagnostic import check_core_audio, check_gsmtc


@pytest.mark.parametrize('session,reason', [(None, 'no_session'), (object(), 'session_present')])
def test_gsmtc_binding_is_distinct_from_session(monkeypatch, session, reason):
    from jarvis.media.winrt_adapter import WinrtGsmtcAdapter
    monkeypatch.setattr(WinrtGsmtcAdapter, 'available', lambda self: True)
    monkeypatch.setattr(WinrtGsmtcAdapter, 'discover_manager', lambda self, cancel: object())
    monkeypatch.setattr(WinrtGsmtcAdapter, 'current_session', lambda self, manager, cancel: session)
    result = check_gsmtc(True)
    assert result['status'] == 'ready' and result['reason'] == reason
    assert result['session_active'] == (session is not None)


def test_binding_absent_and_backend_failure(monkeypatch):
    from jarvis.media.winrt_adapter import WinrtGsmtcAdapter
    monkeypatch.setattr(WinrtGsmtcAdapter, 'available', lambda self: False)
    assert check_gsmtc(True)['reason'] == 'binding_missing'
    monkeypatch.setattr(WinrtGsmtcAdapter, 'available', lambda self: True)
    def fail(*args):
        raise OSError('private backend details')
    monkeypatch.setattr(WinrtGsmtcAdapter, 'discover_manager', fail)
    result = check_gsmtc(True)
    assert result['status'] == 'error' and 'private' not in str(result)


def test_core_audio_uses_production_com_binding_and_cleans_up(monkeypatch):
    from jarvis.local_control import windows
    calls = []
    class Endpoint:
        def __enter__(self):
            calls.append('enter')
            return self
        def get(self):
            calls.append('get')
            return 50, False
        def __exit__(self, *args):
            calls.append('exit')
    monkeypatch.setattr('jarvis.runtime.diagnostic.sys.platform', 'win32')
    monkeypatch.setattr(windows, '_DefaultEndpointVolume', Endpoint)
    result = check_core_audio(True)
    assert result['status'] == 'ready' and result['backend'] == 'ctypes-com'
    assert calls == ['enter', 'get', 'exit']
    def fail(self):
        raise OSError('device absent')
    monkeypatch.setattr(Endpoint, 'get', fail)
    assert check_core_audio(True)['status'] == 'unavailable'
    assert calls[-1] == 'exit'


def test_disabled_backends_do_not_probe(monkeypatch):
    from jarvis.media.winrt_adapter import WinrtGsmtcAdapter
    monkeypatch.setattr(WinrtGsmtcAdapter, 'available', lambda self: (_ for _ in ()).throw(AssertionError()))
    assert check_gsmtc(False)['status'] == 'disabled'
    assert check_core_audio(False)['status'] == 'disabled'


def test_native_async_timeout_cancels_and_drains_worker():
    import asyncio
    from threading import Event
    from jarvis.media.service import GsmtcMediaService
    from jarvis.media.winrt_adapter import WinrtGsmtcAdapter

    cancelled = Event()
    native_cancelled = Event()
    class Operation:
        def cancel(self):
            native_cancelled.set()
        def __await__(self):
            async def wait():
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return wait().__await__()
    service = GsmtcMediaService(WinrtGsmtcAdapter(), timeout=0.02)
    with pytest.raises(TimeoutError):
        service._call(lambda cancel: WinrtGsmtcAdapter._await(Operation(), cancel))
    assert cancelled.wait(1) and native_cancelled.is_set()


def test_core_audio_partial_activation_failure_releases_com(monkeypatch):
    import ctypes
    from jarvis.local_control.windows import _DefaultEndpointVolume
    calls = []
    class Ole:
        def CoInitializeEx(self, *args):
            return 0
        def CoCreateInstance(self, *args):
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = 123
            return 0
        def CoUninitialize(self):
            calls.append('uninit')
    endpoint = object.__new__(_DefaultEndpointVolume)
    endpoint._ole32 = Ole()
    endpoint._initialized = False
    endpoint._enumerator = ctypes.c_void_p()
    endpoint._device = ctypes.c_void_p()
    endpoint._endpoint = ctypes.c_void_p()
    def invoke(pointer, index, *args):
        if index == 2:
            calls.append('release')
            return 0
        raise OSError('No default render device')
    monkeypatch.setattr(endpoint, '_invoke', invoke)
    with pytest.raises(OSError):
        endpoint.__enter__()
    assert calls == ['release', 'uninit']
    endpoint.__exit__()
    assert calls == ['release', 'uninit']
