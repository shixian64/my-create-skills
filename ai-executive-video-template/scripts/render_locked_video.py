import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path


TEMPLATE_ID = "sageroad-ai-executive-v1"
FONT = "C\\:/Windows/Fonts/msyhbd.ttc"
INTRO_DURATION = 8.30
SECTION_DURATION = 15.00
LAST_CONTENT = 11.60
LAST_HOLD = 2.00
END_DURATION = 3.65
XFADE = 0.25
COMMAND_LOG: list[list[str]] = []


def run(command: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    COMMAND_LOG.append(command)
    return subprocess.run(
        command, check=True, capture_output=capture, text=True,
        encoding="utf-8", errors="replace"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_filter(path: Path, content: str) -> Path:
    path.write_text(content + "\n", encoding="utf-8")
    return path


def esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            .replace("%", "\\%").replace(",", "\\,"))


def enable(start: float, end: float) -> str:
    return f":enable='between(t\\,{start:.2f}\\,{end:.2f})'"


def drawtext(text: str, x: str, y: int, size: int, color: str, start: float | None = None) -> str:
    suffix = enable(start, INTRO_DURATION - 0.10) if start is not None else ""
    return f"drawtext=fontfile='{FONT}':text='{esc(text)}':x={x}:y={y}:fontsize={size}:fontcolor={color}{suffix}"


def chinese_count(count: int) -> str:
    return "一二三四五六七八"[count - 1]


def card_layout(count: int, intro: bool) -> list[tuple[int, int, int, int]]:
    if count <= 3:
        width, height, gap = 430, 210 if intro else 220, 80
        y = 410 if intro else 340
        start = (1920 - (count * width + (count - 1) * gap)) // 2
        return [(start + i * (width + gap), y, width, height) for i in range(count)]
    if count == 4:
        width, height, gap = 360, 210 if intro else 220, 40
        y = 410 if intro else 340
        start = (1920 - (count * width + (count - 1) * gap)) // 2
        return [(start + i * (width + gap), y, width, height) for i in range(count)]

    width, gap = 360, 40
    height = 180
    rows = math.ceil(count / 4)
    result = []
    for row in range(rows):
        row_count = min(4, count - row * 4)
        start = (1920 - (row_count * width + (row_count - 1) * gap)) // 2
        y = (330 if intro else 315) + row * (height + 35)
        for col in range(row_count):
            result.append((start + col * (width + gap), y, width, height))
    return result


def render_intro(bg: Path, audio: Path, config: dict, output: Path, filter_path: Path) -> None:
    sections = config["sections"]
    count = len(sections)
    filters = [
        "drawbox=x=160:y=112:w=1600:h=5:color=0x2563EB:t=fill",
        drawtext(config.get("intro_hint", "内部 AI 应用进展"), "(w-text_w)/2", 176, 28, "0x707D8B"),
        drawtext(config.get("title", "AI 正在融入研发日常"), "(w-text_w)/2", 245, 70, "0x162033"),
    ]
    layout = card_layout(count, intro=True)
    for index, (section, (x, y, width, height)) in enumerate(zip(sections, layout)):
        start = 1.10 + index * (0.65 if count <= 3 else 0.35)
        filters.extend([
            f"drawbox=x={x}:y={y}:w={width}:h={height}:color=white@0.98:t=fill{enable(start, INTRO_DURATION - 0.10)}",
            f"drawbox=x={x}:y={y}:w={width}:h={height}:color=0xD7E2EF:t=2{enable(start, INTRO_DURATION - 0.10)}",
            drawtext(section["stage"], f"{x}+({width}-text_w)/2", y + (45 if count <= 4 else 30), 30, "0x526171", start),
            drawtext(section["title"], f"{x}+({width}-text_w)/2", y + (120 if count <= 4 else 92), 38, "0x2563EB", start),
        ])
    footer_y = 730 if count <= 4 else 815
    footer_start = 3.25 if count <= 3 else min(4.0, 1.25 + count * 0.35)
    filters.extend([
        f"drawbox=x=355:y={footer_y}:w=1210:h=115:color=0x2563EB:t=fill{enable(footer_start, INTRO_DURATION - 0.10)}",
        drawtext(f"当前展示{chinese_count(count)}个高频场景", "(w-text_w)/2", footer_y + 35, 32, "white", footer_start),
        "fade=t=in:st=0:d=0.25,fade=t=out:st=8.12:d=0.18",
        "format=yuv420p",
    ])
    video_filter = ",".join(filters)
    audio_filter = f"aresample=48000,loudnorm=I=-18:TP=-2:LRA=7,apad,atrim=duration={INTRO_DURATION},asetpts=PTS-STARTPTS"
    filter_graph = f"[0:v]{video_filter}[v];[1:a]{audio_filter}[a]"
    write_filter(filter_path, filter_graph)
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", str(INTRO_DURATION), "-i", str(bg),
         "-i", str(audio), "-filter_complex_script", str(filter_path),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1",
         "-movflags", "+faststart", str(output)])


