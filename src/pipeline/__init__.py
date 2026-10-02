"""Idea -> script -> edited vertical video where the line is heard verbatim."""

import os

# onnxruntime (Kokoro, faster-whisper VAD) >= 1.29 sends telemetry whose upload thread hits a
# destroyed mutex at process exit -> SIGABRT after a successful run. Only an env var set before
# onnxruntime initializes turns it off ("Disabling Telemetry"):
# https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md
# onnxruntime is imported lazily in the providers, i.e. always after this line.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
