"""Shared playback/export gain; keep ordinary volume behavior unchanged."""
import numpy as np


def apply_volume(wave, volume):
    output = np.asarray(wave, dtype=np.float32) * volume
    if volume <= 1:
        return output
    # Boost quiet samples linearly. Round peaks smoothly instead of hard-clipping
    # the boosted waveform or normalizing every chunk to a different loudness.
    magnitude = np.abs(output)
    peaks = magnitude > .9
    if np.any(peaks):
        output[peaks] = np.sign(output[peaks]) * (
            .9 + .1 * np.tanh((magnitude[peaks] - .9) / .1))
    return output
