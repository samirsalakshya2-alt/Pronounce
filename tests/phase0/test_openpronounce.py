"""Phase 0 Test: OpenPronounce package import and API verification."""

import inspect
import openpronounce

def test_openpronounce():
    print(f"OpenPronounce version: {getattr(openpronounce, '__version__', 'unknown')}")
    print("Exported symbols:")
    symbols = [s for s in dir(openpronounce) if not s.startswith("_")]
    for s in symbols:
        val = getattr(openpronounce, s)
        sig = inspect.signature(val) if callable(val) and not isinstance(val, type) else type(val)
        print(f"  {s}: {sig}")
        
    print("\nSUCCESS: OpenPronounce package verified.")

if __name__ == "__main__":
    test_openpronounce()
