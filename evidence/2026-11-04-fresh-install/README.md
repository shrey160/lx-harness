# Fresh public-clone installation rehearsal — 4 November 2026

**Windows and Ubuntu WSL2 passed the documented installation and synthetic-preview
workflow from fresh public GitHub clones.** Both used independent new Python
environments, built frontends, real script sandboxes, isolated UI state and fresh
SDK stdio MCP clients. Each completed all six synthetic samples and retained its
setting/workspace/result after server restart. No provider key or paid call.

This is fresh-checkout evidence on an existing host, not a clean-OS test. WSL is
the Linux environment tested; bare Linux, other distributions and ARM remain
unverified. An SDK client exercised generated MCP setup; no global Codex/Claude/
Muse configuration was changed and no independent autonomous installation agent
was used.

## Source and environment matrix

Both platforms cloned these public `main` revisions directly from GitHub:

| Repository | Revision |
| --- | --- |
| LX harness | `0e4004b42c6007bcc55ec25f68745fddf576fdaa` |
| Separate portable core | `ab7ef6ef9fee847f314a6337118066f4268ecde3` |

The source revision pair is the same on both platforms. The repositories are
siblings inside a disposable host Git repository, outside its task sample folder.
All four fresh Git working trees remain clean after setup. The original LX
checkout and original core were preserved; the original core's existing
`.env.example` modification was neither read nor changed.

| Check | Native Windows 11 x86_64 | Ubuntu 24.04.3, WSL2 x86_64 |
| --- | --- | --- |
| Git | 2.52.0.windows.1 | 2.43.0 |
| uv | 0.13.5, preinstalled | 0.13.5, task-local official release |
| Node / npm | 24.20.0 / 11.10.1, preinstalled | 24.20.0 / 11.19.0, task-local official release |
| Managed Python | 3.12.13, existing interpreter/cache | 3.12.15, freshly downloaded into task-local tools |
| New virtual environment | `.venv-win`, Windows executables | `.venv`, Linux executables on native Linux filesystem |
| Locked sync with MCP extra | 42 resolved, 39 installed | 42 resolved, 39 installed |
| Frontend | `npm --prefix web ci` and build pass | same commands pass |
| Docker image builder | lock-hash-checked Pillow download, cached build layers | lock-hash-checked Pillow download, successful build |
| Actual checker/helper backend | Docker, existing Docker Desktop 29.8.0 | Linux user/mount/network namespaces |
| CLI / MCP `--help` | pass | pass |
| Browser UI | fresh state, create synthetic workspace, Tools/Connectivity, completed run | same workflow, namespace readiness, completed run |
| Generated setup / stdio client | correct fresh interpreter/root, 23 tools | correct fresh interpreter/root, 23 tools |
| Checker self-test | 66 cases, 6 samples × 8 named checks | same |
| Preview | `a8b0a710e50d`, completed 6/6 | `01907f576b3b`, completed 6/6 |
| Restart persistence | pass | pass |

The Windows host-project path includes spaces. Linux tools were installed into a
disposable directory from official uv/Node release URLs with supplied SHA-256
checks, without system-wide installation. Existing WSL had no Linux-native uv or
Node; its npm resolved to a Windows installation. That was corrected in the test
process's PATH before setup. Docker Desktop/WSL and Windows prerequisites already
existed, so this does not verify installing those prerequisites on a clean OS.
Docker image builds used the existing shared daemon/base-image cache.

Linux npm 11.19.0 reported an esbuild install-script approval warning. The locked
platform package and frontend build succeeded without disabling protections or
changing the lockfile. No dependency update was performed.

## End-to-end checks

For each platform:

1. Clone the two public repositories and run the README's locked sync, npm install/
   build, image builder and CLI/MCP entry-point checks.
2. Start the fresh UI on a separate loopback port with an explicit scratch state
   root. Create the **Order records (text only)** synthetic workspace through the
   browser. Change only that test state's SAM toggle, checking non-secret persistence.
3. Parse the UI-generated Codex TOML in memory and launch a fresh SDK stdio client
   using its interpreter/arguments/root. No global agent configuration changes.
   Verify live calls are disabled, no key is present, the UI's setting is visible,
   guides/environment work and the same workspace can be inspected.
