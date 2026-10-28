def words(prefix: str, count: int) -> str:
    """`count` distinct, ordered words: prefix000, prefix001, ..."""
    return " ".join(f"{prefix}{i:03d}" for i in range(count))
