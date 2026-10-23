/** Streaming linear interpolation; preserves fractional phase across actual block lengths.
 * Delay/quality and index mapping require later device validation; no calibration claim. */
export class Resampler {
  private buffer: number[] = []; private position = 0;
  constructor(readonly inputRate: number, readonly outputRate: number) {
    if (!(inputRate > 0 && outputRate > 0)) throw new Error('Invalid sample rate');
  }
  push(input: Float32Array): Float32Array {
    for (const value of input) this.buffer.push(value);
    const output: number[] = []; const step = this.inputRate / this.outputRate;
    while (this.position + 1 < this.buffer.length) {
      const index = Math.floor(this.position), fraction = this.position - index;
      output.push(this.buffer[index] * (1 - fraction) + this.buffer[index + 1] * fraction);
      this.position += step;
    }
    const consumed = Math.floor(this.position);
    this.buffer.splice(0, consumed); this.position -= consumed;
    return Float32Array.from(output);
  }
}
export function encodePCM(input: Float32Array): string {
  const bytes = new Uint8Array(input.length * 2), view = new DataView(bytes.buffer);
  input.forEach((sample, index) => view.setInt16(index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767)), true));
  let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
export function decodePCM(encoded: string): Float32Array {
  if (encoded.length > 256000) throw new Error('Audio message exceeds bound');
  const binary = atob(encoded); if (binary.length % 2) throw new Error('Odd PCM byte count');
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0)), view = new DataView(bytes.buffer);
  return Float32Array.from({length: bytes.length / 2}, (_, index) => view.getInt16(index * 2, true) / 32768);
}
