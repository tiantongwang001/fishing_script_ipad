"""Bounded local WDA screenshot-stream reader; never launches apps or sends input.

Timestamps measure host receipt, not device capture. WDA supplies no capture
timestamp, so even a recent, different frame does not prove low source latency.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
import hashlib
import http.client
import io
import math
import socket
import threading
import time
from urllib.parse import urlsplit

from PIL import Image, ImageOps


class MJPEGError(RuntimeError):
    """The local stream ended or supplied invalid screenshot data."""


@dataclass(frozen=True)
class MJPEGFrame:
    sequence: int
    received_at: float
    sha256: str
    size: tuple[int, int]
    rgb: bytes
    source_capture_time: None = None

    def to_image(self) -> Image.Image:
        """Return a caller-owned RGB image; reader state stays immutable."""
        return Image.frombytes("RGB", self.size, self.rgb)


def _seconds(value: float, name: str, *, allow_zero: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value > 30
            or (value < 0 if allow_zero else value <= 0)):
        raise ValueError(f"{name} must be finite seconds in {'[0' if allow_zero else '(0'}, 30]")
    return float(value)


def _local_address(url: str) -> tuple[str, int, str]:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port is None or not 1 <= parsed.port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or url != url.strip()
                or any(char in url for char in "\\\r\n\t")):
            raise ValueError
        # Do not depend on DNS or HTTP proxy configuration, even for localhost.
        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        return host, parsed.port, parsed.path or "/"
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("MJPEG URL must be plain HTTP on localhost with an explicit port, without credentials/query/fragment") from exc


class _MultipartParser:
    """Strict lengths; accepts WDA's nonstandard already-prefixed boundary."""
    HEADER_LIMIT = 16384

    def __init__(self, content_type: str, max_frame_bytes: int):
        message = Message()
        message["content-type"] = content_type
        boundary = message.get_param("boundary")
        if message.get_content_type() != "multipart/x-mixed-replace" or not isinstance(boundary, str):
            raise MJPEGError("Expected multipart/x-mixed-replace with a boundary")
        try:
            encoded = boundary.encode("ascii")
        except UnicodeError as exc:
            raise MJPEGError("Non-ASCII multipart boundary") from exc
        if not 1 <= len(encoded) <= 70 or any(byte < 33 or byte > 126 for byte in encoded):
            raise MJPEGError("Invalid multipart boundary")
        self.boundaries = {b"--" + encoded}
        if encoded.startswith(b"--"):
            self.boundaries.add(encoded)
        self.max_frame_bytes = max_frame_bytes
        self.buffer = bytearray()
        self.state = "boundary"
        self.length = 0
        self.preamble_bytes = 0

    def feed(self, data: bytes) -> list[bytes]:
        # The network reader uses 64 KiB chunks. Bound parser memory separately.
        if len(data) > 65536:
            raise MJPEGError("Oversized stream read")
        self.buffer.extend(data)
        if len(self.buffer) > self.max_frame_bytes + self.HEADER_LIMIT + 65536:
            raise MJPEGError("Multipart buffer limit exceeded")
        frames = []
        while True:
            if self.state == "boundary":
                end = self.buffer.find(b"\r\n")
                if end < 0:
                    if len(self.buffer) > self.HEADER_LIMIT:
                        raise MJPEGError("Multipart boundary line too long")
                    break
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 2]
                if line in self.boundaries:
                    self.state = "headers"
                    self.preamble_bytes = 0
                elif line in {value + b"--" for value in self.boundaries}:
                    raise MJPEGError("MJPEG stream ended")
                else:
                    self.preamble_bytes += end + 2
                    if self.preamble_bytes > self.HEADER_LIMIT:
                        raise MJPEGError("Multipart boundary not found")
            elif self.state == "headers":
                end = self.buffer.find(b"\r\n\r\n")
                if end < 0:
                    if len(self.buffer) > self.HEADER_LIMIT:
                        raise MJPEGError("Multipart headers too long")
                    break
                if end > self.HEADER_LIMIT:
                    raise MJPEGError("Multipart headers too long")
                headers = {}
                for line in bytes(self.buffer[:end]).split(b"\r\n"):
                    key, separator, value = line.partition(b":")
                    key = key.lower().strip()
                    if not separator or not key or key in headers:
                        raise MJPEGError("Malformed or repeated multipart header")
                    headers[key] = value.strip()
                length = headers.get(b"content-length", b"")
                if not length.isdigit() or len(length) > 10:
                    raise MJPEGError("A numeric multipart Content-Length is required")
                self.length = int(length)
                if not 1 <= self.length <= self.max_frame_bytes:
                    raise MJPEGError("JPEG Content-Length exceeds allowed bounds")
                if headers.get(b"content-type", b"").lower() != b"image/jpeg":
                    raise MJPEGError("Multipart part is not image/jpeg")
                del self.buffer[:end + 4]
                self.state = "body"
            else:
                if len(self.buffer) < self.length:
                    break
                frames.append(bytes(self.buffer[:self.length]))
                del self.buffer[:self.length]
                self.state = "boundary"
        return frames


