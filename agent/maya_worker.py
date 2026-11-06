"""Runs inside the local-voice tool's own Python (.venv) to speak Indic lines with Maya 2
in the saved reference voice. Started by agent/clone.py; reads one JSON request per
line on stdin and prints "RESULT {...}" lines, like local-voice's IndexTTS worker.

    <local-voice>/.venv/bin/python <this file> <local-voice folder>
"""
import json
import sys
import traceback
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    sys.path.insert(0, str(root))
    import soundfile as sf
    from app.engine import SAMPLE_RATE, VoiceEngine

    meta = json.loads((root / "data" / "voice.json").read_text(encoding="utf-8"))
    engine = VoiceEngine()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            audio = engine.speak(req["text"], Path(req["reference"]), reference_text=meta["transcript"],
                                 reference_language=meta.get("language", "en"), language=req["language"])
            Path(req["output"]).parent.mkdir(parents=True, exist_ok=True)
            sf.write(req["output"], audio, SAMPLE_RATE)
            print("RESULT " + json.dumps({"ok": True}), flush=True)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            print("RESULT " + json.dumps({"ok": False, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
