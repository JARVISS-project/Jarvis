from scripts.validate_windows_package_23bo import owned_runtime_pids


def test_cleanup_scope_excludes_other_instances_and_same_pid_at_foreign_path():
    rows = [
        {'ProcessId': 2, 'ParentProcessId': 1, 'ExecutablePath': r'C:\temp\JARVIS.exe'},
        {'ProcessId': 3, 'ParentProcessId': 2, 'ExecutablePath': r'C:\temp\JARVIS.exe'},
        {'ProcessId': 4, 'ParentProcessId': 1, 'ExecutablePath': r'C:\other\JARVIS.exe'},
        {'ProcessId': 5, 'ParentProcessId': 99, 'ExecutablePath': r'C:\temp\JARVIS.exe'},
    ]
    assert owned_runtime_pids(rows, r'C:\temp\JARVIS.exe', 1) == {1, 2, 3}
