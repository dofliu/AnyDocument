"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures.generate import SAMPLES, generate_all


@pytest.fixture(scope="session")
def generated_samples() -> dict[str, Path]:
    """Generate all sample files once per session."""
    return generate_all()


@pytest.fixture
def sample(generated_samples: dict[str, Path]) -> Callable[[str], Path]:
    """Return ``sample(fmt)`` -> path of a generated sample (see ``SAMPLES`` for ids)."""

    def _get(fmt: str) -> Path:
        if fmt not in SAMPLES:
            raise KeyError(f"unknown sample format {fmt!r}; known: {sorted(SAMPLES)}")
        return generated_samples[fmt]

    return _get


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests marked ``requires(tool)`` when the external tool is not installed."""
    from anydoc.external import is_available

    for marker in item.iter_markers(name="requires"):
        for tool in marker.args:
            if not is_available(tool):
                pytest.skip(f"external tool not available: {tool}")
