"""Turn a WAV file into 16 kHz mono PCM16, the format streaming STT expects."""

import array
import sys
import wave

TARGET_RATE = 16000


def read_pcm16_mono(path, target_rate=TARGET_RATE):
    """Read a PCM WAV and return little-endian 16-bit mono samples at target_rate."""
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        sampwidth = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sampwidth != 2:
        raise ValueError(
            f"{path} is {sampwidth * 8}-bit PCM. Export 16-bit PCM WAV."
        )
    return resample_pcm16(_downmix(frames, channels), rate, target_rate)


def _downmix(pcm, channels):
    if channels == 1:
        return pcm
    samples = _from_le(pcm)
    mixed = array.array("h")
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
    n_dst = max(1, int(round(len(src) * float(dst_rate) / float(src_rate))))
    if n_dst == 1 or len(src) == 1:
        return _to_le(array.array("h", [src[0]]))
    dst = array.array("h", [0]) * n_dst
    scale = (len(src) - 1) / float(n_dst - 1)
    for index in range(n_dst):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(src) - 1)
        frac = position - left
        value = src[left] + (src[right] - src[left]) * frac
        dst[index] = max(-32768, min(32767, int(round(value))))
    return _to_le(dst)


def iter_chunks(pcm, sample_rate=TARGET_RATE, chunk_ms=50):
    """Yield ~chunk_ms slices. 50 ms at 16 kHz mono s16 is 1600 bytes."""
    size = sample_rate * chunk_ms // 1000 * 2
    if size <= 0:
        raise ValueError("chunk_ms is too small")
    for start in range(0, len(pcm), size):
        yield pcm[start : start + size]


def silence(ms, sample_rate=TARGET_RATE):
    return b"\x00\x00" * (sample_rate * ms // 1000)


def _from_le(pcm):
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _to_le(samples):
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()
