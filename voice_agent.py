import subprocess
import requests
import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write, read
import queue
import time
import re
import unicodedata

SAMPLE_RATE = 16000
CHUNK_DURATION = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

DEVICE = 1
WHISPER = r"D:\Projects\local-voice-agent\whisper.cpp\build\bin\Release\whisper-cli.exe"
WHISPER_MODEL = r"D:\Projects\local-voice-agent\whisper.cpp\ggml-tiny.en.bin"
PIPER = r"C:\Users\rishi\AppData\Local\Programs\Python\Python310\Scripts\piper.exe"
VOICE = "en_US-lessac-medium"

AUDIO_FILE = "input.wav"
OUTPUT_FILE = "response.wav"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:8b"

SPEECH_THRESHOLD = 150
SILENCE_DURATION = 1.0
MAX_RECORDING_TIME = 15
WAIT_FOR_SPEECH_TIMEOUT = 10
MAX_HISTORY_MESSAGES = 20


def record_until_silence():
    audio_queue = queue.Queue()
    frames = []
    speaking = False
    silence_time = 0
    start_time = None
    wait_start = time.time()

    def callback(indata, frame_count, time_info, status):
        audio_queue.put(indata.copy())

    print("\n🎤 Listening... Speak naturally.")

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            device=DEVICE,
            channels=1,
            dtype="int16",
            callback=callback
        ):
            while True:
                try:
                    chunk = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                level = np.sqrt(
                    np.mean(chunk.astype(np.float32) ** 2)
                )

                if level > SPEECH_THRESHOLD:
                    if not speaking:
                        speaking = True
                        start_time = time.time()
                        print("🗣️ Speech detected...")

                    silence_time = 0
                    frames.append(chunk)

                elif speaking:
                    frames.append(chunk)
                    silence_time += CHUNK_DURATION

                    if silence_time >= SILENCE_DURATION:
                        print("⏹️ Speech ended.")
                        break

                if (
                    not speaking
                    and time.time() - wait_start >= WAIT_FOR_SPEECH_TIMEOUT
                ):
                    print("No speech detected. Listening again...")
                    return False

                if (
                    speaking
                    and time.time() - start_time >= MAX_RECORDING_TIME
                ):
                    print("⏹️ Maximum recording time reached.")
                    break

    except Exception as error:
        print(f"Microphone error: {error}")
        return False

    if not frames:
        return False

    audio = np.concatenate(frames, axis=0)
    write(AUDIO_FILE, SAMPLE_RATE, audio)
    return True


def transcribe():
    result = subprocess.run(
        [
            WHISPER,
            "-m", WHISPER_MODEL,
            "-f", AUDIO_FILE,
            "-nt"
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    if result.returncode != 0:
        print("Whisper error:", result.stderr)
        return ""

    return result.stdout.strip()


def ask_qwen(message, history):
    messages = history + [
        {
            "role": "user",
            "content": message
        }
    ]

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "keep_alive": "10m"
        },
        timeout=120
    )

    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def speak(text):
    print("🔊 Speaking...")

    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')

    # Remove emojis and unsupported Unicode characters.
    text = "".join(
        char for char in text
        if unicodedata.category(char) not in {"Cs", "So", "Sk", "Cf"}
    ).strip()

    if not text:
        print("No speakable text.")
        return

    result = subprocess.run(
        [
            PIPER,
            "--model", VOICE,
            "--output_file", OUTPUT_FILE,
            "--sentence-silence", "0.35"
        ],
        input=text,
        text=True,
        encoding="utf-8",
        capture_output=True
    )

    if result.returncode != 0:
        print("Piper error:", result.stderr)
        return

    sample_rate, audio = read(OUTPUT_FILE)
    sd.play(audio, sample_rate)
    sd.wait()


def main():
    history = [
        {
            "role": "system",
            "content": (
                "You are a helpful local voice assistant. "
                "Answer naturally and conversationally. "
                "Usually use one to three short sentences. "
                "Avoid long explanations unless asked. "
                "Your answers will be spoken aloud."
            )
        }
    ]

    print("=" * 45)
    print("       LOCAL VOICE AGENT")
    print("  Say 'exit' to end the conversation")
    print("=" * 45)

    while True:
        try:
            if not record_until_silence():
                continue

            print("🧠 Transcribing...")
            user_text = transcribe()

            if not user_text:
                print("Couldn't understand that. Try again.")
                continue

            print(f"\n👤 You: {user_text}")

            command = re.sub(r"[^\w\s]", "", user_text.lower()).split()

            if (
                (command and all(word in {"exit", "quit"} for word in command))
                or command == ["stop", "assistant"]
            ):
                print("👋 Voice agent stopped.")
                break

            print("🤖 Qwen is thinking...")

            try:
                reply = ask_qwen(user_text, history)
            except requests.RequestException as error:
                print(f"❌ Ollama connection error: {error}")
                print("Check that Ollama is running.")
                continue

            print(f"\n🤖 Assistant: {reply}")

            history.append({
                "role": "user",
                "content": user_text
            })

            history.append({
                "role": "assistant",
                "content": reply
            })

            if len(history) > MAX_HISTORY_MESSAGES + 1:
                history = [history[0]] + history[-MAX_HISTORY_MESSAGES:]

            try:
                speak(reply)
            except (subprocess.SubprocessError, OSError) as error:
                print(f"❌ Speech output error: {error}")

        except KeyboardInterrupt:
            print("\n👋 Voice agent stopped.")
            break

        except Exception as error:
            print(f"\n❌ Error: {error}")
            print("Restarting the listening loop...")


if __name__ == "__main__":
    main()