class MJPEGReader:
    """One local stream, one latest frame, one sequential consumer; no retries.

    ``next_frame`` waits for a frame after the last returned frame (or explicit
    ``after``), optionally requiring different decoded pixels. It never queues
    frames. ``max_age`` bounds host-receipt age only; source latency is unknown.
    An error or closed reader fails closed instead of returning cached content.
    """
    def __init__(self, url: str = "http://127.0.0.1:9100/", *, network_timeout: float = 1.0,
                 max_frame_bytes: int = 8 * 1024 * 1024,
                 max_dimension: int = 8192, max_pixels: int = 16_000_000):
        self.address = _local_address(url)
        self.network_timeout = _seconds(network_timeout, "network_timeout")
        if self.network_timeout > 3:
            raise ValueError("network_timeout must be at most 3 seconds")
        for name, value, upper in (("max_frame_bytes", max_frame_bytes, 32 * 1024 * 1024),
                                   ("max_dimension", max_dimension, 16384),
                                   ("max_pixels", max_pixels, 32_000_000)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be an integer in 1..{upper}")
        self.max_frame_bytes = max_frame_bytes
        self.max_dimension = max_dimension
        self.max_pixels = max_pixels
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: http.client.HTTPConnection | None = None
        self._socket: socket.socket | None = None
        self._latest: MJPEGFrame | None = None
        self._last_returned: MJPEGFrame | None = None
        self._error: Exception | None = None
        self._sequence = 0

    def start(self) -> MJPEGReader:
        with self._condition:
            if self._thread is not None or self._stop.is_set():
                raise MJPEGError("MJPEGReader can only be started once")
            self._thread = threading.Thread(target=self._consume, name="local-wda-mjpeg", daemon=True)
            self._thread.start()
        return self

    def __enter__(self) -> MJPEGReader:
        return self.start()

    def __exit__(self, *_):
        self.close()

    def _decode(self, data: bytes, received_at: float, sequence: int) -> MJPEGFrame:
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise MJPEGError("Incomplete JPEG frame")
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if (image.format != "JPEG" or width < 1 or height < 1
                    or max(width, height) > self.max_dimension or width * height > self.max_pixels):
                raise MJPEGError("JPEG dimensions exceed allowed bounds")
            image.load()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            rgb = normalized.tobytes()
            digest = hashlib.sha256(str(normalized.size).encode("ascii") + b":" + rgb).hexdigest()
            return MJPEGFrame(sequence, received_at, digest, normalized.size, rgb)

    def _consume(self):
        host, port, path = self.address
        connection = http.client.HTTPConnection(host, port, timeout=self.network_timeout)
        response = None
        with self._condition:
            self._connection = connection
        try:
            if self._stop.is_set():
                return
            connection.connect()
            with self._condition:
                # HTTP/1.0 WDA says Connection: close; getresponse transfers
                # socket ownership to its file. Retain it for cancellation.
                self._socket = connection.sock
            if self._stop.is_set():
                return
            connection.request("GET", path, headers={"Accept": "multipart/x-mixed-replace", "Connection": "close"})
            response = connection.getresponse()
            if response.status != 200:
                raise MJPEGError(f"Local MJPEG HTTP status {response.status}; redirects are not followed")
            parser = _MultipartParser(response.getheader("Content-Type", ""), self.max_frame_bytes)
            while not self._stop.is_set():
                data = response.read1(65536)
                received_at = time.monotonic()
                if not data:
                    raise MJPEGError("MJPEG connection ended or frame was truncated")
                pending = parser.feed(data)
                if pending:
                    # Decode only the newest complete frame in this read.
                    self._sequence += len(pending)
                    frame = self._decode(pending[-1], received_at, self._sequence)
                    with self._condition:
                        self._latest = frame
                        self._condition.notify_all()
        except Exception as exc:
            if not self._stop.is_set():
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
        finally:
            if response is not None:
                response.close()
            connection.close()

    def next_frame(self, *, max_wait: float = 1.0, max_age: float = 0.25,
                   after: MJPEGFrame | None = None, require_unique: bool = True) -> MJPEGFrame:
        max_wait = _seconds(max_wait, "max_wait", allow_zero=True)
        max_age = _seconds(max_age, "max_age")
        if not isinstance(require_unique, bool):
            raise ValueError("require_unique must be boolean")
        if after is not None and not isinstance(after, MJPEGFrame):
            raise ValueError("after must be an MJPEGFrame or None")
        deadline = time.monotonic() + max_wait
        with self._condition:
            previous = after if after is not None else self._last_returned
            while True:
                if self._thread is None:
                    raise MJPEGError("MJPEGReader has not been started")
                if self._stop.is_set():
                    raise MJPEGError("MJPEGReader is closed")
                if self._error is not None:
                    raise MJPEGError(f"Local MJPEG stream failed: {self._error}") from self._error
                frame = self._latest
                now = time.monotonic()
                if (frame is not None and 0 <= now - frame.received_at <= max_age
                        and (previous is None or frame.sequence > previous.sequence)
                        and (not require_unique or previous is None or frame.sha256 != previous.sha256)):
                    self._last_returned = frame
                    return frame
                remaining = deadline - now
                if remaining <= 0:
                    raise TimeoutError("No sufficiently recent MJPEG frame satisfying freshness/uniqueness constraints")
                self._condition.wait(remaining)

    def close(self):
        """Cancel reads and wait a bounded interval; never leave a live worker silently."""
        self._stop.set()
        with self._condition:
            connection = self._connection
            sock = self._socket
            self._condition.notify_all()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if connection is not None:
            connection.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(self.network_timeout + 1.0)
            if self._thread.is_alive():
                raise MJPEGError("MJPEG worker did not stop within the close deadline")
