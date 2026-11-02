"""Offline byte streams only: no device, Appium server, sockets, or files."""
import io
import queue
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from fishing_probe.mjpeg import MJPEGError, MJPEGReader, _MultipartParser


def jpeg(color="red", size=(20, 10), exif=None):
    output = io.BytesIO()
    options = {"exif": exif} if exif is not None else {}
    Image.new("RGB", size, color).save(output, format="JPEG", **options)
    return output.getvalue()


def part(data, boundary=b"--BoundaryString"):
    return (boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n\r\n")


class FakeResponse:
    status = 200
    def __init__(self):
        self.chunks = queue.Queue()
        self.read_started = threading.Event()
        self.closed = False

    def getheader(self, name, default=None):
        return "multipart/x-mixed-replace; boundary=--BoundaryString"

    def read1(self, count):
        self.read_started.set()
        try:
            result = self.chunks.get(timeout=1)
        except queue.Empty as exc:
            raise TimeoutError("mock read timed out") from exc
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self, response):
        self.response = response

    def shutdown(self, _):
        self.response.chunks.put(b"")


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.sock = FakeSocket(response)
        self.closed = False

    def connect(self):
        pass

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        # WDA uses HTTP/1.0 Connection: close. Its response owns the socket.
        self.sock = None
        return self.response

    def close(self):
        self.closed = True


class TestMultipartParser(unittest.TestCase):
    def parser(self, boundary="--BoundaryString", limit=10000):
        return _MultipartParser(f"multipart/x-mixed-replace; boundary={boundary}", limit)

    def test_wda_and_standard_boundaries_with_fragmented_headers_and_payloads(self):
        first, second = jpeg(), jpeg("blue")
        for advertised, delimiter in (("--BoundaryString", b"--BoundaryString"),
                                       ("normal", b"--normal")):
            with self.subTest(boundary=advertised):
                parser = self.parser(advertised)
                payload = part(first, delimiter) + part(second, delimiter)
                frames = []
                for offset in range(0, len(payload), 7):
                    frames.extend(parser.feed(payload[offset:offset + 7]))
                self.assertEqual(frames, [first, second])

    def test_length_is_required_positive_bounded_and_unambiguous(self):
        for headers in (b"Content-Type: image/jpeg", b"Content-Length: -1\r\nContent-Type: image/jpeg",
                        b"Content-Length: 0\r\nContent-Type: image/jpeg",
                        b"Content-Length: 101\r\nContent-Type: image/jpeg",
                        b"Content-Length: 8\r\ncontent-length: 8\r\nContent-Type: image/jpeg",
                        b"Content-Length: 8\r\nContent-Type: image/png"):
            with self.subTest(headers=headers):
                with self.assertRaises(MJPEGError):
                    self.parser(limit=100).feed(b"--BoundaryString\r\n" + headers + b"\r\n\r\n")

    def test_incomplete_payload_is_not_a_frame(self):
        parser = self.parser()
        self.assertEqual(parser.feed(part(jpeg())[:-20]), [])

    def test_header_preamble_and_chunk_memory_are_bounded(self):
        for payload in (b"X" * 17000, b"x\r\n" * 6000, b"X" * 65537,
                        b"--BoundaryString\r\nX: " + b"x" * 17000):
            with self.subTest(length=len(payload)):
                with self.assertRaises(MJPEGError):
                    self.parser().feed(payload)

    def test_invalid_mime_or_boundary_rejected(self):
        for content_type in ("image/jpeg", "multipart/x-mixed-replace",
                             "multipart/x-mixed-replace; boundary=" + "a" * 71,
                             "multipart/x-mixed-replace; boundary=你好"):
            with self.subTest(content_type=content_type), self.assertRaises(MJPEGError):
                _MultipartParser(content_type, 10000)


