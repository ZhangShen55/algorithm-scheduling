from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompt"

def load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")