import subprocess
import re
import requests
import sounddevice as sd
from scipy.io.wavfile import write, read

SAMPLE_RATE = 16000
DURATION = 5

WHISPER = r"D:\Projects\local-voice-agent\whisper.cpp\build\bin\Release\whisper-cli.exe"
MODEL = r"D:\Projects\local-voice-agent\whisper.cpp\ggml-base.bin"
AUDIO = r"D:\Projects\local-voice-agent\input.wav"

PIPER = r"C:\Users\rishi\AppData\Local\Programs\Python\Python310\Scripts\piper.exe"
VOICE = "en_US-lessac-medium"
OUTPUT = "response.wav"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:8b"


def record_audio():
    print("\n🎤 Speak now...")

    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )

    sd.wait()
    write(AUDIO, SAMPLE_RATE, audio)

    print("✅ Recording complete")


def transcribe():
    result = subprocess.run(
        [
            WHISPER,
            "-m", MODEL,
            "-f", AUDIO,
            "-nt"
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    return result.stdout.strip()


def ask_qwen(text):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful local voice assistant. "
                        "Keep responses short, natural and conversational. "
                        "Your response will be spoken aloud."
                    )
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()

    return response.json()["message"]["content"]


def speak(text):
    print("🔊 Speaking...")

    subprocess.run(
        [
            PIPER,
            "--model", VOICE,
            "--output_file", OUTPUT
        ],
        input=text,
        text=True,
        encoding="utf-8"
    )

    rate, audio = read(OUTPUT)

    sd.play(audio, rate)
    sd.wait()


def main():
    record_audio()

    print("🧠 Transcribing...")
    text = transcribe()

    print(f"\n👤 You: {text}")

    if not text:
        print("❌ No speech detected.")
        return

    print("🤖 Qwen is thinking...")
    reply = ask_qwen(text)

    print(f"\n🤖 Assistant: {reply}")

    speak(reply)


if __name__ == "__main__":
    main()