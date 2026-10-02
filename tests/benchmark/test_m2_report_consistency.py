"""The M2 report and handoff must agree with the stored benchmark run.

Each check recomputes a claim from the cells / raw arrays / summaries and
asserts that the exact figure appears in the document. Skipped when the run
(personal data, not in Git) is absent.
"""

import json
import re

import numpy as np
import pytest
from conftest import PROJECT_ROOT

from pronunciation_lab.benchmark.cells import CellStore
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.engines import ENGINES
from pronunciation_lab.benchmark.validate import validate_run

RUN_DIR = PROJECT_ROOT / "data" / "benchmark_results" / "runs" / "m2-20261002"
REPORT = (PROJECT_ROOT / "docs" / "M2_BENCHMARK_REPORT.md").read_text()
HANDOFF = (PROJECT_ROOT / "docs" / "M2_TO_M3_HANDOFF.md").read_text()

pytestmark = pytest.mark.skipif(not RUN_DIR.is_dir(), reason="benchmark run not present")
IDS = [f"R{i:02d}" for i in range(1, 21)]


@pytest.fixture(scope="module")
def store():
    return CellStore(RUN_DIR)


@pytest.fixture(scope="module")
def summaries():
    return {p.stem: json.loads(p.read_text()) for p in (RUN_DIR / "summaries").glob("*.json")}


def compact(text):
    return re.sub(r"\s+", " ", text)


def test_execution_counts(summaries):
    recordings = preflight(PROJECT_ROOT / "data").recordings
    counts = validate_run(RUN_DIR, recordings, list(ENGINES), list(ENGINES))["counts"]
    for label in ("ok", "blocked", "unresolved"):
        assert f"| **{label}** | **{counts[label]}** |" in REPORT
    for label in ("partial", "failed"):
        assert f"| {label} | {counts[label]} |" in REPORT
    assert f"| missing / corrupt | {counts['missing']} / {counts['corrupt']} |" in REPORT


def test_identity_and_uncertainty_tables(summaries):
    e = summaries["phenomena"]["engines"]
    op, raw = e["openpronounce"], e["wav2vec2_raw"]
    for key, label in (("substitution", "substitution"), ("omission", "not decoded"), ("insertion", "insertion"),
                       ("excluded_alignment_suspect", "excluded (alignment-suspect)"), ("match", "decoded as expected")):
        assert f"| {label} | {op['phoneme_identity'][key]} | {raw['phoneme_identity'][key]} |" in REPORT
    for eng in (op, raw):
        u, i = eng["uncertainty"], eng["phoneme_identity"]
        assert f"{u['substituted_with_expected_plausible']} / {i['substitution']}" in REPORT
        assert f"{u['ambiguous_decodes']} / {u['decoded_phonemes']}" in REPORT
        assert f"{u['omitted_with_expected_plausible']} / {i['omission']}" in REPORT


def test_style_label_table(summaries):
    from collections import Counter

    for eid in ("openpronounce", "wav2vec2_raw"):
        totals = Counter()
        for g in summaries["style_comparison"]["groups"].values():
            totals.update(g["engines"][eid]["label_counts"])
        summaries.setdefault("_labels", {})[eid] = totals
    op, raw = summaries["_labels"]["openpronounce"], summaries["_labels"]["wav2vec2_raw"]
    for label in set(op) | set(raw):
        assert f"| {label} | {op[label]} | {raw[label]} |" in REPORT


def test_shared_model_claim(store):
    """'Bit-identical posteriors in all 20 recordings' — checked, not assumed."""
    for rid in IDS:
        a = store.load_raw(store.load(f"{rid}__openpronounce").cell)
        b = store.load_raw(store.load(f"{rid}__wav2vec2_raw").cell)
        assert np.array_equal(a["log_posteriors"], b["log_posteriors"])
        assert np.array_equal(a["vocab"], b["vocab"])
    assert "bit-identical" in REPORT and "one acoustic model" in HANDOFF


def _peak(raw, phone, fs, fe):
    ids = [i for i, t in enumerate(raw["vocab"]) if t == phone]
    return float(np.exp(raw["log_posteriors"][fs:fe][:, ids].max()))


def test_r04_versus_ordinary_readings(store):
    pt = {}
    for rid in ("R01", "R02", "R03", "R04"):
        cell = store.load(f"{rid}__wav2vec2_raw").cell
        raw = store.load_raw(cell)
        for w in cell.result.words:
            for p in w.phonemes:
                if p.expected.phoneme == "θ":
                    pt.setdefault(rid, []).append(_peak(raw, "t", p.timing.frame_start, p.timing.frame_end))
                    if rid == "R04":
                        assert p.observed.top == "θ"
    r04 = f"{min(pt['R04']):.2f}–{max(pt['R04']):.2f}"
    ordinary = pt["R01"] + pt["R02"]
    assert f"R04's P(t) values ({r04})" in compact(REPORT)
    assert f"R01–R02 ({min(ordinary):.2f}–{max(ordinary):.2f})" in compact(REPORT)
    assert max(pt["R04"]) <= max(ordinary)  # the claim "within the ordinary range"
    assert f"{max(pt['R03']):.2f}" == "0.70"


