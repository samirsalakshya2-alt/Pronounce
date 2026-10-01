"""Phase 0 Test: Audio input/output devices verification."""

import sounddevice as sd

def test_audio_devices():
    devices = sd.query_devices()
    default_input = sd.default.device[0]
    default_output = sd.default.device[1]
    
    input_devices = [idx for idx, d in enumerate(devices) if d['max_input_channels'] > 0]
    output_devices = [idx for idx, d in enumerate(devices) if d['max_output_channels'] > 0]
    
    print(f"Total audio devices: {len(devices)}")
    print(f"Input devices count: {len(input_devices)}, default input: {default_input}")
    print(f"Output devices count: {len(output_devices)}, default output: {default_output}")
    
    assert len(input_devices) > 0, "No audio input devices detected!"
    assert len(output_devices) > 0, "No audio output devices detected!"
