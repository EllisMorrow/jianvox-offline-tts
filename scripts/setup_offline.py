"""Validate every payload, then install only from the local wheel cache."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from download_tts import digest, load_manifest, target_path, write_json_atomic

PROJECT = Path(__file__).resolve().parent.parent


def verify_payload(root, manifest):
    for index, item in enumerate(manifest["files"], 1):
        path = target_path(root, item["path"])
        if not path.is_file() or path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise RuntimeError(f"File missing or SHA-256 mismatch: {item['path']}. Run download again.")
        print(f"Verify {index}/{len(manifest['files'])}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=PROJECT / ".downloads")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--skip-pip", action="store_true", help="For adopting an already installed runtime")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError("Use 64-bit Python 3.12.")
    manifest = load_manifest(PROJECT / "scripts" / "download_manifest.json")
    verify_payload(args.downloads, manifest)
    if args.check_only:
        print("PAYLOAD_CHECK_PASSED", flush=True)
        return
    if not args.skip_pip:
        common = [sys.executable, "-m", "pip", "install", "--no-index", "--find-links",
                  str(args.downloads / "安装包" / "wheels"), "--require-hashes", "--no-build-isolation"]
        for filename in ["requirements-bootstrap.txt", "requirements-offline.txt"]:
            subprocess.run(common + ["-r", str(PROJECT / "scripts" / filename)], check=True)
        subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    import tkinter
    import customtkinter
    import audiotsm
    import torch
    import sounddevice
    if not torch.cuda.is_available():
        raise RuntimeError("NVIDIA CUDA is unavailable. Check GPU/driver; no CPU fallback is enabled.")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("This configuration requires an NVIDIA GPU supporting BF16 (e.g. RTX 30 or newer).")
    if not any(device["max_output_channels"] for device in sounddevice.query_devices()):
        raise RuntimeError("No audio output device is available.")
    # Actually create a CUDA tensor to catch incompatible driver/device binaries.
    torch.ones(1, device="cuda", dtype=torch.bfloat16).add_(1)
    torch.cuda.synchronize()
    model = args.downloads.resolve() / "模型" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"
    (PROJECT / ".local").mkdir(exist_ok=True)
    write_json_atomic(PROJECT / ".local" / "install.json",
                      {"runtime_python": sys.executable, "model_path": str(model),
                       "downloads_path": str(args.downloads.resolve())})
    print("OFFLINE_SETUP_PASSED", flush=True)


if __name__ == "__main__":
    main()