def test_w_to_v_count(store):
    decoded_v, total = [], 0
    for rid in IDS:
        cell = store.load(f"{rid}__wav2vec2_raw").cell
        for w in cell.result.words:
            for p in w.phonemes:
                if p.expected.phoneme == "w":
                    total += 1
                    if p.observed.top == "v":
                        decoded_v.append((rid, w.word))
    assert decoded_v == [("R05", "would"), ("R07", "would"), ("R11", "world")]
    assert f"Of the **{total} /w/ targets** in the benchmark, **{len(decoded_v)} were decoded [v]**" in REPORT
    assert f"{len(decoded_v)} of {total} /w/ targets" in HANDOFF


def test_cross_engine_totals(summaries):
    ce = summaries["cross_engine"]["recordings"].values()
    shared = sum(r["spans_shared"] for r in ce)
    only_op = sum(len(r["spans_only_in"]["openpronounce"]) for r in ce)
    only_raw = sum(len(r["spans_only_in"]["wav2vec2_raw"]) for r in ce)
    words = sum(len(r["words_disagreeing"]) for r in ce)
    assert (f"{shared} shared spans, {only_op} only in OpenPronounce, {only_raw} only in raw; "
            f"{words} word occurrences") in compact(HANDOFF)


def test_performance_figures(summaries):
    perf = summaries["performance"]["engines"]
    for eid in ("openpronounce", "wav2vec2_raw"):
        e = perf[eid]
        assert f"{e['model_load_ms']['median']:.0f} ms" in REPORT
        assert f"{e['warm']['inference_ms']['median']:.0f} ms" in REPORT
        assert f"{e['warm']['realtime_factor']['median']:.3f}" in REPORT
    assert "not comparable" in REPORT  # model-load order caveat


def test_no_unresolved_placeholders():
    """No template token (e.g. TESTS_PASSED, MUTATION_SUMMARY) may survive into a document."""
    for doc in (REPORT, HANDOFF):
        prose = re.sub(r"`[^`]*`", "", doc)  # code spans legitimately name env vars
        assert re.findall(r"\b[A-Z]{3,}(?:_[A-Z]+)+\b", prose) == []


def _deviations(store, rid):
    """Substituted + not decoded + inserted, raw engine, alignment-suspect words excluded."""
    from pronunciation_lab.benchmark.analysis import alignment_suspect_words

    words = store.load(f"{rid}__wav2vec2_raw").cell.result.words
    suspect = alignment_suspect_words(words)
    return sum(
        (p.engine_evidence["operation"] != "match") + len(p.engine_evidence["extra_heard_phones"])
        for i, w in enumerate(words) if i not in suspect for p in w.phonemes
    )


def test_speed_does_not_consistently_add_deviations(store):
    groups = (("R01", "R02", "R03"), ("R05", "R06", "R07"), ("R08", "R09", "R10"),
              ("R11", "R12", "R13"), ("R14", "R15", "R16"), ("R17", "R18"))
    dev = {r: _deviations(store, r) for g in groups for r in g}
    text = compact(REPORT)
    for g in groups:
        assert " / ".join(f"{r} {dev[r]}" for r in g) in text
    exceed = [g[-1] for g in groups if all(dev[g[-1]] > dev[r] for r in g[:-1])]
    assert exceed == ["R13", "R18"]
    assert "Only R13 and R18 exceed every slower reading of their text" in text
    assert "only R13 and R18 exceed every slower reading" in HANDOFF


def test_not_decoded_composition(store):
    from pronunciation_lab.benchmark.analysis import alignment_suspect_words

    out = {}
    for eid in ("wav2vec2_raw", "openpronounce"):
        final = tds = both = n = 0
        for rid in IDS:
            words = store.load(f"{rid}__{eid}").cell.result.words
            suspect = alignment_suspect_words(words)
            for i, w in enumerate(words):
                if i in suspect:
                    continue
                for p in w.phonemes:
                    if p.engine_evidence["operation"] == "omission":
                        n += 1
                        f = p.expected.position == len(w.phonemes) - 1
                        t = p.expected.phoneme in ("t", "d", "s")
                        final, tds, both = final + f, tds + t, both + (f and t)
        out[eid] = (final, tds, both, n)
    raw, op = out["wav2vec2_raw"], out["openpronounce"]
    assert (f"{raw[0]} of {raw[3]} are word-final and {raw[1]} of {raw[3]} are /t d s/ ({raw[2]} both) — raw; "
            f"OpenPronounce {op[0]} / {op[1]} / {op[2]} of {op[3]}") in compact(REPORT)


def test_alignment_pile_locations(store):
    piles = {}
    for rid in IDS:
        for w in store.load(f"{rid}__wav2vec2_raw").cell.result.words:
            n = sum(len(p.engine_evidence["extra_heard_phones"]) for p in w.phonemes)
            if n >= 3:
                piles[rid] = (w.word, n)
    assert piles == {"R07": ("few", 3), "R08": ("the", 8), "R16": ("understand", 3)}
    assert '"understand" carries 3 inserted phones' in REPORT
    assert '"ɪ v ɪ n ɪ ŋ" to "the" as 8 insertions' in compact(REPORT)


def test_not_decoded_w_regions(store):
    for rid, word in (("R08", "will"), ("R19", "were")):
        cell = store.load(f"{rid}__wav2vec2_raw").cell
        raw = store.load_raw(cell)
        p = next(p for w in cell.result.words if w.word == word for p in w.phonemes if p.expected.phoneme == "w")
        assert p.engine_evidence["operation"] == "omission"
        pv = _peak(raw, "v", p.timing.frame_start, p.timing.frame_end)
        pw = _peak(raw, "w", p.timing.frame_start, p.timing.frame_end)
        assert f"{pv:.2f} vs {pw:.2f}" in REPORT
