import pytest

from anydoc.core import errors


@pytest.mark.parametrize(
    "cls",
    [
        errors.UnsupportedFormatError,
        errors.CorruptFileError,
        errors.ExternalToolMissingError,
        errors.SecurityLimitError,
        errors.OperationNotSupportedError,
    ],
)
def test_all_errors_derive_from_anydoc_error(cls: type[Exception]) -> None:
    assert issubclass(cls, errors.AnyDocError)
    assert issubclass(errors.AnyDocError, Exception)


def test_errors_are_distinct_siblings() -> None:
    assert not issubclass(errors.CorruptFileError, errors.UnsupportedFormatError)


def test_catch_base_class() -> None:
    with pytest.raises(errors.AnyDocError):
        raise errors.SecurityLimitError("too big")


def test_external_tool_missing_carries_tool_name() -> None:
    exc = errors.ExternalToolMissingError("libreoffice")
    assert exc.tool == "libreoffice"
    assert "libreoffice" in str(exc)


def test_external_tool_missing_custom_message() -> None:
    exc = errors.ExternalToolMissingError("tesseract", "OCR unavailable")
    assert str(exc) == "OCR unavailable"
