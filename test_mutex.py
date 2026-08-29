import subprocess, time, sys

code1 = '''
import win32event, win32api, winerror, time, os
print("PID: " + str(os.getpid()))
mutex = win32event.CreateMutex(None, False, "Global\\\\JARVIS_Background_Runtime")
err = win32api.GetLastError()
print("Created mutex, error=" + str(err) + ", already_exists=" + str(err==winerror.ERROR_ALREADY_EXISTS))
print("Holding mutex for 15 seconds...")
time.sleep(15)
win32api.CloseHandle(mutex)
print("Done")
'''

p1 = subprocess.Popen([sys.executable, '-c', code1])
print("Started process 1: " + str(p1.pid))
time.sleep(2)

code2 = '''
import win32event, win32api, winerror, time, os
print("PID: " + str(os.getpid()))
mutex = win32event.CreateMutex(None, False, "Global\\\\JARVIS_Background_Runtime")
err = win32api.GetLastError()
print("Created mutex, error=" + str(err) + ", already_exists=" + str(err==winerror.ERROR_ALREADY_EXISTS))
time.sleep(2)
win32api.CloseHandle(mutex)
print("Done")
'''

p2 = subprocess.Popen([sys.executable, '-c', code2])
print("Started process 2: " + str(p2.pid))

p1.wait()
p2.wait()