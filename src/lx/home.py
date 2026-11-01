"""The person's home folder, for a process started with a trimmed environment.

Some agent clients start a tool server with only the variables they choose to pass. Without
USERPROFILE, or HOMEDRIVE and HOMEPATH, `Path.home()` raises on Windows, and the server never
answers its client. This asks Windows for the profile folder when the variables are missing.
"""
import os
from pathlib import Path


def home():
    try:
        return Path.home()
    except RuntimeError:
        if os.name != "nt":
            raise
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 40, None, 0, buffer) == 0 and buffer.value:  # CSIDL_PROFILE
            return Path(buffer.value)
        raise
