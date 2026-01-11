"""Pytest configuration and fixtures for api-parity tests."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Project root for fixture paths
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
MOCK_SERVER_MODULE = "tests.integration.mock_server"


def find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

    def __init__(self, port: int, variant: str = "a"):
        self.port = port
        self.variant = variant
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        """Start the mock server subprocess."""
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
                self._process.wait()
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
    port = find_free_port()
    with MockServer(port, variant="a") as server:
        yield server


@pytest.fixture(scope="function")
def mock_server_b() -> MockServer:
    """Start mock server variant B on a random port."""
    port = find_free_port()
    with MockServer(port, variant="b") as server:
        yield server


@pytest.fixture(scope="function")
def dual_servers():
    """Start both server variants for differential testing."""
    port_a = find_free_port()
    port_b = find_free_port()

    with MockServer(port_a, variant="a") as server_a:
        with MockServer(port_b, variant="b") as server_b:
            yield {"a": server_a, "b": server_b}
