# Issue: [Jules] Top-level installer PATH entry injection check fails when SHELL environment variable is unset or custom

## Problem Description
In `test/test_top_level_install.py`, the test asserts that `install.sh` adds its `PATH` entry (`PATH="$HOME/.FlossWare/claude-ensemble/bin:$PATH"`) to the user's shell configuration file (e.g. `.bashrc` or `.zshrc`).

When running `test/test_top_level_install.py` in environments where `SHELL` is not explicitly set to `/bin/bash` or `/bin/zsh` or where temporary home directory shell profiles (`.bashrc` / `.zshrc`) are created dynamically during test execution, the installer's shell detection logic in `install.sh` defaults to a shell config that may not match the file created in the isolated test home directory.

As a result, `python3 test/test_top_level_install.py` fails with:
```
SystemExit: FAIL: installer did not add its PATH entry to existing shell config
```

## Impact
This causes `test/test_top_level_install.py` to fail in non-interactive CI/test environments or sandboxes where `$SHELL` is missing or set to `/bin/sh`.

## Proposed Solution
Update `test/test_top_level_install.py` to explicitly set `SHELL=/bin/bash` in the environment passed to `install.sh` during the isolated top-level install test, ensuring consistent target profile selection (`.bashrc`).
