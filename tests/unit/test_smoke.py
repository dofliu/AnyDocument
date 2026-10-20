"""Smoke tests for the package skeleton."""

import re

import anydoc


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", anydoc.__version__)


def test_public_api_exports_version() -> None:
    assert "__version__" in anydoc.__all__
