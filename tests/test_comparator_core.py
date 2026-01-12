"""Unit tests for Comparator core functionality and NOT_FOUND sentinel."""

from api_parity.comparator import NOT_FOUND, _NotFound

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


class TestNotFoundSentinel:
    """Tests for the NOT_FOUND sentinel."""

    def test_singleton(self):
        """NOT_FOUND is a singleton."""
        sentinel1 = _NotFound()
        sentinel2 = _NotFound()
        assert sentinel1 is sentinel2
        assert sentinel1 is NOT_FOUND

    def test_distinct_from_none(self):
        """NOT_FOUND is distinct from None."""
        assert NOT_FOUND is not None
        assert NOT_FOUND != None  # noqa: E711

    def test_repr(self):
        """NOT_FOUND has a useful repr."""
        assert repr(NOT_FOUND) == "<NOT_FOUND>"
