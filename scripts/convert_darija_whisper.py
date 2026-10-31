"""Build the Moroccan Darija speech model used for subtitles (run once, ~5 min, ~3 GB download).

Merges the Darija LoRA adapter ``anaszil/whisper-large-v3-turbo-darija`` (MIT license) into
``openai/whisper-large-v3-turbo`` and converts the result to an int8 CTranslate2 model, the format
faster-whisper loads. The heavy conversion libraries stay out of the project's dependencies:

    uv run --no-project --python 3.12 --with torch --with "transformers>=4.46" --with peft \\
        --with ctranslate2 --with huggingface_hub python scripts/convert_darija_whisper.py

Then set in config.toml::

    whisper_model = "models/whisper-large-v3-turbo-darija-ct2"
    whisper_language = "ar"
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile

BASE = "openai/whisper-large-v3-turbo"
ADAPTER = "anaszil/whisper-large-v3-turbo-darija"
NEEDED = ("tokenizer.json", "preprocessor_config.json")  # faster-whisper reads both


def main() -> None:
    import ctranslate2
    from huggingface_hub import hf_hub_download
    from peft import PeftModel
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="models/whisper-large-v3-turbo-darija-ct2")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="darija_merge_") as merged:
        base = WhisperForConditionalGeneration.from_pretrained(BASE)
        PeftModel.from_pretrained(base, ADAPTER).merge_and_unload().save_pretrained(merged)
        WhisperProcessor.from_pretrained(BASE).save_pretrained(merged)
        for name in NEEDED:  # recent transformers no longer writes preprocessor_config.json
            if not os.path.exists(os.path.join(merged, name)):
                shutil.copy(hf_hub_download(BASE, name), os.path.join(merged, name))
        ctranslate2.converters.TransformersConverter(merged, copy_files=list(NEEDED)).convert(
            args.out, quantization="int8", force=True
        )
    print(f"Darija model written to {args.out}")


if __name__ == "__main__":
    main()
