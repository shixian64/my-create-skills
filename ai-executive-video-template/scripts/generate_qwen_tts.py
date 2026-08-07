import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
VOICE_INSTRUCTIONS = (
    "使用专业沉稳的中文女声，语气自然可信，语速适中，停顿清晰，"
    "像企业内部汇报旁白，不要新闻播音腔，不要过度抒情。"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the locked Cherry narration voice.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is not configured")

    payload = {
        "model": "qwen3-tts-instruct-flash",
        "input": {
            "text": args.text,
            "voice": "Cherry",
            "language_type": "Chinese",
            "instructions": VOICE_INSTRUCTIONS,
            "optimize_instructions": True,
        },
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Qwen3-TTS HTTP {error.code}: {body}") from error

    audio_url = result.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        raise SystemExit(f"Qwen3-TTS returned no audio URL: {result}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(audio_url, timeout=180) as response:
        args.output.write_bytes(response.read())
    print(json.dumps({"output": str(args.output), "voice": "Cherry"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
