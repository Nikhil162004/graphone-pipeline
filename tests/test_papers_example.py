"""The worked-example connector (Papers With Code page 98456).

The repo picker is a pure function, so the real judgement call on the
real page — five GitHub links, exactly one of them the paper's own
repository — is locked down here without any network access.
"""
from src.sources.papers import _choose_official_repo, _title_tokens

PAGE_LINKS = [  # the actual repo links found on paper/98456
    "thudm/slime",
    "zai-org/GLM-5",
    "huggingface/transformers",
    "vllm-project/vllm",
    "sgl-project/sglang",
]
TITLE = "GLM-5.2: Built for Long-Horizon Tasks"


def test_chooses_the_papers_own_repo():
    assert _choose_official_repo(TITLE, PAGE_LINKS) == "zai-org/GLM-5"


def test_no_links_means_no_repo_not_an_invented_one():
    assert _choose_official_repo(TITLE, []) == ""


def test_tokens_keep_model_versions():
    assert {"glm", "5.2"} <= _title_tokens(TITLE)
