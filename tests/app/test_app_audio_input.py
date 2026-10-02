"""Turning uploads into the analysis WAV: formats, conversions, rejections."""

import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from apphelpers import wav_bytes

from pronunciation_lab.app import audio_input as AI

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def prepare(data, name, tmp_path):
    return AI.prepare_audio(data, name, tmp_path / "work")


def test_analysis_format_is_copied_byte_for_byte(tmp_path):
    data = wav_bytes(1.0, tmp=tmp_path)
    p = prepare(data, "R01.wav", tmp_path)
    assert p.conversion == "copied"
    assert p.analysis_path.read_bytes() == data
    assert p.original_path.read_bytes() == data
    assert p.duration_ms == pytest.approx(1000.0)


@pytest.mark.parametrize("sr, channels, subtype", [(44_100, 2, "PCM_16"), (48_000, 1, "PCM_24"), (16_000, 1, "FLOAT"), (8_000, 1, "PCM_16"), (16_000, 2, "PCM_16")])
def test_other_wav_formats_are_resampled_to_analysis_format(tmp_path, sr, channels, subtype):
    p = prepare(wav_bytes(1.0, sr=sr, channels=channels, subtype=subtype, tmp=tmp_path), "x.wav", tmp_path)
    info = sf.info(str(p.analysis_path))
    assert p.conversion == "resampled"
    assert (info.samplerate, info.channels, info.subtype) == (16_000, 1, "PCM_16")
    assert p.duration_ms == pytest.approx(1000.0, abs=1.0)
    assert p.source["sample_rate_hz"] == sr and p.source["channels"] == channels


def test_flac_is_read_without_ffmpeg(tmp_path, monkeypatch):
    path = tmp_path / "a.flac"
    sf.write(path, np.random.default_rng(1).uniform(-0.2, 0.2, 16_000).astype(np.float32), 16_000)
    monkeypatch.setattr(AI.shutil, "which", lambda name: None)
    p = prepare(path.read_bytes(), "a.flac", tmp_path)
    assert p.conversion == "resampled" and sf.info(str(p.analysis_path)).format == "WAV"


@needs_ffmpeg
@pytest.mark.parametrize("codec_args, ext", [(["-c:a", "aac"], ".m4a"), (["-c:a", "libopus"], ".webm"), (["-c:a", "libmp3lame"], ".mp3")])
def test_compressed_formats_go_through_ffmpeg(tmp_path, codec_args, ext):
    src = tmp_path / "src.wav"
    sf.write(src, np.sin(np.linspace(0, 2000, 32_000)).astype(np.float32) * 0.3, 16_000)
    out = tmp_path / f"enc{ext}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *codec_args, str(out)], check=True)
    p = prepare(out.read_bytes(), out.name, tmp_path)
    info = sf.info(str(p.analysis_path))
    # libsndfile >= 1.1 decodes MP3 itself; AAC and Opus-in-WebM need ffmpeg.
    assert p.conversion == ("ffmpeg" if ext != ".mp3" else p.conversion)
    assert p.conversion in ("ffmpeg", "resampled")
    assert (info.samplerate, info.channels, info.subtype) == (16_000, 1, "PCM_16")
    assert p.duration_ms == pytest.approx(2000.0, abs=80.0)
    assert p.original_path.suffix == ext


def test_compressed_audio_without_ffmpeg_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(AI.shutil, "which", lambda name: None)
    with pytest.raises(AI.AudioInputError) as e:
        prepare(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 200, "memo.m4a", tmp_path)
    assert e.value.code == "audio_unsupported" and "ffmpeg" in e.value.message


