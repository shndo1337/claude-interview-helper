"""Feed both growing PipeWire PCM files to the recognizer while recording."""
from array import array
import base64
import threading


class LiveAudio:
    BLOCK_BYTES = 5120  # 160 ms of 16 kHz mono S16LE per channel.

    def __init__(self, bridge, directory, recording, processes):
        self.bridge = bridge
        self.directory = directory
        self.recording = recording
        self.processes = processes
        self.finished = threading.Event()
        self.stopping = threading.Event()
        self.cancelled = threading.Event()
        self.error = None
        self.bytes_sent = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            with (self.directory / 'microphone.raw').open('rb') as microphone, (self.directory / 'system.raw').open('rb') as system:
                buffers = [bytearray(), bytearray()]
                files = [microphone, system]
                while not self.cancelled.is_set():
                    for i, file in enumerate(files):
                        buffers[i].extend(file.read(self.BLOCK_BYTES - len(buffers[i])))
                    if all(len(b) == self.BLOCK_BYTES for b in buffers):
                        count = self.BLOCK_BYTES
                    elif self.finished.is_set():
                        count = max(map(len, buffers))
                        if not count:
                            break
                        if count % 2:
                            raise RuntimeError('Неполный аудиосэмпл PipeWire')
                    else:
                        if not self.stopping.is_set() and any(p.poll() is not None for p in self.processes):
                            raise RuntimeError('Захват звука прервался')
                        self.cancelled.wait(.02)
                        continue
                    channels = []
                    for buffer in buffers:
                        chunk = bytes(buffer[:count])
                        del buffer[:count]
                        channels.append(array('h', chunk + bytes(count - len(chunk))))
                    stereo = array('h', [0]) * (count // 2 * 2)
                    stereo[0::2], stereo[1::2] = channels
                    self.bridge.request('stream-audio', recording=self.recording,
                                        pcm=base64.b64encode(stereo.tobytes()).decode('ascii'), timeout=20)
                    self.bytes_sent += count * 2
        except Exception as error:
            self.error = error

    def finish(self, timeout=60):
        self.thread.join(timeout)
        if self.thread.is_alive():
            self.cancelled.set()
            self.thread.join(21)
            raise RuntimeError('Распознавание не успевает за записью')
        if self.error:
            raise RuntimeError(str(self.error))

    def cancel(self):
        self.cancelled.set()
        self.thread.join(21)
