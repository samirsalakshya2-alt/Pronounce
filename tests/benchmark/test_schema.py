from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    EngineInfo,
    ExpectedPhoneme,
    NBestCandidate,
    ObservedPhoneme,
    PhonemeResult,
    ProcessingInfo,
    PronunciationResult,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
    TimingInfo,
    WordResult,
)


def test_schema_creation():
    result = PronunciationResult(
        recording=RecordingInfo(
            id="R001",
            audio=RecordingAudio(
                original_path="data/test_recording_original.wav",
                analysis_path="data/test_recording.wav",
                sample_rate_hz=48000,
                channels=1,
                duration_ms=5000,
            ),
            target=TargetInfo(
                text="I think this is a pronunciation test."
            ),
        ),

        engine=EngineInfo(
            name="test-engine",
            version="0.1",
            mode="local",
        ),

        processing=ProcessingInfo(
            wall_time_ms=1200,
            model_load_ms=500,
            inference_ms=600,
            postprocessing_ms=100,
            realtime_factor=0.24,
            run_type="warm",
        ),

        words=[
            WordResult(
                word="think",
                expected_phonemes=["θ", "ɪ", "ŋ", "k"],
                timing=TimingInfo(
                    start_ms=1546,
                    end_ms=1807,
                    duration_ms=261,
                    source="engine",
                ),
                phonemes=[
                    PhonemeResult(
                        position=0,

                        expected=ExpectedPhoneme(
                            phoneme="θ",
                            position=0,
                            syllable=1,
                            stress=None,
                        ),

                        observed=ObservedPhoneme(
                            top="t",
                            nbest=[
                                NBestCandidate(
                                    phoneme="t",
                                    probability=0.71,
                                ),
                                NBestCandidate(
                                    phoneme="θ",
                                    probability=0.19,
                                ),
                            ],
                        ),

                        timing=TimingInfo(
                            start_ms=1546,
                            end_ms=1614,
                            duration_ms=68,
                            source="engine",
                        ),

                        acoustic=AcousticEvidence(),

                        prosody={
                            "stress_expected": None,
                            "stress_observed": None,
                            "prominence": None,
                        },
                    )
                ],
            )
        ],
    )

    assert result.schema_version == "0.2"
    assert result.recording.id == "R001"
    assert result.engine.name == "test-engine"
    assert result.processing.wall_time_ms == 1200

    phoneme = result.words[0].phonemes[0]

    assert phoneme.expected.phoneme == "θ"
    assert phoneme.observed.top == "t"
    assert phoneme.observed.nbest[0].probability == 0.71