import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the three-section master and retain every intermediate artifact.")
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve()
    if workspace.exists():
        raise SystemExit(f"Workspace already exists; use a new empty path: {workspace}")

    project = workspace / "project"
    shutil.copytree(skill_root / "assets" / "project-template", project)
    config = project / "config.json"
    output_relative = json.loads(config.read_text(encoding="utf-8"))["output"]
    output = project / output_relative

    run([sys.executable, str(skill_root / "scripts" / "validate_template.py"), "--config", str(config)])
    run([sys.executable, str(skill_root / "scripts" / "render_locked_video.py"), "--config", str(config)])
    run([sys.executable, str(skill_root / "scripts" / "compare_reference.py"), "--candidate", str(output)])
    print(json.dumps({
        "status": "ok",
        "workspace": str(workspace),
        "rebuilt_video": str(output),
        "intermediate_workdir": str(output.parent / ".locked-template-work"),
        "qa": str(output.parent / "qa.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
