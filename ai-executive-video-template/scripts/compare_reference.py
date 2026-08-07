import argparse
import json
import re
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def probe(path: Path) -> dict:
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a rebuilt video with the locked visual master.")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--min-ssim", type=float, default=0.985)
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parents[1]
    candidate = args.candidate.resolve()
    reference = (args.reference or skill_root / "assets" / "reference" / "master_reference_3_sections.mp4").resolve()
    if not candidate.exists() or not reference.exists():
        raise SystemExit(f"Missing candidate or reference: {candidate} / {reference}")

    candidate_info = probe(candidate)
    reference_info = probe(reference)
    candidate_duration = float(candidate_info["format"]["duration"])
    reference_duration = float(reference_info["format"]["duration"])
    duration_delta = abs(candidate_duration - reference_duration)
    if duration_delta > 0.15:
        raise SystemExit(f"Duration drift: {candidate_duration:.3f}s vs {reference_duration:.3f}s")

    null_device = "NUL"
    result = run([
        "ffmpeg", "-hide_banner", "-i", str(candidate), "-i", str(reference),
        "-lavfi", "[0:v]setpts=PTS-STARTPTS[v0];[1:v]setpts=PTS-STARTPTS[v1];[v0][v1]ssim=shortest=1",
        "-an", "-f", "null", null_device,
    ])
    matches = re.findall(r"All:([0-9.]+)", result.stderr)
    if not matches:
        raise SystemExit("FFmpeg did not return an SSIM score")
    score = float(matches[-1])
    report = {
        "status": "ok" if score >= args.min_ssim else "failed",
        "candidate": str(candidate),
        "reference": str(reference),
        "candidate_duration": candidate_duration,
        "reference_duration": reference_duration,
        "duration_delta": duration_delta,
        "ssim": score,
        "minimum_ssim": args.min_ssim,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if score < args.min_ssim:
        raise SystemExit(f"Visual regression: SSIM {score:.6f} < {args.min_ssim:.6f}")


if __name__ == "__main__":
    main()
