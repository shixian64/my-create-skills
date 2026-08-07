import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


TEMPLATE_ID = "sageroad-ai-executive-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe must be available in PATH")

    skill_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((skill_root / "assets" / "locked" / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["sha256"].items():
        path = skill_root / relative
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(f"Locked asset changed: {path}\nexpected={expected}\nactual={actual}")

    environment = json.loads((skill_root / "assets" / "locked" / "environment-lock.json").read_text(encoding="utf-8"))
    ffmpeg_version = subprocess.run(
        ["ffmpeg", "-version"], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.splitlines()[0]
    expected_ffmpeg = f"ffmpeg version {environment['ffmpeg_version']}"
    if not ffmpeg_version.startswith(expected_ffmpeg):
        raise SystemExit(f"FFmpeg environment drift:\nexpected={expected_ffmpeg}\nactual={ffmpeg_version}")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if python_version != environment["python_version"]:
        raise SystemExit(f"Python environment drift: expected {environment['python_version']} actual {python_version}")
    font = Path(environment["font_path"])
    if not font.exists() or sha256(font) != environment["font_sha256"]:
        raise SystemExit(f"Locked font missing or changed: {font}")

    source_manifest = json.loads((skill_root / "assets" / "source-engine" / "source-manifest.json").read_text(encoding="utf-8"))
    for relative, expected in source_manifest["sha256"].items():
        path = skill_root / relative
        if not path.exists():
            raise SystemExit(f"Missing source-engine artifact: {path}")
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(f"Source-engine artifact changed: {path}\nexpected={expected}\nactual={actual}")

    build_manifest = json.loads((skill_root / "assets" / "reference-build-v1" / "build-manifest.json").read_text(encoding="utf-8"))
    for relative, expected in build_manifest["sha256"].items():
        path = skill_root / relative
        if not path.exists():
            raise SystemExit(f"Missing reference-build artifact: {path}")
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(f"Reference-build artifact changed: {path}\nexpected={expected}\nactual={actual}")

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("template_id") != TEMPLATE_ID:
        raise SystemExit(f"template_id must be {TEMPLATE_ID}")
    sections = config.get("sections") or []
    if not 1 <= len(sections) <= 8:
        raise SystemExit("sections must contain 1 to 8 items")
    base = config_path.parent
    intro_audio = (base / config["intro_audio"]).resolve()
    if not intro_audio.exists():
        raise SystemExit(f"Missing intro audio: {intro_audio}")

    checked = []
    for index, section in enumerate(sections, 1):
        for field in ("stage", "title", "clip", "value_title", "value_detail"):
            if not section.get(field):
                raise SystemExit(f"Section {index} missing field: {field}")
        if len(section["title"]) > 14 or len(section["value_title"]) > 12 or len(section["value_detail"]) > 18:
            raise SystemExit(f"Section {index} text is too long; shorten it instead of changing font size")
        clip = (base / section["clip"]).resolve()
        if not clip.exists():
            raise SystemExit(f"Missing section clip: {clip}")
        info = probe(clip)
        video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
        duration = float(info["format"]["duration"])
        if not video or not audio:
            raise SystemExit(f"Section {index} must contain video and audio: {clip}")
        if video.get("width") != 1920 or video.get("height") != 1080 or video.get("r_frame_rate") != "30/1":
            raise SystemExit(f"Section {index} must be 1920x1080 at 30fps: {clip}")
        if not 14.90 <= duration <= 15.10:
            raise SystemExit(f"Section {index} must be 15 seconds: {duration:.3f}s {clip}")
        checked.append({"index": index, "clip": str(clip), "duration": duration})

    print(json.dumps({"status": "ok", "template_id": TEMPLATE_ID, "section_count": len(sections), "clips": checked}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
