"""Phase 0 Test: PyTorch and Apple Silicon MPS verification."""

import sys
import torch

def test_torch_mps():
    print(f"Python executable: {sys.executable}")
    print(f"PyTorch version: {torch.__version__}")
    
    assert torch.backends.mps.is_built(), "PyTorch was not built with MPS support!"
    assert torch.backends.mps.is_available(), "MPS device is not available on this Apple Silicon system!"
    
    device = torch.device("mps")
    x = torch.randn(1000, 1000, device=device)
    y = torch.matmul(x, x)
    torch.mps.synchronize()
    
    mean_val = y.mean().item()
    print(f"MPS tensor computation succeeded. Mean value: {mean_val:.4f}")
    assert isinstance(mean_val, float)
