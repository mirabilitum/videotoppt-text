from __future__ import annotations

import json
import os

from common import load_config, output_dir


def main() -> None:
    load_config()

    from funasr import AutoModel

    out = output_dir()
    audio_wav = out / "audio.wav"
    if not audio_wav.exists():
        raise FileNotFoundError(f"Missing audio file: {audio_wav}")

    model = AutoModel(
        model=os.getenv("FUNASR_MODEL", "paraformer-zh"),
        vad_model=os.getenv("FUNASR_VAD_MODEL", "fsmn-vad"),
        punc_model=os.getenv("FUNASR_PUNC_MODEL", "ct-punc"),
        log_level="ERROR",
    )

    result = model.generate(
        input=str(audio_wav),
        batch_size_s=int(os.getenv("FUNASR_BATCH_SIZE_S", "300")),
        hotword=os.getenv("FUNASR_HOTWORD", ""),
    )

    transcript_text = "\n".join(r.get("text", "") for r in result)

    transcript_txt = out / "transcript.txt"
    transcript_json = out / "transcript.json"
    transcript_txt.write_text(transcript_text, encoding="utf-8")
    transcript_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"transcript.txt={transcript_txt}")
    print(f"transcript.json={transcript_json}")
    print(f"chars={len(transcript_text)}")
    print(f"segments={len(result)}")


if __name__ == "__main__":
    main()
