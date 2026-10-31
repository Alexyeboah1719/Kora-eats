"""Validated file uploads. We never trust the filename or Content-Type: the file's own first bytes
decide its type, the size is capped, and the stored name is random (no path tricks possible)."""
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import api_error

Image.MAX_IMAGE_PIXELS = 50_000_000            # decompression-bomb guard: Pillow refuses images of 2x this size
MAX_SIDE = 2000                                # longest edge we keep (phones take 4000+ px photos; 2000 is plenty for a menu)
FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
IMAGE_TYPES = {"jpg", "png", "webp"}
DOC_TYPES = IMAGE_TYPES | {"pdf"}


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF-"):
        return "pdf"
    return None


def public_dir() -> Path:
    p = Path(get_settings().upload_dir) / "public"
    p.mkdir(parents=True, exist_ok=True)
    return p


def private_dir() -> Path:
    p = Path(get_settings().upload_dir) / "private"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_checked(file: UploadFile, allowed: set[str]) -> tuple[bytes, str]:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise api_error(413, "file_too_large", f"File is too large (max {get_settings().max_upload_mb} MB).")
    kind = _sniff(data[:16])
    if kind is None or kind not in allowed:
        raise api_error(415, "bad_file_type", "Unsupported file. Allowed: " + ", ".join(sorted(allowed)).upper() + ".")
    return data, kind


def _bad_image():
    return api_error(415, "bad_file_type", "That image couldn't be read. Try a different photo (JPG, PNG or WebP).")


def clean_image(data: bytes) -> tuple[bytes, str]:
    """Re-encode a photo: applies the phone's rotation, then DROPS ALL METADATA (camera model, GPS location...), caps the size,
    and proves the file really is an image. What we store is a fresh file we made ourselves, never the uploaded bytes."""
    try:
        with Image.open(BytesIO(data)) as im:
            kind = FORMATS.get(im.format or "")
            if kind is None:
                raise _bad_image()
            im.load()
            im = ImageOps.exif_transpose(im)
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            out = BytesIO()
            if kind == "jpg":
                im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
            elif kind == "png":
                im.save(out, "PNG", optimize=True)
            else:
                im.save(out, "WEBP", quality=85)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _bad_image()
    return out.getvalue(), kind


def save_public_image(file: UploadFile) -> str:
    """Returns a URL like /uploads/ab12....jpg (safe to show to anyone). The stored file is a clean re-encoding."""
    data, _ = _read_checked(file, IMAGE_TYPES)
    data, kind = clean_image(data)
    name = f"{uuid.uuid4().hex}.{kind}"
    (public_dir() / name).write_bytes(data)
    return f"/uploads/{name}"


def save_private_document(file: UploadFile) -> tuple[str, str]:
    """Returns (stored_name, original_name). Served only to admins via an authenticated endpoint."""
    data, kind = _read_checked(file, DOC_TYPES)
    if kind != "pdf":                                   # document photos are kept as sent, but must really be images
        try:
            with Image.open(BytesIO(data)) as im:
                im.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise _bad_image()
    name = f"{uuid.uuid4().hex}.{kind}"
    (private_dir() / name).write_bytes(data)
    return name, (file.filename or "document")[:255]
