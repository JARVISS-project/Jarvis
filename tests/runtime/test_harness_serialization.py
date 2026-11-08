import threading
import time
from types import MethodType

from jarvis.harness import Harness


def test_harness_serializes_voice_and_remote_turns() -> None:
    harness = Harness.__new__(Harness)
    harness._turn_lock = threading.RLock()
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def process(_self, _message, **_kwargs):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with state_lock:
            active -= 1
        return "ok"

    harness._processar_mensagem_serialized = MethodType(process, harness)
    threads = [
        threading.Thread(target=harness.processar_mensagem, args=(str(index),))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum_active == 1
