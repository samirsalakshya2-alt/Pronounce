"""Phase 0 Test: eSpeak-ng and phonemizer G2P verification."""

from phonemizer.backend import EspeakBackend

def test_espeak_g2p():
    assert EspeakBackend.is_available(), "eSpeak backend is not available!"
    
    backend = EspeakBackend(language='en-us', preserve_punctuation=True, with_stress=True)
    
    test_cases = [
        ("think", "θ"),
        ("three", "θ"),
        ("ship", "ʃ"),
        ("sheep", "ʃ"),
        ("particularly", "p"),
    ]
    
    for word, expected_char in test_cases:
        phones = backend.phonemize([word], strip=True)[0]
        assert len(phones) > 0
        assert expected_char in phones, f"Expected '{expected_char}' in '{phones}' for word '{word}'"
        print(f"G2P: '{word}' -> [{phones}]")
