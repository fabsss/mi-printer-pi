"""Offline tests (no printer/Bluetooth needed): python3 -m unittest discover -s tests -v"""
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
from mizink.lpstat_shim import render                             # noqa: E402

TOKEN = "00112233445566778899aabb"


class FakeConn:
    """Simulates the printer side of the protocol (Basil, RC4)."""

    def __init__(self, cipher):
        self.cipher, self.rx, self.file, self.methods = cipher, bytearray(), bytearray(), []
        self.is_open = True
        self.jpegs = []
        self.status = {"category": "idle", "sub_category": "idle", "battery": 80, "error": 0}

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
                self._reply(j["id"], self.status)
            elif m == "print_job":
                self.file.clear()
                self._reply(j["id"], {"job_id": 7})
            elif m == "confirm_job":
                self.jpegs.append(bytes(self.file))
                self._reply(j["id"], ["OK"])
            elif m == "job_info":
                self._reply(j["id"], [{"job_state": getattr(self, "job_state", "finished"), "prt_copies": 1}])
            elif m == "retime":
                self._reply(j["id"], ["OK"])
            # clean_data: no reply (like the real printer)

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
                def status(self):
                    return {"category": "idle", "error": 0}

                def print_image(self, *a, **k):
                    raise RuntimeError("boom")
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    w.process_queue(Boom())
            self.assertEqual(sp.pending(spool), [])
            self.assertEqual(len(list((spool / "failed").glob("*.json"))), 1)


class ErrorHandling(unittest.TestCase):
    def _setup(self, d, n=3):
        from PIL import Image
        os.environ["MIZINK_STATUS"] = str(d / "status.json")
        Image.new("RGB", (100, 150)).save(d / "f.jpg")
        spool = d / "s"
        spool.mkdir()
        for _ in range(n):
            sp.enqueue(d / "f.jpg", spool=spool)
        return spool

    def test_printer_error_moves_queue_to_failed_without_printing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            spool = self._setup(d)
            p = make_printer()
            p.conn.status = {"category": "error", "sub_category": "x", "battery": 50, "error": -7101}
            w = Worker(spool, {}, None)
            self.assertEqual(w.process_queue(p), 0)
            self.assertNotIn("print_job", p.conn.methods)           # nothing was sent to the printer
            self.assertEqual(sp.pending(spool), [])                  # queue is empty, no pile-up
            self.assertEqual(len(list((spool / "failed").glob("*.json"))), 3)
            self.assertIn("-7101", w.problem)

    def test_error_after_print_aborts_rest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            spool = self._setup(d)
            p = make_printer()
            orig = p.conn.write

            def write(frame):
                orig(frame)
                if "confirm_job" in p.conn.methods:  # printer fails after the first job
                    p.conn.status = {"category": "error", "battery": 50, "error": -7101}
            p.conn.write = write
            w = Worker(spool, {}, None)
            self.assertEqual(w.process_queue(p), 0)                  # error seen while waiting
            self.assertEqual(p.conn.methods.count("print_job"), 1)   # exactly one print attempt
            self.assertEqual(len(list((spool / "failed").glob("*.json"))), 2)  # sent job held by printer

    def test_out_of_paper_detected_during_print_keeps_photo(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            spool = self._setup(d, n=2)
            p = make_printer()
            orig = p.conn.write

            def write(frame):
                orig(frame)
                if "confirm_job" in p.conn.methods:  # paper runs out right after the job is accepted
                    p.conn.status = {"category": "error", "battery": 50, "error": -7101}
                    # the printer never reports "finished" for this job
                    p.conn.job_state = "failed"
            p.conn.write = write
            w = Worker(spool, {}, None)
            self.assertEqual(w.process_queue(p), 0)
            self.assertIn("Out of paper", w.problem)
            self.assertEqual(sp.pending(spool), [])
            # the job already sent is held by the printer (not kept, would print twice);
            # the one that was still waiting is kept in failed/
            self.assertEqual(len(list((spool / "failed").glob("*.json"))), 1)
            self.assertEqual(sp.requeue_failed(spool), 1)
            self.assertEqual(len(sp.pending(spool)), 1)

    def test_no_retry_after_image_was_sent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            spool = self._setup(d, n=1)
            p = make_printer()

            def boom(*a, **k):
                raise ConnectionError("link lost")
            p.job_info = boom
            w = Worker(spool, {}, None)
            with self.assertRaises(ConnectionError):
                w.process_queue(p)
            self.assertEqual(sp.pending(spool), [])                  # not queued for a second print

    def test_enqueue_refused_on_problem_and_shim_output(self):
        now = 1000.0
        ok = {"state": "connected", "updated": now, "problem": None}
        bad = {"state": "connected", "updated": now, "problem": "Printer error -7101: x"}
        self.assertIsNone(sp.refuse_reason(ok, now))
        self.assertIn("-7101", sp.refuse_reason(bad, now))
        self.assertIn("not running", sp.refuse_reason({}, now))
        self.assertIn("not running", sp.refuse_reason({"state": "connected", "updated": now - 60}, now))
        # the photobooth-app classifies lpstat output by these keywords (idle > disabled > printing)
        self.assertIn("idle", render("p", ok, now))
        self.assertIn("disabled", render("p", bad, now))
        self.assertNotIn("idle", render("p", bad, now))
        self.assertIn("disabled", render("p", {}, now))
        self.assertIn("printing", render("p", {"state": "printing", "updated": now - 100}, now))


if __name__ == "__main__":
    unittest.main()
