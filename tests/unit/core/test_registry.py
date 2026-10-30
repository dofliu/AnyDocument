"""Tests for the handler registry."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

import anydoc
from anydoc.core.errors import OperationNotSupportedError, UnsupportedFormatError
from anydoc.core.model import Capability, Document
from anydoc.core.registry import FormatHandler, HandlerRegistry, registry


class FakeHandler(FormatHandler):
    format_id: ClassVar[str] = "fake"
    extensions: ClassVar[tuple[str, ...]] = (".fk", "FAKE")
    mime_types: ClassVar[tuple[str, ...]] = ("application/x-fake",)
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.READ})

    def open(self, source: Path | bytes) -> Document:
        raise NotImplementedError


class OtherHandler(FakeHandler):
    format_id: ClassVar[str] = "other"
    extensions: ClassVar[tuple[str, ...]] = ("fk",)


@pytest.fixture(autouse=True)
def _isolated_global_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with an empty global registry (restored afterwards)."""
    monkeypatch.setattr(registry, "_handlers", {})


def test_register_and_lookup() -> None:
    reg = HandlerRegistry()
    assert reg.register(FakeHandler) is FakeHandler
    assert reg.get("fake") is FakeHandler
    assert reg.get("nope") is None
    assert reg.all() == [FakeHandler]


def test_duplicate_register_rejected_unless_replace() -> None:
    reg = HandlerRegistry()
    reg.register(FakeHandler)
    with pytest.raises(ValueError):
        reg.register(FakeHandler)
    reg.register(FakeHandler, replace=True)


def test_lookup_by_extension_is_case_and_dot_insensitive() -> None:
    reg = HandlerRegistry()
    reg.register(FakeHandler)
    reg.register(OtherHandler)
    assert {h.format_id for h in reg.by_extension(".FK")} == {"fake", "other"}
    assert reg.by_extension("fake") == [FakeHandler]
    assert reg.by_extension("zzz") == []


def test_lookup_by_mime() -> None:
    reg = HandlerRegistry()
    reg.register(FakeHandler)
    assert reg.by_mime("Application/X-Fake") == [FakeHandler]
    assert reg.by_mime("text/plain") == []


def test_unregister() -> None:
    reg = HandlerRegistry()
    reg.register(FakeHandler)
    reg.unregister("fake")
    reg.unregister("fake")
    assert reg.get("fake") is None


def test_handler_defaults() -> None:
    assert FakeHandler.sniff(b"x", None) == 0.0
    with pytest.raises(OperationNotSupportedError):
        FakeHandler().create()


def test_load_entry_points() -> None:
    ep_ok = MagicMock()
    ep_ok.load.return_value = FakeHandler
    ep_bad = MagicMock()
    ep_bad.load.return_value = object()
    reg = HandlerRegistry()
    with patch("anydoc.core.registry.entry_points", return_value=[ep_ok, ep_bad]):
        assert reg.load_entry_points() == ["fake"]
        assert reg.load_entry_points() == []
    assert reg.get("fake") is FakeHandler


def test_open_dispatches_to_registered_handler(tmp_path: Path) -> None:
    sentinel = MagicMock()

    class RtfHandler(FakeHandler):
        format_id: ClassVar[str] = "rtf"

        def open(self, source: Path | bytes) -> Document:
            sentinel.source = source
            return sentinel  # type: ignore[no-any-return]

    f = tmp_path / "a.rtf"
    f.write_bytes(b"{\\rtf1\\ansi hello}")
    registry.register(RtfHandler)
    try:
        assert anydoc.open(f) is sentinel
        assert sentinel.source == f
    finally:
        registry.unregister("rtf")


def test_open_without_handler_raises(tmp_path: Path) -> None:
    f = tmp_path / "a.rtf"
    f.write_bytes(b"{\\rtf1\\ansi hello}")
    with (
        patch("anydoc.core.registry.entry_points", return_value=[]),
        pytest.raises(UnsupportedFormatError),
    ):
        anydoc.open(f)


def test_open_loads_entry_point_handlers(tmp_path: Path) -> None:
    sentinel = MagicMock()

    class RtfEpHandler(FakeHandler):
        format_id: ClassVar[str] = "rtf"

        def open(self, source: Path | bytes) -> Document:
            return sentinel  # type: ignore[no-any-return]

    ep = MagicMock()
    ep.load.return_value = RtfEpHandler
    f = tmp_path / "a.rtf"
    f.write_bytes(b"{\\rtf1\\ansi hello}")
    try:
        with patch("anydoc.core.registry.entry_points", return_value=[ep]):
            assert anydoc.open(f) is sentinel
    finally:
        registry.unregister("rtf")
