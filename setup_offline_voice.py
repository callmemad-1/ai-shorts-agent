"""Download the free Kokoro offline voice model (~350 MB) into assets/tts/."""
import urllib.request
from pathlib import Path

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {"kokoro-v1.0.onnx": "kokoro-v1.0.onnx", "voices-v1.0.bin": "voices-v1.0.bin"}
dest = Path(__file__).resolve().parent / "assets" / "tts"
dest.mkdir(parents=True, exist_ok=True)

for remote, local in FILES.items():
    out = dest / local
    if out.exists() and out.stat().st_size > 1_000_000:
        print(f"already have {local}")
        continue
    print(f"downloading {remote} ...")
    urllib.request.urlretrieve(BASE + remote, out)
    print(f"  saved {out} ({out.stat().st_size / 1e6:.0f} MB)")
print("Offline voice ready. Use: python main.py --tts kokoro")
