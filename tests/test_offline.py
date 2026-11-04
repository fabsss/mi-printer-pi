"""Offline-Tests (ohne Drucker/Bluetooth): python3 -m unittest discover -s tests -v"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mizink import spool as sp                                    # noqa: E402
from mizink.crypto import Cipher, rc4_keystream                   # noqa: E402
from mizink.printer import Printer                                # noqa: E402
from mizink.protocol import (build_frame, parse_frames, CH_JSON, CH_FILE,  # noqa: E402
                             RESPONSE, ENC_JSON, ENCTYPE_MIJIA)
from mizink.worker import Worker                                  # noqa: E402

TOKEN = "00112233445566778899aabb"


class FakeConn:
    """Simuliert die Drucker-Seite des Protokolls (Basil, RC4)."""

    def __init__(self, cipher):
        self.cipher, self.rx, self.file, self.methods = cipher, bytearray(), bytearray(), []
        self.is_open = True
        self.jpegs = []

    def _reply(self, mid, result):
        body = self.cipher.crypt(json.dumps({"id": mid, "result": result}).encode())
        self.rx += build_frame(CH_JSON, RESPONSE, ENC_JSON, mid, body, ENCTYPE_MIJIA)

    def write(self, frame):
        frames, _ = parse_frames(frame)
        for f in frames:
            assert f.checksum_ok
            body = self.cipher.crypt(f.body)
            if f.channel == CH_FILE:
                self.file += body[4:]
                continue
            j = json.loads(body)
            m = j["method"]
            self.methods.append(m)
            if m == "mixed_status":
                self._reply(j["id"], {"category": "idle", "sub_category": "idle", "battery": 80, "error": 0})
            elif m == "print_job":
                self.file.clear()
                self._reply(j["id"], {"job_id": 7})
            elif m == "confirm_job":
                self.jpegs.append(bytes(self.file))
                self._reply(j["id"], ["OK"])
            elif m == "job_info":
                self._reply(j["id"], [{"job_state": "finished", "prt_copies": 1}])
            elif m == "retime":
                self._reply(j["id"], ["OK"])
            # clean_data: keine Antwort (wie am echten Drucker)

    def read_available(self):
        d = bytes(self.rx)
        self.rx.clear()
        return d

    def pump(self, s):
        pass


def make_printer():
    cipher = Cipher.from_token(TOKEN)
    p = Printer("AA:BB:CC:DD:EE:FF", cipher)
    p.conn = FakeConn(cipher)
    return p


class CryptoProtocol(unittest.TestCase):
    def test_rc4_known_vector(self):  # Wikipedia: Key="Key", Plaintext="Plaintext"
        ks = rc4_keystream(b"Key", 9)
        self.assertEqual(bytes(a ^ b for a, b in zip(ks, b"Plaintext")).hex().upper(), "BBF316E8D940AF0AD3")

    def test_frame_roundtrip(self):
        fr = build_frame(CH_JSON, 6, ENC_JSON, 5, b"hello", ENCTYPE_MIJIA)
        frames, rest = parse_frames(fr + b"\x00")
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].checksum_ok)
        self.assertEqual((frames[0].body, frames[0].enctype, frames[0].msg_sn), (b"hello", 4, 5))
        self.assertEqual(rest, b"\x00")


class EndToEnd(unittest.TestCase):
    def test_print_image_sends_valid_jpeg(self):
        from PIL import Image
        p = make_printer()
        p.print_image(Image.new("RGB", (3000, 2000), "red"))
        self.assertIn("confirm_job", p.conn.methods)
        im = Image.open(io.BytesIO(p.conn.jpegs[0]))
        self.assertEqual(im.size, (1040, 1560))

    def test_queue_and_worker(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            os.environ["MIZINK_STATUS"] = str(d / "status.json")
            src = d / "foto.jpg"
            Image.new("RGB", (1800, 1200), "blue").save(src)
            spool = d / "spool"
            spool.mkdir()
            sp.enqueue(src, copies=2, spool=spool)
            self.assertEqual(len(sp.pending(spool)), 1)
            w = Worker(spool, {}, None)
            self.assertEqual(w.process_queue(make_printer()), 1)
            self.assertEqual(sp.pending(spool), [])
            self.assertEqual(list(spool.glob("*.img")), [])

    def test_queue_limit_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.jpg").write_bytes(b"x")
            for _ in range(2):
                sp.enqueue(d / "a.jpg", spool=d, max_queue=2)
            with self.assertRaises(RuntimeError):
                sp.enqueue(d / "a.jpg", spool=d, max_queue=2)
            with self.assertRaises(FileNotFoundError):
                sp.enqueue(d / "nope.jpg", spool=d)

    def test_failed_job_moves_to_failed(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            os.environ["MIZINK_STATUS"] = str(d / "status.json")
            Image.new("RGB", (100, 150)).save(d / "f.jpg")
            spool = d / "s"
            spool.mkdir()
            sp.enqueue(d / "f.jpg", spool=spool)
            w = Worker(spool, {"max_attempts": 2}, None)

            class Boom:
                def print_image(self, *a, **k):
                    raise RuntimeError("boom")
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    w.process_queue(Boom())
            self.assertEqual(sp.pending(spool), [])
            self.assertEqual(len(list((spool / "failed").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
