"""Tests for PDF encryption, decryption and permissions (T042)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from typer.testing import CliRunner

from anydoc.cli import app
from anydoc.core.errors import CorruptFileError, OperationNotSupportedError
from anydoc.formats.pdf import PdfHandler
from anydoc.formats.pdf import security as sec

runner = CliRunner()


def _pdf(n: int = 2) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(1, n + 1):
        c.drawString(100, 200, f"BODY{i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _text(data: bytes, password: str = "") -> str:
    r = PdfReader(io.BytesIO(data))
    if r.is_encrypted:
        assert r.decrypt(password)
    return "".join(p.extract_text().strip() for p in r.pages)


@pytest.mark.parametrize("algorithm", list(sec.ALGORITHMS))
def test_encrypt_roundtrip_all_algorithms(algorithm: str) -> None:
    enc = sec.encrypt_pdf(_pdf(), "user", owner_password="owner", algorithm=algorithm)
    assert sec.is_encrypted(enc)
    assert not sec.is_encrypted(_pdf())
    assert "BODY1" in _text(enc, "user")
    assert "BODY2" in _text(enc, "owner")
    plain = sec.decrypt_pdf(enc, "user")
    assert not sec.is_encrypted(plain)
    assert _text(plain) == "BODY1BODY2"


def test_wrong_or_missing_password_rejected() -> None:
    enc = sec.encrypt_pdf(_pdf(), "user", owner_password="owner")
    for bad in ("", "nope"):
        with pytest.raises(OperationNotSupportedError):
            sec.decrypt_pdf(enc, bad)
        with pytest.raises(OperationNotSupportedError):
            sec.get_permissions(enc, bad)


def test_owner_defaults_to_user_password() -> None:
    enc = sec.encrypt_pdf(_pdf(), "same")
    assert _text(sec.decrypt_pdf(enc, "same")) == "BODY1BODY2"


def test_permissions_roundtrip() -> None:
    perms = sec.Permissions.only("print", "copy")
    enc = sec.encrypt_pdf(_pdf(), "", owner_password="o", permissions=perms)
    got = sec.get_permissions(enc)
    assert got == perms
    assert got.to_dict()["print"] is True and got.to_dict()["modify"] is False
    assert sec.get_permissions(_pdf()) == sec.Permissions()
    # opens without a password but restrictions remain; owner password lifts them
    assert "BODY1" in _text(enc)
    assert _text(sec.decrypt_pdf(enc, "o")) == "BODY1BODY2"


def test_restricted_without_passwords_locks_owner() -> None:
    enc = sec.encrypt_pdf(_pdf(), permissions=sec.Permissions.only("print"))
    assert sec.get_permissions(enc).modify is False
    assert _text(enc) == "BODY1BODY2"


def test_reencrypt_encrypted_input() -> None:
    first = sec.encrypt_pdf(_pdf(), "a")
    second = sec.encrypt_pdf(first, "b", source_password="a", algorithm="AES-128")
    assert "BODY1" in _text(second, "b")
    with pytest.raises(OperationNotSupportedError):
        sec.encrypt_pdf(first, "b")


def test_validation_and_errors() -> None:
    with pytest.raises(ValueError):
        sec.encrypt_pdf(_pdf(), "x", algorithm="ROT13")
    with pytest.raises(ValueError):
        sec.encrypt_pdf(_pdf())
    with pytest.raises(ValueError):
        sec.Permissions.only("fly")
    with pytest.raises(CorruptFileError):
        sec.is_encrypted(b"not a pdf")
    assert sec.decrypt_pdf(_pdf()) and not sec.is_encrypted(sec.decrypt_pdf(_pdf()))


def test_document_api(tmp_path: Path) -> None:
    doc = PdfHandler().open(_pdf())
    assert doc.permissions() == sec.Permissions()
    enc = doc.encrypt("pw", owner_password="ow", permissions=sec.Permissions.only("print"))
    assert doc.is_encrypted is False

    locked = PdfHandler().open(enc)
    assert locked.is_locked
    with pytest.raises(OperationNotSupportedError):
        locked.permissions()
    with pytest.raises(OperationNotSupportedError):
        locked.decrypt()

    opened = PdfHandler().open(enc, password="pw")
    assert opened.permissions().modify is False
    assert opened.page_count == 2
    opened.decrypt()
    assert opened.is_encrypted is False
    opened.rotate_pages(90)  # editing works after decrypt
    assert opened.pages()[0].rotation == 90
    out = opened.save(tmp_path / "o.pdf")
    assert not sec.is_encrypted(out.read_bytes())

    # encrypting an already unlocked encrypted document re-uses its password
    again = PdfHandler().open(enc, password="ow").encrypt("new")
    assert "BODY1" in _text(again, "new")


def test_cli_encrypt_decrypt_permissions(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(_pdf())
    enc = tmp_path / "enc.pdf"
    r = runner.invoke(
        app,
        ["pdf", "encrypt", str(src), "-o", str(enc), "--user-password", "u",
         "--owner-password", "o", "--allow", "print,copy"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert sec.is_encrypted(enc.read_bytes())

    r = runner.invoke(app, ["pdf", "permissions", str(enc), "--password", "u"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["encrypted"] is True
    assert data["permissions"]["print"] is True and data["permissions"]["modify"] is False

    r = runner.invoke(app, ["pdf", "permissions", str(enc), "--password", "bad"])
    assert r.exit_code == 1 and "password" in r.output.lower()

    dec = tmp_path / "dec.pdf"
    assert runner.invoke(app, ["pdf", "decrypt", str(enc), "-o", str(dec)]).exit_code == 1
    r = runner.invoke(app, ["pdf", "decrypt", str(enc), "-o", str(dec), "--password", "o"])
    assert r.exit_code == 0
    assert _text(dec.read_bytes()) == "BODY1BODY2"

    r = runner.invoke(app, ["pdf", "encrypt", str(src), "-o", str(enc)])
    assert r.exit_code == 1
    r = runner.invoke(
        app, ["pdf", "encrypt", str(src), "-o", str(enc), "--user-password", "x", "--allow", "fly"]
    )
    assert r.exit_code == 1
    r = runner.invoke(app, ["pdf", "decrypt", str(tmp_path / "missing.pdf"), "-o", str(dec)])
    assert r.exit_code == 2
