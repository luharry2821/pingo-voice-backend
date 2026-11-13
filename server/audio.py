"""Audio helpers: turn a WAV file into 16 kHz mono PCM16, the format streaming STT expects.

Used by the eval scripts to stream recorded clips like a live mic. Live
browsers already send 16 kHz PCM16, so they skip this file. Pure standard library, no numpy.

PCM16 = signed 16-bit samples, little-endian, 2 bytes per sample (mono).
"""

import array
import sys
import wave

# Streaming STT is configured for 16 kHz audio (see stt.sample_rate in
# server/config.py).
TARGET_RATE = 16000


def read_pcm16_mono(path, target_rate=TARGET_RATE):
    """Read a PCM WAV and return little-endian 16-bit mono samples at target_rate."""
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        sampwidth = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    # Only 16-bit WAVs are supported; 8/24/32-bit files would need conversion first.
    if sampwidth != 2:
        raise ValueError(
            f"{path} is {sampwidth * 8}-bit PCM. Export 16-bit PCM WAV."
        )
    return resample_pcm16(_downmix(frames, channels), rate, target_rate)


def _downmix(pcm, channels):
    """Average interleaved channels into mono PCM16 (no-op for mono input)."""
    if channels == 1:
        return pcm
    samples = _from_le(pcm)
    mixed = array.array("h")
    # Interleaved frames: [L, R, L, R, ...]; average each group of `channels` samples.
    for index in range(0, len(samples), channels):
        group = samples[index : index + channels]
        mixed.append(int(round(sum(group) / float(len(group)))))
    return _to_le(mixed)


def resample_pcm16(pcm, src_rate, dst_rate):
    """Linear resample. Returned unchanged when the rates already match."""
    if src_rate == dst_rate:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rate must be positive")
    src = _from_le(pcm)
    if len(src) == 0:
        return b""
    # Number of output samples for the same duration at the new rate.
    n_dst = max(1, int(round(len(src) * float(dst_rate) / float(src_rate))))
    if n_dst == 1 or len(src) == 1:
        return _to_le(array.array("h", [src[0]]))
    dst = array.array("h", [0]) * n_dst
    # Map each output sample to a fractional input position and interpolate between the
    # two nearest input samples; clamp to the 16-bit range.
    scale = (len(src) - 1) / float(n_dst - 1)
    for index in range(n_dst):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(src) - 1)
        frac = position - left
        value = src[left] + (src[right] - src[left]) * frac
        dst[index] = max(-32768, min(32767, int(round(value))))
    return _to_le(dst)


def _from_le(pcm):
    """Little-endian PCM16 bytes -> array of ints (an odd trailing byte is dropped)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _to_le(samples):
    """Array of 16-bit ints -> little-endian PCM16 bytes (byteswaps on big-endian machines)."""
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()
