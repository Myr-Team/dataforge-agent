from backend.customer_text import sanitize_customer_text


def test_sanitize_customer_text_removes_leading_quote_separator_after_markdown_bullet() -> None:
    raw = "- ”，第一段\n* ”，第二段\n1. ”，第三段"

    assert sanitize_customer_text(raw) == "- 第一段\n* 第二段\n1. 第三段"
