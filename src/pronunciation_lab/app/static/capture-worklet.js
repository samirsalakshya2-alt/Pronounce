// M12 capture worklet: forwards every render quantum of microphone PCM with its
// absolute sample index. The main thread owns all cutting decisions; this
// processor never drops or reorders samples.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.index = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    const frames = channel ? channel.length : 128;
    const data = new Float32Array(frames);
    if (channel) data.set(channel); // no input yet (device starting): silence, still counted
    this.port.postMessage({ start: this.index, data }, [data.buffer]);
    this.index += frames;
    return true;
  }
}

registerProcessor("pronounce-capture", CaptureProcessor);