@pytest.mark.parametrize("data, code", [
    (b"", "audio_empty"),
    (b"this is not audio at all" * 10, "audio_unreadable"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio_unreadable"),
])
def test_bad_uploads(tmp_path, data, code):
    with pytest.raises(AI.AudioInputError) as e:
        prepare(data, "x.wav", tmp_path)
    assert e.value.code == code


def test_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(AI, "MAX_UPLOAD_BYTES", 1000)
    with pytest.raises(AI.AudioInputError) as e:
        prepare(b"\x00" * 1001, "x.wav", tmp_path)
    assert e.value.code == "audio_too_large"


@pytest.mark.parametrize("seconds, code", [(0.0, "audio_too_short"), (0.1, "audio_too_short"), (0.29, "audio_too_short")])
def test_too_short(tmp_path, seconds, code):
    with pytest.raises(AI.AudioInputError) as e:
        prepare(wav_bytes(seconds, tmp=tmp_path), "x.wav", tmp_path)
    assert e.value.code == code


def test_minimum_length_is_accepted(tmp_path):
    assert prepare(wav_bytes(0.3, tmp=tmp_path), "x.wav", tmp_path).duration_ms == pytest.approx(300.0)


def test_too_long(tmp_path, monkeypatch):
    monkeypatch.setattr(AI, "MAX_DURATION_MS", 900.0)
    with pytest.raises(AI.AudioInputError) as e:
        prepare(wav_bytes(1.0, tmp=tmp_path), "x.wav", tmp_path)
    assert e.value.code == "audio_too_long"


@pytest.mark.parametrize("name, suffix", [("../../etc/passwd", ".bin"), ("a.WAV", ".wav"), ("noext", ".bin"), ("x.toolongext", ".bin"), ("", ".bin")])
def test_original_file_name_cannot_escape_the_workdir(tmp_path, name, suffix):
    p = prepare(wav_bytes(0.5, tmp=tmp_path), name, tmp_path)
    assert p.original_path.parent == tmp_path / "work"
    assert p.original_path.suffix == suffix


def test_original_hash_identifies_the_upload(tmp_path):
    import hashlib

    data = wav_bytes(0.5, tmp=tmp_path)
    assert prepare(data, "a.wav", tmp_path).original_sha256 == hashlib.sha256(data).hexdigest()


def test_silence_detection(tmp_path):
    silent = prepare(wav_bytes(0.5, silent=True, tmp=tmp_path), "s.wav", tmp_path)
    assert AI.is_silent(silent.analysis_path)
    loud = AI.prepare_audio(wav_bytes(0.5, tmp=tmp_path), "n.wav", tmp_path / "w2")
    assert not AI.is_silent(loud.analysis_path)


def test_ffmpeg_failure_with_partial_output_is_rejected(tmp_path, monkeypatch):
    """ffmpeg can fail part-way (e.g. a truncated .m4a) yet leave a partial WAV behind.

    Its exit status must decide, not the presence of an output file.
    """
    def failing_ffmpeg(cmd, **kwargs):
        sf.write(cmd[-1], np.zeros(16_000, np.float32), 16_000, subtype="PCM_16")  # partial output
        return subprocess.CompletedProcess(cmd, 1, b"", b"Invalid data found when processing input")

    monkeypatch.setattr(AI.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(AI.subprocess, "run", failing_ffmpeg)
    with pytest.raises(AI.AudioInputError) as e:
        prepare(b"\x00\x00\x00\x20ftypM4A " + b"\x01" * 500, "cut.m4a", tmp_path)
    assert e.value.code == "audio_unreadable"
    assert not (tmp_path / "work" / "analysis.wav").exists()  # the partial file is removed


@needs_ffmpeg
def test_truncated_m4a_is_rejected_or_converted_consistently(tmp_path):
    """A real truncated AAC file: either a clean error or a valid analysis WAV, never a crash."""
    src = tmp_path / "src.wav"
    sf.write(src, np.sin(np.linspace(0, 2000, 32_000)).astype(np.float32) * 0.3, 16_000)
    m4a = tmp_path / "full.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "aac", str(m4a)], check=True)
    data = m4a.read_bytes()[: len(m4a.read_bytes()) // 3]
    try:
        p = prepare(data, "cut.m4a", tmp_path)
    except AI.AudioInputError as e:
        assert e.code in ("audio_unreadable", "audio_too_short")
    else:
        info = sf.info(str(p.analysis_path))
        assert (info.samplerate, info.channels, info.subtype) == (16_000, 1, "PCM_16")
