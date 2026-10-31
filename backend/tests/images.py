"""Real (tiny) images for upload tests. The app now verifies and re-encodes every photo, so fake bytes no longer pass."""
from io import BytesIO

from PIL import Image


def png_bytes(size=(8, 8), color=(15, 46, 34)) -> bytes:
    b = BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def jpeg_bytes(size=(40, 30), with_gps=False, orientation=None) -> bytes:
    """A JPEG that optionally carries a GPS location, a camera name and a rotation flag, like a phone photo."""
    im = Image.new("RGB", size, (200, 120, 40))
    exif = Image.Exif()
    if with_gps:
        exif[0x010F] = "SecretPhoneBrand"
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (5.0, 33.0, 22.0), "W", (0.0, 10.0, 59.0)
    if orientation:
        exif[0x0112] = orientation
    b = BytesIO()
    im.save(b, "JPEG", exif=exif)
    return b.getvalue()


PNG = png_bytes()
