# tests

The product suite. It runs offline: no live calls, no credentials, no spend. Provider
replies are doubles, so a pass proves the plumbing, not what a model can do.

Run it from `main_LX`, always with discovery, because test modules import each other
by bare name:

```powershell
.venv-win\Scripts\python.exe -m unittest discover -s tests
```

```bash
.venv/bin/python -m unittest discover -s tests
```

## Skips

Tests that use the sandbox skip when there is none, so on Windows start Docker Desktop
and build the image first. A full Windows run skips 2 tests (symlinks and a POSIX path
translation); a full WSL run skips none.

## Layout

Most `test_<name>.py` files cover `lx/<name>.py`. These go wider:

| File | Covers |
|---|---|
| `test_application.py` | The whole app flow through the engine |
| `test_server.py` | The loopback HTTP boundary |
| `test_tasks.py` | Spec-driven tasks, on the real engine and sandbox |
| `test_inventory_workflow.py` | The inventory loop end to end, with doubles |
| `test_spike.py` | The actual engine with the controller-backed fixture |
| `test_components.py`, `test_checks.py` | Skills, scripts and checks, on the real sandbox |

## Rules for tests

- Use dummy keys and a fake keychain. `Engine()` defaults to the real system keychain,
  so pass a fake one (fix list item 26 makes this refuse).
- Never open the network. Mock the transport.
- Keep a scratch state root for anything that writes state.

`test_managed_workflow` and `test_nested_recovery` in the core suite are known to be
flaky in WSL.
