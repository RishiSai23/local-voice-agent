"""
backend/sanitizer.py
--------------------
Reusable, conservative Markdown-to-plain-text sanitizer for assistant responses.
Cleans Markdown formatting (bold, italic, code blocks, inline code, links, headers)
so text is readable in the conversation feed and cleanly pronounceable by Piper TTS.
Preserves:
- Actual text words, punctuation, line breaks, and numbers.
- Mathematical operators with spaces (e.g., '5 * 4 = 20').
- Identifiers with underscores (e.g., 'snake_case_name').
"""

import re


def clean_markdown(text: str) -> str:
    """
    Conservatively strip Markdown formatting syntax from *text* while preserving
    actual words, punctuation, line breaks, and non-formatting symbols.
    """
    if not text:
        return ""

    # 1. Normalize carriage returns
    result = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Fenced code blocks: ```lang ... ``` or ``` ... ```
    # Replace with the inner code contents
    result = re.sub(
        r"```[a-zA-Z0-9_\-\.]*[\s\n]*([\s\S]*?)[\s\n]*```",
        r"\1",
        result,
    )

    # 3. Inline code: `code`
    result = re.sub(r"`([^`\n]+)`", r"\1", result)

    # 4. Images: ![alt](url) -> alt
    result = re.sub(r"!\[([^\]]*?)\]\([^)]*?\)", r"\1", result)

    # 5. Links: [link text](url) -> link text
    result = re.sub(r"\[([^\]]+?)\]\([^)]*?\)", r"\1", result)

    # 6. Headers: # Heading -> Heading
    result = re.sub(r"(?m)^#{1,6}\s+", "", result)

    # 7. Blockquotes: > Quote -> Quote
    result = re.sub(r"(?m)^>\s*", "", result)

    # 8. Horizontal rules: --- or *** or ___ on their own line
    result = re.sub(r"(?m)^(?:\s*[-*_]\s*){3,}\s*$", "", result)

    # 9. Strikethrough: ~~text~~ -> text
    result = re.sub(r"~~([^~\n]+?)~~", r"\1", result)

    # 10. Bold and italic formatting
    # Triple asterisks/underscores: ***bold italic*** or ___bold italic___
    result = re.sub(r"\*\*\*([^\*\n]+?)\*\*\*", r"\1", result)
    result = re.sub(r"(?<!\w)___([^_\n]+?)___(?!\w)", r"\1", result)

    # Double asterisks/underscores: **bold** or __bold__
    result = re.sub(r"\*\*([^\*\n]+?)\*\*", r"\1", result)
    result = re.sub(r"(?<!\w)__([^_\n]+?)__(?!\w)", r"\1", result)

    # Single asterisk/underscore: *italic* or _italic_
    # Conservative matching:
    # - Delimiting asterisk must not have whitespace right inside the delimiter.
    # - This protects math expressions like '5 * 4 = 20' where asterisks have spaces around them.
    result = re.sub(
        r"(?<!\*)\*(?!\s|\*)([^\*\n]+?)(?<!\s|\*)\*(?!\*)",
        r"\1",
        result,
    )
    result = re.sub(
        r"(?<!\w)_(?!\s|_)([^_\n]+?)(?<!\s|_)_(?!\w)",
        r"\1",
        result,
    )

    # 11. Bullet point markers at line starts:
    # Convert leading list asterisks (* item) to clean hyphens (- item)
    result = re.sub(r"(?m)^\s*[\*]\s+", "- ", result)

    # 12. Normalize excessive consecutive newlines (more than 2 -> 2)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()
