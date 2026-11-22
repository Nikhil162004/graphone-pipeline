from src.llm_engine import dense_chunk, estimate_tokens, split_into_chunks

TEXT = "\n\n".join(f"Paragraph {i} about pricing and plans and billing." for i in range(60))


def test_chunks_never_exceed_budget():
    chunks = split_into_chunks(TEXT, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)


def test_dense_chunk_keeps_head_and_budget():
    chunk = dense_chunk(TEXT, 800, keywords=("pricing",))
    assert len(chunk) <= 800
    assert chunk.startswith("Paragraph 0")


def test_long_paragraph_hard_split():
    chunks = split_into_chunks("x" * 5000, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert sum(len(c) for c in chunks) == 5000


def test_token_estimate():
    assert estimate_tokens("abcd" * 100) == 100
