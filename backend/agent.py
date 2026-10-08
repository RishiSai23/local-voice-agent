import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:8b"


def ask_agent(message: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful local voice assistant. "
                        "Keep responses natural, concise and conversational. "
                        "Your responses will be spoken aloud."
                    )
                },
                {
                    "role": "user",
                    "content": message
                }
            ],
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()

    return response.json()["message"]["content"]