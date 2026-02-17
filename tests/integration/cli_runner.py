"""In-process CLI runner for integration tests.

WHY this exists: Running the CLI via subprocess.run() adds ~2-3s of Python
startup overhead per invocation. Integration tests make many CLI calls, so
this overhead compounds to 50+ seconds across the suite. This module provides
run_cli() which calls the CLI dispatch logic directly in-process, capturing
stdout/stderr the same way subprocess would.

Usage:
    from tests.integration.cli_runner import run_cli

    result = run_cli("explore", "--spec", str(spec_path), "--validate", ...)
    assert result.returncode == 0
    assert "Validation successful" in result.stdout
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from io import StringIO


@dataclass
class CLIResult:
    """Result of an in-process CLI invocation, matching subprocess interface.

    Attributes:
        returncode: Exit code (0 = success, 1 = error).
        stdout: Captured standard output as string.
        stderr: Captured standard error as string.
    """

    returncode: int
    stdout: str
    stderr: str


def run_cli(*args: str) -> CLIResult:
    """Run the api-parity CLI in-process, capturing stdout/stderr.

    Equivalent to subprocess.run([sys.executable, "-m", "api_parity.cli", *args])
    but without the ~2-3s Python startup overhead.

    Args:
        *args: CLI arguments (e.g., "explore", "--spec", "openapi.yaml").

    Returns:
        CLIResult with returncode, stdout, and stderr.
    """
    from api_parity.cli import dispatch, parse_args

    # Save original streams
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    captured_stdout = StringIO()
    captured_stderr = StringIO()

    sys.stdout = captured_stdout
    sys.stderr = captured_stderr

    try:
        parsed = parse_args(list(args))
        returncode = dispatch(parsed)

    except SystemExit as e:
        # argparse calls sys.exit on parse errors
        returncode = e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        returncode = 1
    except Exception as e:
        # Unexpected error — print to captured stderr for debugging
        captured_stderr.write(f"Unexpected error: {e}\n")
        captured_stderr.write(traceback.format_exc())
        returncode = 1
    finally:
        stdout_val = captured_stdout.getvalue()
        stderr_val = captured_stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return CLIResult(returncode=returncode, stdout=stdout_val, stderr=stderr_val)
