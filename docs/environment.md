# Preparing an environment

How to give a task what it needs, whether that is a key, a Python library or a
program installed on the system. Read this before starting a task that needs more
than photos, audio and text.

> **Status.** What exists today was read from the code, and the default sandbox was
> probed with throwaway scripts on 1 November 2026. The steps for adding a library or
> a system tool to the image (sections 5 and 6) have **not yet been tried**. They are
> marked, and they should be proven on a real task before you rely on them.

## 1. Which place does your requirement belong in?

"Environment" means three separate places. Put each requirement in the right one.

| Place | What lives there | Who sets it up | Changed how |
|---|---|---|---|
| **The LX machine** | uv, Node.js, Python, Docker, the app | You, once | Install it |
| **Hosted tools** | SAM, Transcribe | You add a key; the work runs at the provider | Settings, under Tools |
| **MCP tools** | A price lookup on a local MCP server | A person connects it and marks it read-only; it runs outside the sandbox | Settings, under Tools, or `lx mcp` |
| **The sandbox** | What learned scripts and scoring checks can use | You, for each need | Change the image |

Decide with these questions, in order:

1. **Is it a model that LX already offers?** Transcription and segmentation are hosted
   tools. Add a Meta key in Settings. The sandbox is not involved.
2. **Does it need a network, an account or a licence server?** It cannot run in the
   sandbox at all. See section 4.
3. **Does it need a GPU, a large model or more than 20 seconds a call?** It cannot run
   in the sandbox either.
4. **Is it a Python library?** Section 5.
5. **Is it a program, such as `ffmpeg` or `tesseract`?** Section 6.
6. **Is it none of these?** You probably need nothing. Scripts already have Python's
   standard library and Pillow.

An orchestrator can choose MCP tools a person has marked read-only, with `spec_draft`.
It cannot connect a server, because that would run a program on this machine.

An orchestrator can choose which hosted tools a run uses, with `spec_draft`. It has no
action for changing the sandbox or the machine, so a person decides those.

## 2. The LX machine

