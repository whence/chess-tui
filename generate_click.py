"""Generate a better chess piece click sound."""

import math
import struct
import wave

def generate_chess_click(duration=0.08, sample_rate=44100):
    """Generate a chess piece click sound.
    
    This creates a short, sharp sound like a piece hitting the board:
    - Quick attack
    - Short decay
    - Slight resonance
    """
    num_samples = int(duration * sample_rate)
    samples = []
    
    for i in range(num_samples):
        t = i / sample_rate
        
        # Main click (sharp attack, quick decay)
        click = math.exp(-t * 80) * math.sin(2 * math.pi * 800 * t)
        
        # Add some higher frequency for sharpness
        sharp = math.exp(-t * 120) * math.sin(2 * math.pi * 2000 * t) * 0.3
        
        # Add a subtle low thud
        thud = math.exp(-t * 40) * math.sin(2 * math.pi * 200 * t) * 0.2
        
        # Combine and normalize
        sample = click + sharp + thud
        sample = max(-1.0, min(1.0, sample))  # Clamp to [-1, 1]
        samples.append(int(sample * 32767))  # Convert to 16-bit integer
    
    return samples

def save_wav(filename, samples, sample_rate=44100):
    """Save samples to a WAV file."""
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        
        # Write samples
        data = struct.pack(f'<{len(samples)}h', *samples)
        wav_file.writeframes(data)

def main():
    samples = generate_chess_click()
    
    # Save to the chess_tui directory
    output_path = "src/chess_tui/click.wav"
    save_wav(output_path, samples)
    
    print(f"Generated chess click sound: {output_path}")
    print(f"Duration: {len(samples) / 44100 * 1000:.1f}ms")
    print(f"Sample rate: 44100 Hz")
    print(f"Bit depth: 16-bit")

if __name__ == "__main__":
    main()
