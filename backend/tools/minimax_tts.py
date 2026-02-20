import os
import requests
from uuid import uuid4
from dotenv import load_dotenv

load_dotenv()

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "..", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


def generate_audio(text: str) -> str:
    """Generate audio from text using MiniMax TTS. Returns filename."""
    filename = f"memo_{uuid4().hex[:8]}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)

    try:
        response = requests.post(
            "https://api.minimaxi.com/v1/t2a_v2",
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "speech-02-turbo",
                "text": text,
                "stream": False,
                "voice_setting": {
                    "voice_id": "male-qn-qingse",
                    "speed": 1.05,
                    "vol": 1.0,
                    "pitch": 0
                }
            },
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("data", {}).get("audio"):
                import base64
                audio_bytes = base64.b64decode(data["data"]["audio"])
                with open(filepath, "wb") as f:
                    f.write(audio_bytes)
                return filename

        # Fallback if MiniMax fails
        return "fallback_memo.mp3"

    except Exception as e:
        print(f"MiniMax TTS error: {e}")
        return "fallback_memo.mp3"
