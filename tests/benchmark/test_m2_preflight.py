"""Preflight: every dataset problem is found, all at once, without touching data."""

import numpy as np
import pytest
import soundfile as sf
from benchmark_fakes import make_dataset, read_manifest, write_manifest

from pronunciation_lab.benchmark.dataset import EXPECTED_IDS, preflight, sha256_file


@pytest.fixture
def data(tmp_path):
    return make_dataset(tmp_path)


def codes(report):
    return sorted(p.code for p in report.problems)


def test_clean_synthetic_dataset_passes(data):
    report = preflight(data)
    assert report.ok, report.problems
    assert [r.recording_id for r in report.recordings] == list(EXPECTED_IDS)
    # Manifest, 20 WAVs and 20 sources are hashed for later integrity checks.
    assert len(report.hashes) == 41
    assert report.recordings[0].text_group == "think_three"


def test_preflight_is_read_only(data):
    before = {p: sha256_file(p) for p in data.rglob("*") if p.is_file()}
    preflight(data)
    assert {p: sha256_file(p) for p in data.rglob("*") if p.is_file()} == before


def test_missing_manifest(tmp_path):
    report = preflight(tmp_path)
    assert codes(report) == ["manifest_missing"]


def test_manifest_missing_columns(data):
    rows = read_manifest(data / "benchmark_manifest.csv")
    write_manifest(data / "benchmark_manifest.csv", rows, ["recording_id", "filename", "target_text"])
    report = preflight(data)
    assert codes(report) == ["manifest_columns_missing"]
    assert "reading_style" in report.problems[0].message


def test_malformed_row_and_missing_target_text_field(data):
    path = data / "benchmark_manifest.csv"
    lines = path.read_text().splitlines()
    lines[3] = "R03,Source R03.m4a"  # too few fields: target text missing entirely
    path.write_text("\n".join(lines) + "\n")
    report = preflight(data)
    assert "manifest_row_malformed" in codes(report)
    assert "recording_missing_from_manifest" in codes(report)


def test_undecodable_manifest(data):
    (data / "benchmark_manifest.csv").write_bytes(b"\xff\xfe\x00bad")
    assert codes(preflight(data)) == ["manifest_unreadable"]


def test_every_problem_is_reported_together(data):
    """Many defects at once: preflight must list all of them, not stop at the first."""
    rows = read_manifest(data / "benchmark_manifest.csv")
    rows[1]["target_text"] = ""                     # R02 empty text
    rows[2]["target_text"] = " " + rows[2]["target_text"]  # R03 whitespace
    rows[5]["recording_id"] = "R05"                 # R06 -> duplicate of R05
    del rows[19]                                    # R20 missing from manifest
    rows[7]["reading_style"] = ""                   # R08 empty style
    write_manifest(data / "benchmark_manifest.csv", rows)

    wav = data / "benchmark_wav"
    (wav / "R09.wav").unlink()                                       # missing
    (wav / "R10.wav").write_bytes(b"")                               # zero bytes
    (wav / "R11.wav").write_bytes(b"RIFF....WAVEfmt garbage")         # unreadable
    sf.write(wav / "R12.wav", np.zeros(8000, np.float32), 8_000)      # sample rate
    sf.write(wav / "R13.wav", np.zeros((16000, 2), np.float32), 16_000, subtype="PCM_16")  # channels
    sf.write(wav / "R14.wav", np.zeros(16000, np.float32), 16_000, subtype="FLOAT")  # subtype
    sf.write(wav / "R15.wav", np.zeros(0, np.float32), 16_000, subtype="PCM_16")     # zero duration
    (data / "Source R16.m4a").unlink()                               # source missing
    sf.write(wav / "R99.wav", np.zeros(160, np.float32), 16_000)      # stray

    report = preflight(data)
    found = {(p.code, p.recording_id) for p in report.problems}
    expected = {
        ("target_text_empty", "R02"),
        ("target_text_whitespace", "R03"),
        ("recording_id_duplicate", "R05"),
        ("recording_missing_from_manifest", "R06"),
        ("recording_missing_from_manifest", "R20"),
        ("reading_style_empty", "R08"),
        ("wav_missing", "R09"),
        ("wav_empty_file", "R10"),
        ("wav_unreadable", "R11"),
        ("wav_sample_rate", "R12"),
        ("wav_channels", "R13"),
        ("wav_subtype", "R14"),
        ("wav_zero_duration", "R15"),
        ("source_missing", "R16"),
    }
    assert expected <= found, expected - found
    assert ("text_group_inconsistent", None) in found  # R02/R03 texts now differ
    assert any(w.code == "wav_unexpected" for w in report.warnings)
    assert not report.ok


def test_texts_must_differ_between_groups(data):
    rows = read_manifest(data / "benchmark_manifest.csv")
    for row in rows:
        if row["recording_id"] in ("R05", "R06", "R07"):
            row["target_text"] = rows[0]["target_text"]
    write_manifest(data / "benchmark_manifest.csv", rows)
    assert "text_groups_overlap" in codes(preflight(data))


def test_unexpected_recording_id(data):
    rows = read_manifest(data / "benchmark_manifest.csv")
    rows.append(dict(rows[0], recording_id="R21"))
    write_manifest(data / "benchmark_manifest.csv", rows)
    assert ("recording_unexpected", "R21") in {(p.code, p.recording_id) for p in preflight(data).problems}


def test_real_dataset_preflight(r01_row):
    """The real benchmark data (skipped if absent)."""
    from conftest import PROJECT_ROOT

    report = preflight(PROJECT_ROOT / "data")
    assert report.ok, [p.as_dict() for p in report.problems]
    assert len(report.recordings) == 20
    for rid, info in report.audio.items():
        assert (info["sample_rate_hz"], info["channels"], info["subtype"]) == (16_000, 1, "PCM_16")
        # Prepared WAVs are reproducible from the sources: same length within AAC padding.
        if "source_duration_s" in info:
            assert abs(info["source_duration_s"] - info["duration_s"]) < 0.1
