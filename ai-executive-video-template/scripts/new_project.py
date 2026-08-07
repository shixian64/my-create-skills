import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a project from the locked video template.")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()

    source = Path(__file__).resolve().parents[1] / "assets" / "project-template"
    target = args.target.resolve()
    if target.exists():
        raise SystemExit(f"Target already exists: {target}")
    shutil.copytree(source, target)
    print(target)


if __name__ == "__main__":
    main()