def normalize_section(source: Path, output: Path, filter_path: Path) -> None:
    filter_complex = (
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,"
        f"fps=30,tpad=stop_mode=clone:stop_duration=1,trim=duration={SECTION_DURATION},setpts=PTS-STARTPTS,format=yuv420p[v];"
        f"[0:a]aresample=48000,loudnorm=I=-18:TP=-2:LRA=7,apad,atrim=duration={SECTION_DURATION},asetpts=PTS-STARTPTS[a]"
    )
    write_filter(filter_path, filter_complex)
    run(["ffmpeg", "-y", "-i", str(source), "-filter_complex_script", str(filter_path),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1",
         "-movflags", "+faststart", str(output)])


def render_end(bg: Path, config: dict, output: Path, filter_path: Path) -> None:
    sections = config["sections"]
    count = len(sections)
    filters = [
        drawtext(config.get("intro_hint", "内部 AI 应用进展"), "145", 110, 27, "0x005AA9"),
        drawtext(f"{chinese_count(count)}个高频研发场景的 AI 应用实践", "145", 165, 56, "0x162033"),
        "drawbox=x=145:y=255:w=1630:h=5:color=0x055AAC:t=fill",
    ]
    layout = card_layout(count, intro=False)
    for section, (x, y, width, height) in zip(sections, layout):
        filters.extend([
            f"drawbox=x={x}:y={y}:w={width}:h={height}:color=white@0.95:t=fill",
            f"drawbox=x={x}:y={y}:w=8:h={height}:color=0x00A55A:t=fill",
            drawtext(section["value_title"], str(x + 60), y + (55 if count <= 4 else 35), 38, "0x162033"),
            drawtext(section["value_detail"], str(x + 60), y + (125 if count <= 4 else 105), 25, "0x526171"),
        ])
    bottom_y = 650 if count <= 4 else 780
    filters.extend([
        f"drawbox=x=235:y={bottom_y}:w=1450:h=140:color=0x142B3E@0.96:t=fill",
        drawtext(config.get("end_value", "工程师聚焦验证 质量与决策"), "(w-text_w)/2", bottom_y + 45, 40, "white"),
        "format=yuv420p",
    ])
    filter_graph = f"[0:v]{','.join(filters)}[v];[1:a]atrim=duration={END_DURATION}[a]"
    write_filter(filter_path, filter_graph)
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", str(END_DURATION), "-i", str(bg),
         "-f", "lavfi", "-t", str(END_DURATION), "-i", "anullsrc=r=48000:cl=mono",
         "-filter_complex_script", str(filter_path),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1",
         "-movflags", "+faststart", str(output)])


