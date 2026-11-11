"""Check streaming of growing capture files and flushing unequal channel tails."""
from array import array
import base64
from pathlib import Path
import tempfile
import threading
import unittest

from live_audio import LiveAudio


class Bridge:
    def __init__(self, fail=False):
        self.pcm = bytearray()
        self.received = threading.Event()
        self.fail = fail

    def request(self, action, **args):
        if self.fail:
            raise RuntimeError('recognizer stopped')
        self.pcm.extend(base64.b64decode(args['pcm']))
        self.received.set()


class LiveAudioTest(unittest.TestCase):
    def test_growing_files_feed_before_stop_and_keep_all_tail_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = array('h', range(5000)).tobytes()
            right = array('h', range(-4700, 0)).tobytes()
            microphone = root / 'microphone.raw'
            system = root / 'system.raw'
            microphone.write_bytes(left[:5120])
            system.write_bytes(right[:5120])
            bridge = Bridge()
            pump = LiveAudio(bridge, root, 'test', [])
            pump.start()
            self.assertTrue(bridge.received.wait(2), 'audio was buffered until stop')
            with microphone.open('ab') as file:
                file.write(left[5120:])
            with system.open('ab') as file:
                file.write(right[5120:])
            pump.stopping.set()
            pump.finished.set()
            pump.finish(2)
            result = array('h', bridge.pcm)
            self.assertEqual(result[0::2].tobytes(), left)
            self.assertEqual(result[1::2].tobytes(), right + bytes(len(left)-len(right)))

    def test_failure_reaches_caller_without_inserting_partial_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['microphone.raw', 'system.raw']:
                (root/name).write_bytes(bytes(5120))
            pump = LiveAudio(Bridge(fail=True), root, 'test', [])
            pump.start()
            with self.assertRaisesRegex(RuntimeError, 'recognizer stopped'):
                pump.finish(2)
            self.assertFalse(pump.thread.is_alive())

    def test_cancel_while_waiting_for_a_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'microphone.raw').write_bytes(bytes(5120))
            (root/'system.raw').write_bytes(b'')
            bridge = Bridge()
            pump = LiveAudio(bridge, root, 'test', [])
            pump.start()
            pump.cancel()
            self.assertFalse(pump.thread.is_alive())
            self.assertFalse(bridge.pcm)


if __name__ == '__main__':
    unittest.main()
