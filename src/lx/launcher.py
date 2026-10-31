"""Starts a run's controller and exits, so the controller is nobody's child.

An agent client ends its server's whole process tree when it closes (seen with
taskkill /T on Windows, P6.1). A run must outlive whoever started it.
"""
import os
import subprocess
import sys


def start(command):
    # Windows: UTF-8 mode, because the vendored engine reads text with the default encoding.
    detach = ({"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW,
               "env":{**os.environ,"PYTHONUTF8":"1"}} if os.name == "nt" else {"start_new_session":True})
    # No inherited streams: an MCP server's stdin and stdout carry its protocol.
    with open(os.devnull,"wb") as output:
        return subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=output,**detach)


if __name__ == "__main__":
    start(sys.argv[1:])
