"""PDF encryption, decryption and permission handling.

Functions take and return PDF ``bytes`` like :mod:`anydoc.formats.pdf.pages`. Encryption
uses pypdf (AES-256 by default; AES-128, RC4-128 and RC4-40 are also available), which needs
the ``cryptography`` package for AES.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions

from anydoc.core.errors import CorruptFileError, OperationNotSupportedError

__all__ = [
    "ALGORITHMS",
    "PERMISSION_NAMES",
    "Permissions",
    "decrypt_pdf",
    "encrypt_pdf",
    "get_permissions",
    "is_encrypted",
]

ALGORITHMS = {
    "AES-256": "AES-256",
    "AES-128": "AES-128",
    "RC4-128": "RC4-128",
    "RC4-40": "RC4-40",
}

_FLAGS: dict[str, UserAccessPermissions] = {
    "print": UserAccessPermissions.PRINT,
    "modify": UserAccessPermissions.MODIFY,
    "copy": UserAccessPermissions.EXTRACT,
    "annotate": UserAccessPermissions.ADD_OR_MODIFY,
    "fill_forms": UserAccessPermissions.FILL_FORM_FIELDS,
    "accessibility": UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS,
    "assemble": UserAccessPermissions.ASSEMBLE_DOC,
    "print_high_quality": UserAccessPermissions.PRINT_TO_REPRESENTATION,
}
PERMISSION_NAMES = tuple(_FLAGS)


@dataclass(frozen=True)
class Permissions:
    """User permissions of a PDF (all ``True`` for an unencrypted document)."""

    print: bool = True
    modify: bool = True
    copy: bool = True
    annotate: bool = True
    fill_forms: bool = True
    accessibility: bool = True
    assemble: bool = True
    print_high_quality: bool = True

    def to_dict(self) -> dict[str, bool]:
        """Return the permissions as a plain dict."""
        return asdict(self)

    def to_flags(self) -> UserAccessPermissions:
        """Return the pypdf permission flags (reserved bits set as required by the spec)."""
        flags = UserAccessPermissions(0)
        for name, flag in _FLAGS.items():
            if getattr(self, name):
                flags |= flag
        # Bits 7-8 and 13-32 are reserved and must be 1.
        return UserAccessPermissions(int(flags) | 0xFFFFF0C0)

    @classmethod
    def from_flags(cls, flags: int) -> Permissions:
        """Build permissions from a raw ``/P`` value."""
        return cls(**{name: bool(flags & int(flag)) for name, flag in _FLAGS.items()})

    @classmethod
    def only(cls, *allowed: str) -> Permissions:
        """Return permissions where only the named operations are allowed."""
        unknown = set(allowed) - set(_FLAGS)
        if unknown:
            raise ValueError(f"Unknown permission(s): {', '.join(sorted(unknown))}")
        return cls(**{name: name in allowed for name in _FLAGS})


def _open(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise CorruptFileError(f"Invalid PDF: {exc}") from exc


def is_encrypted(data: bytes) -> bool:
    """Return whether the PDF has an encryption dictionary."""
    return bool(_open(data).is_encrypted)


def _unlock(reader: PdfReader, password: str) -> None:
    if reader.is_encrypted and not reader.decrypt(password):
        raise OperationNotSupportedError("Wrong password for encrypted PDF")


def get_permissions(data: bytes, password: str = "") -> Permissions:
    """Return the permissions granted to the user of this PDF.

    For an unencrypted PDF everything is allowed. An encrypted PDF that cannot be opened
    with ``password`` raises ``OperationNotSupportedError``; opening with the owner
    password still reports the document's stored user permissions.
    """
    reader = _open(data)
    if not reader.is_encrypted:
        return Permissions()
    _unlock(reader, password)
    encrypt_dict: Any = reader.trailer["/Encrypt"]
    raw = encrypt_dict.get_object().get("/P", -1)
    return Permissions.from_flags(int(raw) & 0xFFFFFFFF)


def encrypt_pdf(
    data: bytes,
    user_password: str = "",
    *,
    owner_password: str | None = None,
    permissions: Permissions | None = None,
    algorithm: str = "AES-256",
    source_password: str = "",
) -> bytes:
    """Encrypt a PDF with a user (open) password and an owner password.

    Args:
        data: The PDF bytes (may already be encrypted if ``source_password`` unlocks it).
        user_password: Password required to open the file (empty = open freely, but the
            ``permissions`` restrictions still apply).
        owner_password: Password with full rights; defaults to ``user_password`` (when both
            are empty a random owner password is generated, locking the restrictions).
        permissions: Allowed operations; defaults to everything.
        algorithm: One of :data:`ALGORITHMS`.
        source_password: Password to open an already encrypted input.
    """
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unknown algorithm {algorithm!r}; choose from {sorted(ALGORITHMS)}")
    if not user_password and not owner_password and permissions is None:
        raise ValueError("Give a user password, an owner password or restricted permissions")
    reader = _open(data)
    _unlock(reader, source_password)
    writer = PdfWriter(clone_from=reader)
    owner = owner_password if owner_password is not None else (user_password or None)
    if owner is None:
        import secrets

        owner = secrets.token_hex(16)
    try:
        writer.encrypt(
            user_password=user_password,
            owner_password=owner,
            permissions_flag=(permissions or Permissions()).to_flags(),
            algorithm=ALGORITHMS[algorithm],
        )
    except ImportError as exc:  # cryptography missing for AES
        raise OperationNotSupportedError(f"{algorithm} requires 'cryptography': {exc}") from exc
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def decrypt_pdf(data: bytes, password: str = "") -> bytes:
    """Remove encryption from a PDF; ``password`` may be the user or owner password.

    An unencrypted PDF is returned re-serialised unchanged in content.
    """
    reader = _open(data)
    _unlock(reader, password)
    writer = PdfWriter(clone_from=reader)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
