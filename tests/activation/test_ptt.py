from jarvis.voice.ptt import PttController, PttState


def test_release_is_latched_before_capture_and_double_press_is_ignored():
    control = PttController()
    assert control.press()
    assert not control.press()
    control.release()
    assert not control.press()
    control.begin_capture()
    assert control.stop_requested
    assert control.state is PttState.STOP_REQUESTED
    control.finish_capture()
    assert control.state is PttState.COMPLETED
    assert control.press()
    assert not control.stop_requested


def test_press_adopts_existing_capture_and_shutdown_is_terminal():
    control = PttController()
    control.begin_capture()
    assert control.press()
    assert control.state is PttState.CAPTURING
    assert not control.pending
    control.close()
    assert control.stop_requested
    control.finish_capture()
    assert control.state is PttState.CANCELLED
    assert not control.press()
