"""Phase 0 Test: facebook/wav2vec2-lv-60-espeak-cv-ft phoneme model inference on Apple Silicon."""

import time
import torch
import numpy as np
from transformers import Wav2Vec2ForCTC, AutoProcessor

def test_wav2vec2_phoneme_model():
    model_id = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(model_id)
    model = Wav2Vec2ForCTC.from_pretrained(model_id).to(device)
    model.eval()
    if device.type == "mps":
        torch.mps.synchronize()
    t_load = time.time() - t0
    
    param_size_mb = sum(p.numel() * p.element_size() for p in model.parameters()) / (1024 * 1024)
    print(f"Model loaded on {device} in {t_load:.2f}s, footprint: {param_size_mb:.1f} MB")
    
    # 1.0s dummy audio
    audio = np.random.randn(16000).astype(np.float32)
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    input_values = inputs.input_values.to(device)
    
    with torch.no_grad():
        t_infer0 = time.time()
        logits = model(input_values).logits
        if device.type == "mps":
            torch.mps.synchronize()
        t_infer = time.time() - t_infer0
        
    print(f"Inference latency: {t_infer:.3f}s, output shape: {logits.shape}")
    assert logits.shape[-1] == 392  # 392 vocab size
    assert logits.shape[0] == 1
