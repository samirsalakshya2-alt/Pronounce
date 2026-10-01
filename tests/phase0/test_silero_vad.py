"""Phase 0 Test: Silero VAD loading and frame probability verification."""

import torch
import numpy as np
from silero_vad import load_silero_vad, get_speech_timestamps

def test_silero_vad():
    print("Testing Silero VAD loading...")
    model = load_silero_vad()
    print(f"Silero VAD model loaded successfully: {type(model)}")
    
    sr = 16000
    # Test frame inference on a 512-sample chunk (standard Silero VAD window at 16kHz)
    chunk = torch.zeros(512, dtype=torch.float32)
    prob_silence = model(chunk, sr).item()
    print(f"Silero probability on silence: {prob_silence:.6f} (Expected near 0.0)")
    
    # Generate multi-harmonic acoustic signal
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    # Fundamental 120Hz + harmonics (120, 240, 360, 480, 600, 1200, 2400) resembling voiced speech
    speech_like = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate([120, 240, 360, 480, 720, 1200, 2400]))
    speech_like = (speech_like / np.max(np.abs(speech_like)) * 0.7).astype(np.float32)
    
    chunk_speech = torch.from_numpy(speech_like[:512])
    prob_speech = model(chunk_speech, sr).item()
    print(f"Silero probability on harmonic audio: {prob_speech:.6f}")
    
    # Model runs forward passes without error
    assert 0.0 <= prob_silence <= 1.0
    assert 0.0 <= prob_speech <= 1.0
    print("\nSUCCESS: Silero VAD forward pass and API verified.")

if __name__ == "__main__":
    test_silero_vad()
