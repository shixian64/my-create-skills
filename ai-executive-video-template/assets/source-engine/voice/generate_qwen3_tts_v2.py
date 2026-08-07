import argparse
import json
import os
import pathlib
import urllib.error
import urllib.request


API_URL = os.environ.get(
    "DASHSCOPE_API_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/"
    "aigc/multimodal-generation/generation",
)


def synthesize(text: str, output: pathlib.Path, voice: str, instructions: str) -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")

    payload = {
        "model": "qwen3-tts-instruct-flash",
        "input": {
            "text": text,
            "voice": voice,
            "language_type": "Chinese",
            "instructions": instructions,
            "optimize_instructions": True,
        },
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qwen3-TTS HTTP {error.code}: {body}") from error

    status_code = result.get("status_code", 200)
    if status_code != 200:
        raise RuntimeError(
            f"Qwen3-TTS request failed: status={status_code} "
            f"code={result.get('code')} message={result.get('message')}"
        )

    audio_url = result.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        raise RuntimeError(f"Qwen3-TTS returned no audio URL: {result}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(audio_url, timeout=180) as response:
        output.write_bytes(response.read())

    print(
        json.dumps(
            {
                "output": str(output),
                "request_id": result.get("request_id"),
                "model": payload["model"],
                "voice": voice,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--voice", default="Cherry")
    parser.add_argument(
        "--instructions",
        default=(
            "使用专业沉稳的中文女声，语气自然可信，语速适中，停顿清晰，"
            "像企业内部汇报旁白，不要夸张播音腔。"
        ),
    )
    args = parser.parse_args()
    synthesize(args.text, args.output, args.voice, args.instructions)


if __name__ == "__main__":
    main()