You need [uv](https://docs.astral.sh/uv/), Node.js and Python 3.12 or 3.13, plus one
sandbox: Docker Desktop on Windows, or Docker or `unshare` on Linux. The
[main README](../README.md) has the install commands.

Without a sandbox, scoring checks do not run, so graded tasks cannot start, and helper
scripts are turned off. Settings shows which sandbox was found.

Windows, Docker Desktop and WSL2 are what this has been run on. Other platforms are
untested.

## 3. What the sandbox gives you by default

Two things run in the sandbox: **helper scripts** that the improver writes and the
agent runs with `run_script`, and **scoring checks**.

A script is always one Python file. It reads one JSON object on stdin and prints JSON.
It receives the agent's input, the paths of the sample's photos, and earlier `segment`
boxes.

Which sandbox you get depends on the machine, and Linux is tried first:

| | Docker | Linux namespace |
|---|---|---|
| Used on | Native Windows, or Linux without `unshare` | Linux and WSL with `unshare` and `chroot` |
| Python and libraries | Those in the image: Python 3.12 and Pillow 11.3.0 | The system's Python, with the app's installed packages |
| Other programs | Only those in the image | The host's `/usr`, `/bin` and `/lib`, read-only |
| Network | None | None |
| Memory | 1 GB | 2 GB |
| CPU | 1 CPU | Limited by CPU time |
| Script time | 20 seconds | 20 seconds |
| Temporary space | 64 MB in `/tmp` | A private work folder, 50 MB per file |
| Processes | 256 | Not limited separately |
| File system | Read-only, apart from `/tmp` and the work folder | Only the folders above |

A script is at most 20,000 characters, its printed output is cut at 20,000 characters,
and its input is at most 2 MB. A harness holds at most 8 scripts. A scoring check has
60 seconds a batch and a larger output limit.

These are safety rules, not run limits. They are fixed in the code, and a spec cannot
change them.

## 4. What cannot work in the sandbox

| You want | Why it fails | What you see | Do this instead |
|---|---|---|---|
| A web API or download | No network | Connection errors in the script | Use a hosted tool, or put the data in the sample |
| A licence server or login | No network | The program refuses to start | Choose a tool that works offline |
| `pip install` during a run | No network, and the root is read-only | Install errors | Add it to the image first |
| A GPU | None is passed in | Falls back to CPU, or errors | Use a hosted model |
| A large model | Memory, and the 20-second limit | Killed, or a timeout | Use a hosted model |
| A long job | 20 seconds | `timed out after 20 s` | Split the work, or run it before the task and add the result to the sample |
| A Windows program | The container is Linux | Will not run | Find a Linux build |
| A large output | 20,000 characters | Output cut, so it may no longer be valid JSON | Print less |
| Saving between calls | Each run is thrown away | The file is gone next time | Pass the data in `input` |

## 5. Adding a Python library

**Not yet tried.** This is an edit to the LX repository, and it changes the sandbox for
every workspace on the machine. The steps assume a library that publishes a Linux wheel
for Python 3.12.

1. Pin it in the project. This records its version and hash in `uv.lock`, and installs
   it for the Linux sandbox:

   ```bash
   uv add "numpy==2.3.2"
   ```

   Use the exact version you want. This number is only an example.

2. Update `docker/sandbox.Dockerfile`. Its `COPY` line names the Pillow wheel, so add
   your wheel beside it. It installs with `--no-index --no-deps`, so **each dependency
   of your library needs its own wheel too**.

3. Update `scripts/build_sandbox_image.py`. It handles one wheel, named in `WHEEL`.
   Make it fetch and hash-check each of yours.

4. Give the image a new name in `src/lx/sandbox.py`, in `IMAGE`, such as
   `lx-sandbox:pillow-11.3.0-numpy-2.3.2`. A new name shows that the environment
   changed, and stops an old image passing for the new one.

5. Tell the improver, as in section 7.

6. Rebuild, then restart the app, because LX finds the image once per process:

   ```bash
   python scripts/build_sandbox_image.py
   ```

7. Check it, as in section 8.

## 6. Adding a system tool

**Installing a program is not yet tried.** Starting one is: scripts in both sandboxes
ran Python and `ls` with `subprocess`, so the route works. On this machine neither
sandbox had `ffmpeg`, `tesseract` or `gcc`.

The two sandboxes differ here. Docker has a `python` command. The Linux sandbox has only
`python3`, so use `sys.executable` to start Python from a script. Their Python versions
differ as well: 3.12.16 and 3.12.3 here.

Scripts are always Python, so a program is reached from Python:

```python
import json, subprocess, sys

data = json.load(sys.stdin)
done = subprocess.run(["tesseract", data["photos"][0], "stdout"], capture_output=True, text=True, timeout=15)
print(json.dumps({"text": done.stdout, "error": done.stderr[-300:]}))
```

**On Docker,** install it in the image. Put this in `docker/sandbox.Dockerfile`, after
`FROM` and before the `USER` line, because installing needs root. Pin the version, and
delete the package lists:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr=<version> \
    && rm -rf /var/lib/apt/lists/*
```

The base image is pinned by digest, but apt's archives are not, so a later rebuild can
fail or differ. Record the versions you used.

Anything the program needs must be in the image as well, such as language data or
model files. Then rename the image, tell the improver, rebuild and check, as in
section 5.

**On Linux without Docker,** the sandbox sees the host's `/usr`, `/bin` and `/lib`, so
install the program on the host. Files elsewhere, such as under `/opt` or your home
folder, are not visible.

## 7. Telling the improver

The improver only knows what its note says. If you skip this, a working library may
never be used.

The note is in `src/lx/inventory_workflow.py`, in `COMPONENT_NOTE`, and says
`Python 3 with Pillow`. Change it to name what you added.

The same words are matched in `src/lx/text_task.py`, in `_NOTE`, which removes Pillow
from the note on text tasks. Change that string to match, or the note there will be
wrong.

State only what is available. Do not say when or how to use it, because the loop
learns that. This note shapes agent behaviour, so check any change against the
Harness² paper first.

## 8. Checking that it worked

Run these in order:

1. **The test suite,** from `main_LX`. It should pass on the same sandbox the app uses.
2. **The app's Settings,** which should still show the sandbox.
3. **`ready` on the workspace.** Free, with no model calls. It says a run can start,
   never that the run is good.
4. **A small proof in the sandbox.** This is the check that matters. Run it from
   `main_LX` with the app's Python and `PYTHONPATH=src`:

   ```python
   from lx import sandbox

   print(sandbox.backend())
   source = 'import json, numpy\nprint(json.dumps({"version": numpy.__version__}))'
   print(sandbox.run_json(source, {}))
   ```

   You want `exit_code` 0 and the version you pinned. A library that is missing gives
   `exit_code` 1 and `ModuleNotFoundError` in `stderr`. For a program, run it with
   `subprocess` here instead, using a real sample input. Time it against the 20-second
   limit.
5. **One trial** on a real sample. It spends a little and needs approval.

After a real run, look in the agent's trace at each `run_script` call. `exit_code` 0
only shows that the script did not crash. Check that the script read the data you
expected and that the agent used the result.

## 9. Working with a coding agent

A coding agent such as Claude Code can make these changes with you, as ordinary work on
the repository. Review its diff, because a request to make something work can also
loosen a limit.

A prompt to start from:

> Read `docs/environment.md`. I need `<library or program>` in the LX sandbox because
> `<reason>`. First tell me which place in section 1 it belongs in, and whether anything
> in section 4 rules it out. If it belongs in the sandbox, follow section 5 or 6 and pin
> every version. Tell the improver what is available, as in section 7, without saying
> how to use it. Show me each package you will install before you install it. Then
> prove it with section 8. Do not change anything listed under "What must not change".

### What must not change

- No network during a run.
- The read-only root, no capabilities, and the unprivileged user.
- The memory, CPU, process and time limits.
- Pinned versions and hash checks. No unpinned installs.
- Scripts running only in the sandbox, never on the host.

### Packages you did not choose

Installing a package runs someone else's code on your machine while the image builds.
A connected agent can also approve live runs, so a person should read and accept every
package added. Do not let an agent add packages on its own.

## 10. After you change the environment

- **Scores are not strictly comparable.** Runs before and after used different
  environments, even with the same spec. Say so when you compare them.
- **It applies to the whole machine.** Every workspace uses the same image, and a run
  record does not name the image, as far as has been checked. Note the change yourself.
- **Each machine must rebuild.** Commit the changes, then run the build there.
- **Both sandboxes matter.** A change made for Docker does nothing for the Linux sandbox
  unless the library is also in the app's environment, or the program on the host.

## Things to know

- An orchestrator reads this guide with `lx guide environment` or the `guide` tool, and
  sees the sandbox's kind and limits, each tool and the run limits with `lx environment`
  or the `environment` tool. It does not see what is installed, so tell it what you added.
- More tools do not always help. In the receiving audit runs of 1 November 2026, 7
  learned scripts ran 52 times out of 53 with no sandbox problem, using only `json` and
  `sys`. The limit there was what the improver chose to write, not what the sandbox
  offered. Check that a task really needs a tool before adding it.
