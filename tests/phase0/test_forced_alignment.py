"""Phase 0 Test: ctc-forced-aligner package verification and Viterbi CTC alignment engine."""

import numpy as np
import os
import soundfile as sf
import tempfile
from ctc_forced_aligner.ctc_aligner import align_sequences
from ctc_forced_aligner import Tokenizer, forced_align, merge_repeats

def test_ctc_aligner():
    print("Testing ctc_forced_aligner core Cython/C Viterbi engine...")
    
    # 1. Test standalone CTC align_sequences function
    # Create synthetic log probabilities: (batch=1, time=20 frames, vocab=10)
    time_steps = 20
    vocab_size = 10
    blank_idx = 0
    
    # Target tokens: [1, 2, 3]
    targets = np.array([[1, 2, 3]], dtype=np.int64)
    
    # Construct synthetic log probs with high probability for target tokens at specific frames
    log_probs = np.full((1, time_steps, vocab_size), -10.0, dtype=np.float32)
    # frames 0..4: blank
    log_probs[0, 0:5, blank_idx] = 0.0
    # frames 5..8: token 1
    log_probs[0, 5:9, 1] = 0.0
    # frames 9..12: token 2
    log_probs[0, 9:13, 2] = 0.0
    # frames 13..17: token 3
    log_probs[0, 13:18, 3] = 0.0
    # frames 18..19: blank
    log_probs[0, 18:20, blank_idx] = 0.0
    
    path, scores = align_sequences(log_probs, targets, blank_idx)
    print(f"align_sequences output:")
    print(f"  Viterbi Path: {path[0].tolist()}")
    print(f"  Scores: shape={scores.shape}, mean_score={scores.mean():.4f}")
    
    assert path.shape == (1, time_steps), f"Expected shape (1, {time_steps}), got {path.shape}"
    # Verify that tokens 1, 2, 3 appeared in sequence
    extracted_non_blank = [int(p) for p in path[0] if p != blank_idx]
    # Unique consecutive
    consecutive = []
    for p in extracted_non_blank:
        if not consecutive or consecutive[-1] != p:
            consecutive.append(p)
    print(f"  Extracted token sequence: {consecutive} (Expected: [1, 2, 3])")
    assert consecutive == [1, 2, 3], f"Viterbi alignment failed to find target tokens in sequence!"
    
    # 2. Test Tokenizer
    tokenizer = Tokenizer()
    text = "think"
    tokens = tokenizer.encode(text)
    decoded = tokenizer.decode(tokens)
    print(f"\nTokenizer test:")
    print(f"  Encoded '{text}': {tokens.tolist()}")
    print(f"  Decoded: '{decoded}'")
    assert decoded == text, f"Tokenizer decode mismatch: '{decoded}' != '{text}'"
    
    print("\nSUCCESS: ctc-forced-aligner core engine verified.")

if __name__ == "__main__":
    test_ctc_aligner()
