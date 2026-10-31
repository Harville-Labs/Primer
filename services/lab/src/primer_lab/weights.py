"""What a directory of weights must be before Lab will load it.

Users may name any model, so whether loading is safe cannot rest on which
model it is. It rests on two things that can be checked on the files:

- **The architecture is one `transformers` implements itself.** Anything
  else is loaded by running Python that came with the checkpoint, which is
  what `trust_remote_code` exists to allow and Lab never allows.
- **The weights are safetensors.** A pickle checkpoint (`.bin`, `.pt`,
  `.ckpt`) can run code as it is unpickled. Their mere presence refuses the
  directory, rather than relying on the loader to prefer the safe file:
  whoever put the directory there should know there is something to remove.
  A fetched model never has one, because the fetch downloads none.

These run before any weights are read, so a refusal is immediate and says
what to change.
"""

from __future__ import annotations

import json
from pathlib import Path

from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES

#: Formats that are, or may be, Python pickles.
PICKLE_SUFFIXES = frozenset({".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt"})

LAYOUT_HINT = "A model directory holds config.json, the tokenizer files, and *.safetensors weights."


class WeightsRefused(RuntimeError):
    """A model directory cannot be loaded safely, or at all."""


def check_config(config_path: Path) -> str:
    """Refuse a config that needs remote code; return the architecture's name.

    Separate from `check_weights` so a fetch can run it on `config.json`
    alone, and refuse a repository before downloading gigabytes of it.
    """
    try:
        config = json.loads(config_path.read_text())
    except FileNotFoundError:
        raise WeightsRefused(f"{config_path.name} is missing. {LAYOUT_HINT}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WeightsRefused(f"{config_path.name} is not valid JSON: {error}") from None
    if not isinstance(config, dict):
        raise WeightsRefused(f"{config_path.name} must be a JSON object.")

    if "auto_map" in config:
        raise WeightsRefused(
            f"{config_path.name} has an auto_map, which means the model is loaded by code "
            "shipped with the checkpoint. Lab never runs remote code; use a model whose "
            "architecture transformers implements itself."
        )
    architecture = config.get("model_type")
    if architecture not in CONFIG_MAPPING_NAMES:
        raise WeightsRefused(
            f"{config_path.name} names model_type {architecture!r}, which this version of "
            "transformers does not implement. Lab only loads architectures it does, because "
            "the alternative is running the checkpoint's own Python."
        )
    return architecture


def check_weights(directory: Path) -> str:
    """Refuse anything unsafe or incomplete; return the architecture's name."""
    if not directory.is_dir():
        raise WeightsRefused(f"No model weights at {directory.name}. {LAYOUT_HINT}")
    architecture = check_config(directory / "config.json")

    pickles = sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.suffix.lower() in PICKLE_SUFFIXES
    )
    if pickles:
        raise WeightsRefused(
            f"{directory.name} holds pickle files ({', '.join(pickles)}), which can run code when "
            "loaded. Remove them; Lab loads safetensors only."
        )
    if not any(directory.glob("*.safetensors")):
        raise WeightsRefused(f"{directory.name} has no *.safetensors weights. {LAYOUT_HINT}")
    return architecture
