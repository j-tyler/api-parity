"""Pytest configuration and fixtures for api-parity tests."""

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from api_parity.models import ResponseCase

# Project root for fixture paths
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
MOCK_SERVER_MODULE = "tests.integration.mock_server"


def make_response(
    status_code: int = 200,
    headers: dict = None,
    body: Any = None,
    elapsed_ms: float = 10.0,
) -> ResponseCase:
    """Helper to create ResponseCase instances for testing."""
    return ResponseCase(
        status_code=status_code,
        headers=headers or {},
        body=body,
        elapsed_ms=elapsed_ms,
    )


class PortReservation:
    """Holds a reserved port with socket kept open to prevent races.

    Use release() just before starting a server on the port.
    The socket uses SO_REUSEADDR so the port can be immediately reused.
    """

    def __init__(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._port = self._socket.getsockname()[1]
        self._released = False

    @property
    def port(self) -> int:
        return self._port

    def release(self) -> int:
        """Release the socket and return the port for server use."""
        if not self._released:
            self._socket.close()
            self._released = True
        return self._port

    def __enter__(self) -> "PortReservation":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def find_free_port() -> int:
    """Find an available port on localhost.

    Note: There's a small race window between this returning and a server
    binding to the port. For tighter control, use PortReservation directly.
    Uses SO_REUSEADDR to minimize port exhaustion issues.
    """
    with PortReservation() as reservation:
        return reservation.port


def wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    """Wait for a server to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    return False


class MockServer:
    """Manages a mock server subprocess."""

    def __init__(self, port: int | PortReservation, variant: str = "a"):
        # Accept either a port number or a PortReservation for tighter control
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
        """Start the mock server subprocess."""
        # Release the reservation just before starting the server
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

        if not wait_for_server(self.host, self.port):
            stderr = ""
            if self._process and self._process.stderr:
                stderr = self._process.stderr.read().decode(errors="replace")
            self.stop()
            raise RuntimeError(f"Mock server failed to start on port {self.port}: {stderr}")

    def stop(self) -> None:
        """Stop the mock server subprocess."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None

    def __enter__(self) -> "MockServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


@pytest.fixture(scope="session")
def test_api_spec() -> Path:
    """Path to test OpenAPI specification."""
    return FIXTURES_DIR / "test_api.yaml"


@pytest.fixture(scope="session")
def comparison_rules_path() -> Path:
    """Path to comparison rules config."""
    return FIXTURES_DIR / "comparison_rules.json"


@pytest.fixture(scope="function")
def mock_server_a() -> MockServer:
    """Start mock server variant A on a random port."""
    reservation = PortReservation()
    with MockServer(reservation, variant="a") as server:
        yield server


@pytest.fixture(scope="function")
def mock_server_b() -> MockServer:
    """Start mock server variant B on a random port."""
    reservation = PortReservation()
    with MockServer(reservation, variant="b") as server:
        yield server


@pytest.fixture(scope="function")
def dual_servers():
    """Start both server variants for differential testing."""
    reservation_a = PortReservation()
    reservation_b = PortReservation()

    with MockServer(reservation_a, variant="a") as server_a:
        with MockServer(reservation_b, variant="b") as server_b:
            yield {"a": server_a, "b": server_b}


@pytest.fixture(scope="function")
def cel_evaluator_exists():
    """Check that the CEL evaluator binary exists, skip if not."""
    cel_path = PROJECT_ROOT / "cel-evaluator"
    if not cel_path.exists():
        pytest.skip("CEL evaluator binary not built. Run: go build -o cel-evaluator ./cmd/cel-evaluator")
    return cel_path


def pytest_collection_modifyitems(config, items):
    """Automatically apply markers based on test location.

    - tests/integration/* -> integration marker
    - tests/* (not integration) -> unit marker
    """
    for item in items:
        # Get relative path from tests directory
        test_path = Path(item.fspath)
        if "integration" in test_path.parts:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
