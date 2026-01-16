"""CEL Evaluator - Python wrapper for the Go CEL subprocess.

This module provides a CELEvaluator class that manages the Go subprocess
and provides a simple evaluate() interface for CEL expression evaluation.

Protocol (newline-delimited JSON):
    Startup: Go sends {"ready":true}
    Request: Python sends {"id":"<uuid>","expr":"a == b","data":{"a":1,"b":1}}
    Response: Go sends {"id":"<uuid>","ok":true,"result":true}
    Error: Go sends {"id":"<uuid>","ok":false,"error":"..."}
"""

from __future__ import annotations

import json
import select
import subprocess
import uuid
from pathlib import Path
from typing import Any


class CELEvaluationError(Exception):
    """Raised when a CEL expression fails to evaluate."""


class CELSubprocessError(Exception):
    """Raised when the CEL subprocess fails or cannot be started."""


class CELEvaluator:
    """Manages a Go CEL evaluator subprocess.

    Usage:
        evaluator = CELEvaluator()
        try:
            result = evaluator.evaluate("a == b", {"a": 1, "b": 1})
            print(result)  # True
        finally:
            evaluator.close()

    Or with context manager:
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("a > 0", {"a": 5})
    """

    # Default path to cel-evaluator binary (relative to this file's directory)
    DEFAULT_BINARY_PATH = Path(__file__).parent.parent / "cel-evaluator"

    # Maximum restart attempts before giving up
    MAX_RESTARTS = 3

    # Timeout for subprocess startup (seconds)
    STARTUP_TIMEOUT = 5.0

    # Timeout for individual evaluation (seconds)
    # Go CEL evaluator has internal 5s timeout, so use 10s to allow for IPC overhead
    EVALUATION_TIMEOUT = 10.0

    def __init__(self, binary_path: str | Path | None = None):
        """Initialize the CEL evaluator.

        Args:
            binary_path: Path to cel-evaluator binary. Defaults to ./cel-evaluator
                        relative to the api_parity package.
        """
        self._binary_path = Path(binary_path) if binary_path else self.DEFAULT_BINARY_PATH
        self._process: subprocess.Popen | None = None
        self._restart_count = 0
        self._start_subprocess()

    def __enter__(self) -> "CELEvaluator":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _start_subprocess(self) -> None:
        """Start the Go subprocess and wait for ready signal."""
        if not self._binary_path.exists():
            raise CELSubprocessError(f"CEL evaluator binary not found: {self._binary_path}")

        self._process = subprocess.Popen(
            [str(self._binary_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Wait for ready signal with timeout
        ready, _, _ = select.select([self._process.stdout], [], [], self.STARTUP_TIMEOUT)
        if not ready:
            self._cleanup_process()
            raise CELSubprocessError(
                f"CEL subprocess startup timeout ({self.STARTUP_TIMEOUT}s)"
            )

        ready_line = self._process.stdout.readline()
        if not ready_line:
            # Read stderr with timeout to avoid blocking
            stderr = ""
            stderr_ready, _, _ = select.select([self._process.stderr], [], [], 1.0)
            if stderr_ready:
                stderr = self._process.stderr.read(4096)  # Read available data, don't block
            raise CELSubprocessError(f"CEL subprocess died during startup: {stderr}")

        try:
            ready_msg = json.loads(ready_line)
            if not ready_msg.get("ready"):
                raise CELSubprocessError(f"Unexpected ready message: {ready_line}")
        except json.JSONDecodeError as e:
            raise CELSubprocessError(f"Invalid ready message: {ready_line}") from e

    def _restart_subprocess(self) -> None:
        """Restart the subprocess after a crash."""
        if self._restart_count >= self.MAX_RESTARTS:
            raise CELSubprocessError(
                f"CEL subprocess crashed {self.MAX_RESTARTS} times, giving up"
            )

        self._restart_count += 1
        self._cleanup_process()
        self._start_subprocess()

    def _cleanup_process(self) -> None:
        """Clean up the subprocess if it exists."""
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.stdout.close()
            except Exception:
                pass
            try:
                self._process.stderr.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=1)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def evaluate(self, expression: str, data: dict[str, Any]) -> bool:
        """Evaluate a CEL expression with the given data.

        Args:
            expression: CEL expression string (e.g., "a == b")
            data: Dictionary of variable bindings (e.g., {"a": 1, "b": 1})

        Returns:
            Boolean result of the expression evaluation.

        Raises:
            CELEvaluationError: If the expression fails to evaluate.
            CELSubprocessError: If the subprocess crashes and cannot be restarted.
        """
        if self._process is None:
            raise CELSubprocessError("CEL evaluator not running")

        request_id = str(uuid.uuid4())
        request = {"id": request_id, "expr": expression, "data": data}

        try:
            # Send request
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()

            # Wait for response with timeout to prevent indefinite blocking
            ready, _, _ = select.select(
                [self._process.stdout], [], [], self.EVALUATION_TIMEOUT
            )
            if not ready:
                raise CELEvaluationError(
                    f"CEL evaluation timeout ({self.EVALUATION_TIMEOUT}s)"
                )

            # Read response
            response_line = self._process.stdout.readline()
            if not response_line:
                # EOF - subprocess died
                self._restart_subprocess()
                # Retry the request after restart
                return self.evaluate(expression, data)

            response = json.loads(response_line)

        except BrokenPipeError:
            # Subprocess died while writing
            self._restart_subprocess()
            return self.evaluate(expression, data)
        except json.JSONDecodeError as e:
            raise CELEvaluationError(f"Invalid response from subprocess: {response_line}") from e

        # Validate response ID matches request
        if response.get("id") != request_id:
            raise CELEvaluationError(
                f"Response ID mismatch: expected {request_id}, got {response.get('id')}"
            )

        if not response.get("ok"):
            raise CELEvaluationError(response.get("error", "Unknown CEL evaluation error"))

        return response["result"]

    def close(self) -> None:
        """Shut down the CEL evaluator subprocess."""
        self._cleanup_process()

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is running."""
        return self._process is not None and self._process.poll() is None
