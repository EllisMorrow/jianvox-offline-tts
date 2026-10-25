"""Shared playback/export gain; keep ordinary volume behavior unchanged."""
import numpy as np


def volume_gain(volume):
    """Keep normal volume linear; give the boost range useful loudness headroom."""
    if volume <= 1:
        return volume
    # The UI is a volume-control percentage, not an amplitude multiplier.
    # 100–150% maps to 0–9 dB, rather than a barely 3.52 dB maximum.
    return 10 ** ((volume - 1) * 18 / 20)


def apply_volume(wave, volume):
    output = np.asarray(wave, dtype=np.float32) * volume_gain(volume)
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
