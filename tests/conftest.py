"""Pytest configuration and fixtures for api-parity tests.

This file provides:
- PortReservation: Race-free port allocation for test servers
- MockServer: Subprocess management for mock API servers
- Fixtures: Shared test infrastructure (specs, servers, CEL evaluator)
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator

import pytest

from api_parity.models import ResponseCase

# Project root for fixture paths
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
MOCK_SERVER_MODULE = "tests.integration.mock_server"


def make_response_case(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: Any = None,
    body_base64: str | None = None,
    elapsed_ms: float = 10.0,
) -> ResponseCase:
    """Create a ResponseCase for testing comparisons.

    Prefer this over constructing ResponseCase directly - it provides
    sensible defaults and documents which fields are typically varied in tests.
    """
    return ResponseCase(
        status_code=status_code,
        headers=headers or {},
        body=body,
        body_base64=body_base64,
        elapsed_ms=elapsed_ms,
    )


# Backward-compatible alias (used by many existing tests)
make_response = make_response_case


class PortReservation:
    """Holds a reserved port with socket kept open to prevent races.

    WHY this exists: find_free_port() has a race window - another process can
    grab the port between when we find it and when our server binds. This class
    keeps the socket open until just before the server starts, eliminating the race.

    Usage:
        reservation = PortReservation()
        # port is held exclusively until release()
        server = MockServer(reservation, variant="a")
        server.start()  # calls release() internally, then binds
    """

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))  # Port 0 = OS assigns ephemeral port
        self._port = self._socket.getsockname()[1]
        self._released = False

    @property
    def port(self) -> int:
        return self._port

    def release(self) -> int:
        """Release the socket and return the port for server use.

        Safe to call multiple times - subsequent calls are no-ops.
        """
        if not self._released:
            self._socket.close()
            self._released = True
        return self._port

    def __enter__(self) -> PortReservation:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def find_free_port() -> int:
    """Find an available port on localhost.

    WARNING: Race condition exists between this returning and a server binding.
    Prefer PortReservation for production test code. This function is kept for
    simple one-off cases where the race risk is acceptable.
    """
    with PortReservation() as reservation:
        return reservation.port


def wait_for_server_ready(host: str, port: int, timeout: float = 10.0) -> bool:
    """Block until server accepts TCP connections, or timeout expires."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    return False


class MockServer:
    """Manages a mock server subprocess for integration tests.

    Runs tests/integration/mock_server.py as a subprocess. The server
    implements the test API spec with configurable "variant" behavior
    to simulate differences between API implementations.
    """

    def __init__(self, port: int | PortReservation, variant: str = "a") -> None:
        """Initialize mock server configuration.

        Args:
            port: Either a port number or PortReservation. Using PortReservation
                  is preferred as it eliminates port allocation races.
            variant: Server behavior variant ("a" or "b"). Different variants
                     return slightly different responses to test differential comparison.
        """
        if isinstance(port, PortReservation):
            self._reservation = port
            self.port = port.port
        else:
            self._reservation = None
            self.port = port
        self.variant = variant
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        """Start the mock server subprocess.

        Raises:
            RuntimeError: If server fails to start within 10 seconds.
        """
        if self._reservation:
            self._reservation.release()

        self._process = subprocess.Popen(
            [
                sys.executable, "-m", MOCK_SERVER_MODULE,
                "--host", self.host,
                "--port", str(self.port),
                "--variant", self.variant,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=PROJECT_ROOT,
        )

        if not wait_for_server_ready(self.host, self.port):
            stderr = ""
            if self._process and self._process.stderr:
                stderr = self._process.stderr.read().decode(errors="replace")
            self.stop()
            raise RuntimeError(
                f"MockServer(variant={self.variant}) failed to start on port {self.port}. "
                f"stderr: {stderr or '(empty)'}"
            )

    def stop(self) -> None:
        """Stop the mock server subprocess with graceful shutdown.

        Uses SIGTERM first, then SIGKILL after 5s if process doesn't exit.
        Safe to call multiple times or if server was never started.
        """
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Process ignored SIGTERM, escalate to SIGKILL
                self._process.kill()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # Process is unkillable (zombie?), nothing more we can do
            self._process = None

    def __enter__(self) -> MockServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def fixture_test_api_spec() -> Path:
    """Path to test OpenAPI specification (tests/fixtures/test_api.yaml)."""
    return FIXTURES_DIR / "test_api.yaml"


@pytest.fixture(scope="session")
def fixture_comparison_rules_path() -> Path:
    """Path to comparison rules config (tests/fixtures/comparison_rules.json)."""
    return FIXTURES_DIR / "comparison_rules.json"


@pytest.fixture(scope="session")
def fixture_dual_mock_servers() -> Generator[dict[str, MockServer], None, None]:
    """Start both server variants for differential testing.

    Session-scoped: servers start once per test session for performance.
    Yields dict with keys "a" and "b" mapping to MockServer instances.

    Example:
        def test_diff(fixture_dual_mock_servers):
            server_a = fixture_dual_mock_servers["a"]
            server_b = fixture_dual_mock_servers["b"]
            # server_a.base_url, server_b.base_url available
    """
    reservation_a = PortReservation()
    reservation_b = PortReservation()

    with MockServer(reservation_a, variant="a") as server_a:
        with MockServer(reservation_b, variant="b") as server_b:
            yield {"a": server_a, "b": server_b}


@pytest.fixture(scope="function")
def fixture_cel_evaluator_path() -> Path:
    """Return path to CEL evaluator binary, or skip test if not built.

    The CEL evaluator is a Go binary that must be built before running tests
    that use it. Skip message includes the build command.
    """
    cel_path = PROJECT_ROOT / "cel-evaluator"
    if not cel_path.exists():
        pytest.skip(
            "CEL evaluator binary not built. "
            "Run: go build -o cel-evaluator ./cmd/cel-evaluator"
        )
    return cel_path


# Backward-compatible fixture aliases


@pytest.fixture(scope="session")
def test_api_spec(fixture_test_api_spec: Path) -> Path:
    """Alias for fixture_test_api_spec (backward compatibility)."""
    return fixture_test_api_spec


@pytest.fixture(scope="session")
def comparison_rules_path(fixture_comparison_rules_path: Path) -> Path:
    """Alias for fixture_comparison_rules_path (backward compatibility)."""
    return fixture_comparison_rules_path


@pytest.fixture(scope="session")
def dual_servers(
    fixture_dual_mock_servers: dict[str, MockServer],
) -> dict[str, MockServer]:
    """Alias for fixture_dual_mock_servers (backward compatibility)."""
    return fixture_dual_mock_servers


@pytest.fixture(scope="function")
def cel_evaluator_exists(fixture_cel_evaluator_path: Path) -> Path:
    """Alias for fixture_cel_evaluator_path (backward compatibility)."""
    return fixture_cel_evaluator_path


# =============================================================================
# Pytest Hooks
# =============================================================================


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Automatically apply markers based on test location.

    This hook runs after test collection and tags tests with markers
    based on their directory location. Enables running subsets via:
        pytest -m integration  # only integration tests
        pytest -m unit         # only unit tests
    """
    for item in items:
        test_path = Path(item.fspath)
        if "integration" in test_path.parts:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
