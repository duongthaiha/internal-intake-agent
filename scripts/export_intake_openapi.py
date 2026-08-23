import json
from pathlib import Path

from intake_api.app import app


OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "openapi"
    / "intake-api.openapi.json"
)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as output_file:
        json.dump(app.openapi(), output_file, indent=2, ensure_ascii=True)
        output_file.write("\n")


if __name__ == "__main__":
    main()