class TestMJPEGReader(unittest.TestCase):
    def start_reader(self, **kwargs):
        response = FakeResponse()
        connection = FakeConnection(response)
        patched = patch("fishing_probe.mjpeg.http.client.HTTPConnection", return_value=connection)
        constructor = patched.start()
        self.addCleanup(patched.stop)
        reader = MJPEGReader(**kwargs).start()
        self.addCleanup(reader.close)
        self.assertTrue(response.read_started.wait(1))
        return reader, response, connection, constructor

    def wait_sequence(self, reader, sequence):
        with reader._condition:
            self.assertTrue(reader._condition.wait_for(
                lambda: reader._latest is not None and reader._latest.sequence >= sequence, 1))

    def test_only_loopback_http_with_explicit_port(self):
        for url in ("https://127.0.0.1:9100", "http://192.168.1.1:9100/",
                    "http://localhost/", "http://user@localhost:9100/",
                    "http://localhost:9100/?x=1", "http://localhost:9100/#x",
                    "http://localhost:9100/\n", "http://localhost:0/", "file:///tmp/foo",
                    "http://localhost.example:9100/", None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MJPEGReader(url)
        self.assertEqual(MJPEGReader("http://[::1]:9100/").address, ("::1", 9100, "/"))
        reader, response, connection, constructor = self.start_reader(url="http://localhost:9100/")
        constructor.assert_called_once_with("127.0.0.1", 9100, timeout=1.0)

    def test_latest_frame_wins_and_pixels_are_caller_owned(self):
        reader, response, _, _ = self.start_reader()
        response.chunks.put(part(jpeg("red")) + part(jpeg("blue")))
        frame = reader.next_frame(max_wait=1, max_age=1)
        self.assertEqual(frame.sequence, 2)
        self.assertEqual(frame.size, (20, 10))
        self.assertIsNone(frame.source_capture_time)
        image = frame.to_image()
        self.assertGreater(image.getpixel((0, 0))[2], 200)
        image.putpixel((0, 0), (1, 2, 3))
        self.assertNotEqual(frame.to_image().getpixel((0, 0)), (1, 2, 3))

    def test_duplicates_do_not_satisfy_unique_next_frame(self):
        reader, response, _, _ = self.start_reader()
        response.chunks.put(part(jpeg()))
        first = reader.next_frame(max_wait=1, max_age=1)
        response.chunks.put(part(jpeg()))
        self.wait_sequence(reader, 2)
        with self.assertRaises(TimeoutError):
            reader.next_frame(max_wait=0.02, max_age=1)
        repeated = reader.next_frame(max_wait=0, max_age=1, require_unique=False)
        self.assertGreater(repeated.sequence, first.sequence)
        self.assertEqual(repeated.sha256, first.sha256)
        response.chunks.put(part(jpeg("green")))
        self.assertNotEqual(reader.next_frame(max_wait=1, max_age=1).sha256, repeated.sha256)

    def test_stale_frame_not_returned_even_when_never_consumed(self):
        reader, response, _, _ = self.start_reader()
        response.chunks.put(part(jpeg()))
        self.wait_sequence(reader, 1)
        with patch("fishing_probe.mjpeg.time.monotonic", return_value=reader._latest.received_at + 2):
            with self.assertRaises(TimeoutError):
                reader.next_frame(max_wait=0, max_age=0.25)

    def test_end_or_timeout_fails_closed_despite_cached_frame(self):
        for ending in (b"", TimeoutError("read timeout")):
            with self.subTest(ending=ending):
                reader, response, _, _ = self.start_reader()
                response.chunks.put(part(jpeg()))
                self.wait_sequence(reader, 1)
                response.chunks.put(ending)
                with reader._condition:
                    self.assertTrue(reader._condition.wait_for(lambda: reader._error is not None, 1))
                with self.assertRaises(MJPEGError):
                    reader.next_frame(max_wait=0, max_age=1)
                reader.close()

    def test_invalid_or_oversized_jpeg_fails_without_publishing(self):
        for payload, kwargs in ((b"not a jpeg", {}), (jpeg()[:-4], {}),
                                (jpeg(size=(40, 20)), {"max_dimension": 30}),
                                (jpeg(size=(40, 20)), {"max_pixels": 700})):
            with self.subTest(kwargs=kwargs, length=len(payload)):
                reader, response, _, _ = self.start_reader(**kwargs)
                response.chunks.put(part(payload))
                with self.assertRaises(MJPEGError):
                    reader.next_frame(max_wait=1, max_age=1)
                self.assertIsNone(reader._latest)
                reader.close()

    def test_exif_orientation_is_applied_before_size_and_hash(self):
        reader = MJPEGReader()
        exif = Image.Exif()
        exif[274] = 6
        frame = reader._decode(jpeg(exif=exif), time.monotonic(), 1)
        self.assertEqual(frame.size, (10, 20))
        self.assertEqual(frame.to_image().size, (10, 20))

    def test_close_interrupts_transferred_socket_and_is_idempotent(self):
        reader, response, connection, _ = self.start_reader()
        self.assertIsNone(connection.sock)
        before = time.monotonic()
        reader.close()
        self.assertLess(time.monotonic() - before, 0.5)
        self.assertFalse(reader._thread.is_alive())
        self.assertTrue(response.closed)
        self.assertTrue(connection.closed)
        reader.close()
        with self.assertRaises(MJPEGError):
            reader.next_frame()
        with self.assertRaises(MJPEGError):
            reader.start()

    def test_redirects_are_rejected_without_following_location(self):
        response = FakeResponse()
        response.status = 302
        connection = FakeConnection(response)
        with patch("fishing_probe.mjpeg.http.client.HTTPConnection", return_value=connection) as constructor:
            with MJPEGReader() as reader:
                with self.assertRaisesRegex(MJPEGError, "redirects are not followed"):
                    reader.next_frame(max_wait=1)
            self.assertEqual(constructor.call_count, 1)

    def test_wait_timeout_and_configuration_limits(self):
        for value in (float("nan"), float("inf"), 0, -1, True, 31):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                MJPEGReader(network_timeout=value)
        for kwargs in ({"max_frame_bytes": 0}, {"max_pixels": True},
                       {"max_dimension": 20000}, {"network_timeout": 4}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MJPEGReader(**kwargs)
        reader, _, _, _ = self.start_reader()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            reader.next_frame(max_wait=0.02)
        self.assertLess(time.monotonic() - started, 0.5)
        for kwargs in ({"max_wait": -1}, {"max_age": 0}, {"require_unique": "yes"}, {"after": 4}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                reader.next_frame(**kwargs)


if __name__ == "__main__":
    unittest.main()
