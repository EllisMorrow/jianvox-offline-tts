"""Stateful WSOLA. Preserve pitch and phase continuity across model chunks."""
import numpy as np
from audiotsm import wsola
from audiotsm.io.array import ArrayReader, ArrayWriter


class Tempo:
    def __init__(self, speed):
        self.processor = None if speed == 1 else wsola(1, speed=speed, frame_length=1024)

    def process(self, wave, final=False):
        if self.processor is None:
            return wave
        writer = ArrayWriter(1)
        self.processor.run(ArrayReader(np.asarray(wave, dtype=np.float32).reshape(1, -1)), writer, flush=final)
        return writer.data.reshape(-1).astype(np.float32, copy=False)