def render_main(sections: list[Path], end_clip: Path, output: Path, filter_path: Path) -> float:
    command = ["ffmpeg", "-y"]
    for section in sections:
        command.extend(["-i", str(section)])
    command.extend(["-i", str(end_clip)])

    parts = []
    last = len(sections) - 1
    for index in range(last):
        parts.append(f"[{index}:v]setpts=PTS-STARTPTS,settb=AVTB[v{index}]")
        parts.append(f"[{index}:a]asetpts=PTS-STARTPTS[a{index}]")
    parts.append(f"[{last}:v]trim=duration={LAST_CONTENT},setpts=PTS-STARTPTS,settb=AVTB,tpad=stop_mode=clone:stop_duration={LAST_HOLD},trim=duration={LAST_CONTENT + LAST_HOLD}[vl]")
    parts.append(f"[{last}:a]atrim=duration={LAST_CONTENT},asetpts=PTS-STARTPTS,apad=pad_dur={LAST_HOLD},atrim=duration={LAST_CONTENT + LAST_HOLD}[al]")
    if len(sections) == 1:
        parts.extend(["[vl]null[bodyv]", "[al]anull[bodya]"])
    else:
        inputs = "".join(f"[v{i}][a{i}]" for i in range(last)) + "[vl][al]"
        parts.append(f"{inputs}concat=n={len(sections)}:v=1:a=1[bodyv][bodya]")

    end_index = len(sections)
    parts.append(f"[{end_index}:v]setpts=PTS-STARTPTS,settb=AVTB[endv]")
    parts.append(f"[{end_index}:a]asetpts=PTS-STARTPTS[enda]")
    body_duration = last * SECTION_DURATION + LAST_CONTENT + LAST_HOLD
    parts.append(f"[bodyv][endv]xfade=transition=fade:duration={XFADE}:offset={body_duration - XFADE}[v]")
    parts.append(f"[bodya][enda]acrossfade=d={XFADE}:c1=tri:c2=tri[a]")
    write_filter(filter_path, ";".join(parts))
    command.extend(["-filter_complex_script", str(filter_path), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1", "-movflags", "+faststart", str(output)])
    run(command)
    return body_duration + END_DURATION - XFADE


def concatenate_intro(intro: Path, main: Path, output: Path, filter_path: Path) -> None:
    filters = (
        "[0:v]setpts=PTS-STARTPTS,format=yuv420p[v0];[0:a]asetpts=PTS-STARTPTS[a0];"
        "[1:v]setpts=PTS-STARTPTS,format=yuv420p[v1];[1:a]asetpts=PTS-STARTPTS[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    write_filter(filter_path, filters)
    run(["ffmpeg", "-y", "-i", str(intro), "-i", str(main), "-filter_complex_script", str(filter_path),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1",
         "-movflags", "+faststart", str(output)])


def qa(output: Path, expected_duration: float, qa_dir: Path) -> dict:
    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size,bit_rate",
                 "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                 "-of", "json", str(output)], capture=True)
    info = json.loads(probe.stdout)
    duration = float(info["format"]["duration"])
    if abs(duration - expected_duration) > 0.15:
        raise SystemExit(f"Unexpected duration: {duration:.3f}s expected {expected_duration:.3f}s")
    run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "NUL"])
    black = run(["ffmpeg", "-hide_banner", "-i", str(output), "-vf", "blackdetect=d=0.20:pix_th=0.02",
                 "-an", "-f", "null", "NUL"], capture=True)
    if "black_start" in black.stderr:
        raise SystemExit("Black frame interval detected")
    loudness = run(["ffmpeg", "-hide_banner", "-i", str(output), "-af", "ebur128=framelog=quiet:peak=true",
                    "-vn", "-f", "null", "NUL"], capture=True)

    qa_dir.mkdir(parents=True, exist_ok=True)
    frame_times = [4.2, INTRO_DURATION + 2.0, max(INTRO_DURATION + 2.0, expected_duration - 5.0), expected_duration - 2.0]
    frame_paths = []
    for index, timestamp in enumerate(frame_times, 1):
        frame = qa_dir / f"frame_{index}_{timestamp:.1f}s.jpg"
        run(["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(output), "-frames:v", "1", "-q:v", "3", str(frame)])
        frame_paths.append(str(frame))
    report = {
        "status": "ok",
        "template_id": TEMPLATE_ID,
        "duration": duration,
        "expected_duration": expected_duration,
        "streams": info["streams"],
        "black_frames": "none",
        "loudness_summary": loudness.stderr[loudness.stderr.rfind("Summary:"):].strip(),
        "qa_frames": frame_paths,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the locked Sageroad AI executive video template.")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe must be available in PATH")

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("template_id") != TEMPLATE_ID:
        raise SystemExit(f"template_id must be {TEMPLATE_ID}")
    sections = config.get("sections") or []
    if not 1 <= len(sections) <= 8:
        raise SystemExit("sections must contain 1 to 8 items")

    root = Path(__file__).resolve().parents[1]
    bg = root / "assets" / "locked" / "sageroad_template_bg.png"
    project = config_path.parent
    output = (project / config["output"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / ".locked-template-work"
    work.mkdir(parents=True, exist_ok=True)
    filters_dir = work / "filtergraphs"
    sections_dir = work / "normalized-sections"
    filters_dir.mkdir(parents=True, exist_ok=True)
    sections_dir.mkdir(parents=True, exist_ok=True)
    COMMAND_LOG.clear()

    intro_audio = (project / config["intro_audio"]).resolve()
    source_assets = {
        "config": {"path": str(config_path), "sha256": sha256(config_path)},
        "background": {"path": str(bg), "sha256": sha256(bg)},
        "intro_audio": {"path": str(intro_audio), "sha256": sha256(intro_audio)},
        "sections": [],
    }
    for section in sections:
        clip = (project / section["clip"]).resolve()
        source_assets["sections"].append({"path": str(clip), "sha256": sha256(clip)})
    (work / "source-snapshot.json").write_text(
        json.dumps({"template_id": TEMPLATE_ID, "source_assets": source_assets, "config": config}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    intro = work / "intro.mp4"
    render_intro(bg, intro_audio, config, intro, filters_dir / "01_intro.ffscript")
    normalized = []
    for index, section in enumerate(sections, 1):
        target = sections_dir / f"section_{index:02d}.mp4"
        normalize_section((project / section["clip"]).resolve(), target, filters_dir / f"02_section_{index:02d}.ffscript")
        normalized.append(target)
    end = work / "end.mp4"
    render_end(bg, config, end, filters_dir / "03_end.ffscript")
    main_video = work / "main.mp4"
    main_duration = render_main(normalized, end, main_video, filters_dir / "04_main.ffscript")
    concatenate_intro(intro, main_video, output, filters_dir / "05_concat.ffscript")
    expected_duration = INTRO_DURATION + main_duration
    report = qa(output, expected_duration, output.parent / "qa_frames")
    report["intermediate_workdir"] = str(work)
    report["intermediate_files"] = [
        str(work / "source-snapshot.json"),
        str(intro),
        *[str(path) for path in normalized],
        str(end),
        str(main_video),
        *[str(path) for path in sorted(filters_dir.glob("*.ffscript"))],
        str(work / "render-commands.json"),
    ]
    (work / "render-commands.json").write_text(
        json.dumps({"template_id": TEMPLATE_ID, "commands": COMMAND_LOG}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output.parent / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "duration": report["duration"], "section_count": len(sections), "qa": "ok"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