4. Test the check through the real sandbox, approve/start only the controlled
   synthetic preview, poll it to completion, read results/edits and download the
   package/trajectories through the UI API. Browser confirms completed 6/6 and
   the source-folder scores after restart.
5. Restart that UI with the same root and reconnect a new MCP client. The saved
   setting, registered workspace and completed run remain readable. No second
   preview is started for persistence testing.

Each check self-test reports blank score **0.125**, wrong-answer score **0.25**,
no weaknesses. These are allowed partial field credits in the built-in records
check, not all-or-nothing matching. Both controlled previews report **0.75 → 1.00**
from scripted doubles. This establishes wiring only and says nothing about model
quality or real harness improvement. Each UI labels usage as simulated:
`$0.0018952`; actual spend and held spend are **zero**.

Each preview produced a valid **12-file package** and **24-trajectory ZIP**;
ZIP CRCs pass. Learned text/edits and all six final answers/checks are exposed.
[windows.json](windows.json) and [linux-wsl.json](linux-wsl.json) contain the
non-sensitive summaries, backend details, run IDs and restart checks.

One Windows preview attempt failed with **cannot publish snapshot destination**
before any run/job existed. After confirming an empty job list, a separate snapshot
approval succeeded and the retried preview completed once. The underlying transient
rename cause was not isolated; no retry loop or product fix conceals it. Keep this
as a Windows reliability caveat. Repeated failure requires investigation, not
repeated run starts. The first Linux background UI launcher also did not persist;
a hidden foreground WSL launcher kept the server available. That was test-process
management, not an application startup or installation failure.

The validation helper initially assumed zero blank-answer credit; corrected to
accept the built-in check's documented partial credit before execution. No check
or product code was weakened or patched. No source-code fix was needed to complete
either installation. Current installation docs now describe the observed Linux
prerequisite distinction and the safe snapshot-failure diagnostic.

## Separate three-sample example artifact

The example commit `ea2b8f1` was not published and is **absent from these public
clones**. Tested its separately identified local bundle (manifest SHA-256
`3f4321da1e943ad7544dff0fc2d5ffa32c64cb0f3d2a69c2cc3958a4d10424c3`) with each
fresh install's Python and a separate scratch MCP state. No old configuration,
credentials, approvals or run cache were copied into either installation.

On each platform the bundle's 29-file integrity/media verification, six positive/
45 negative cases and **33 real-sandbox checker cases** pass. Task loading and
direct starting JSON work, truth reads are refused, and no hint/eval drift or
data problems occur. **Live readiness is false as expected because the fresh
installation has no Meta key**, with an explicit Spark/SAM/Transcribe key blocker.
No example run was started. [windows-example.json](windows-example.json) and
[linux-example.json](linux-example.json) preserve these results. This verifies the
package's preparation instructions on new installs, not paid perception/model use.

## Reproduction and remaining limits

Follow the [installation guide](../../docs/installation.md) and use two new sibling
clones, a separate synthetic task folder, and a shared explicit scratch root for
the UI and MCP. The local test used ports 8876/8877. Then use the fresh install's
Python, not the original environment, to execute [rehearse.py](rehearse.py):

```text
python rehearse.py --url http://127.0.0.1:8876 --workspace ABSOLUTE_SYNTHETIC_TASK_FOLDER --output windows.json
```

The helper requires a synthetic text workspace created in that UI and SAM switched
off in the test state, checks the generated launch configuration, refuses
live-enabled state, and refuses to duplicate a preview if a run already exists.
After a server restart, append `--restart-check` to verify persistence against the
same output record. For the Linux test use its Python, Linux paths and port 8877.

Installation tool/source downloads used the network. Controlled previews and
checker/helper sandboxes made no provider calls. Key entry, keychain saving,
provider acceptance, local Unsloth, live Transcribe/SAM, real model-loop quality,
bare Linux and clean-OS prerequisite setup remain untested in this rehearsal.
No broad suite was necessary for documentation/evidence-only edits.

Only task-owned test servers/tabs are stopped at closeout; clones, scratch state
and completed artifacts remain locally for review. No existing real workspace
deletion, billing decision, credential inspection, core patch, push or Git history
change. General D-041 experiment export remains a separate pending task.
