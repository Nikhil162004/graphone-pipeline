import os

from src.resolver import EntityResolver

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed_entities.json")


def make():
    return EntityResolver(SEED)


def test_legal_suffix_and_punctuation():
    r = make()
    assert r.resolve("OpenAI, Inc.") == "OpenAI"
    assert r.resolve("Anthropic PBC") == "Anthropic"


def test_spaced_variant():
    r = make()
    assert r.resolve("Open AI") == "OpenAI"


def test_aliases():
    r = make()
    assert r.resolve("DeepMind") == "Google DeepMind"
    assert r.resolve("WandB") == "Weights & Biases"
    assert r.resolve("Cursor") == "Anysphere"


def test_squashed_exact():
    r = make()
    assert r.resolve("HuggingFace") == "Hugging Face"


def test_unresolved_passes_through_and_logs():
    r = make()
    assert r.resolve("Tiny Unknown Startup") == "Tiny Unknown Startup"
    assert r.log[-1]["method"] == "unresolved"


def test_mentions():
    r = make()
    found = r.find_mentions("OpenAI and Mistral AI both shipped updates, per Hugging Face.")
    assert "OpenAI" in found and "Mistral AI" in found
