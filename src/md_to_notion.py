"""통제된 마크다운 부분집합 -> Notion 블록 변환기.

처리 대상(렌더러가 내보내는 전부): H1/H2/H3, 단락, **bold**, `인라인코드`,
```코드블록```(언어태그), 불릿(-), 구분선(---), 인용(>).
임의 마크다운 파싱은 하지 않는다.
"""
from __future__ import annotations
import re

NOTION_TEXT_LIMIT = 2000

# Notion code 블록 language enum 일부 + 별칭 매핑. 미지정/미지원 -> plain text
_LANG_MAP = {
    "": "plain text", "text": "plain text", "txt": "plain text",
    "sql": "sql", "java": "java", "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "bash": "bash", "sh": "shell", "shell": "shell",
    "json": "json", "yaml": "yaml", "yml": "yaml",
    "html": "html", "css": "css", "c": "c", "cpp": "c++", "c++": "c++",
    "go": "go", "kotlin": "kotlin", "kt": "kotlin", "rust": "rust",
    "xml": "xml", "diff": "diff", "markdown": "markdown",
}


def _map_language(lang: str) -> str:
    return _LANG_MAP.get(lang.strip().lower(), "plain text")


def _chunk(text: str, limit: int = NOTION_TEXT_LIMIT) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


# 인라인: **bold** 와 `code` (비중첩, 렌더러 출력과 일치)
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def _rich_text(line: str) -> list[dict]:
    """한 줄을 rich_text 배열로. bold/inline code 주석 처리 + 2000자 분할."""
    out: list[dict] = []
    for tok in _INLINE_RE.split(line):
        if not tok:
            continue
        ann = {}
        content = tok
        if tok.startswith("**") and tok.endswith("**"):
            content = tok[2:-2]
            ann = {"bold": True}
        elif tok.startswith("`") and tok.endswith("`"):
            content = tok[1:-1]
            ann = {"code": True}
        for piece in _chunk(content):
            item = {"type": "text", "text": {"content": piece}}
            if ann:
                item["annotations"] = ann
            out.append(item)
    return out or [{"type": "text", "text": {"content": ""}}]


def _block(kind: str, line: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rich_text(line)}}


def to_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # 코드블록 ```lang ... ```
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            buf: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].rstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 닫는 ``` 소비
            code = "\n".join(buf)
            blocks.append({
                "object": "block", "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": c}} for c in _chunk(code)],
                    "language": _map_language(lang),
                },
            })
            continue

        if stripped == "":
            i += 1
            continue
        if stripped == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif stripped.startswith("### "):
            blocks.append(_block("heading_3", stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(_block("heading_2", stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(_block("heading_1", stripped[2:]))
        elif stripped.startswith("> "):
            blocks.append(_block("quote", stripped[2:]))
        elif stripped.startswith("- "):
            blocks.append(_block("bulleted_list_item", stripped[2:]))
        else:
            blocks.append(_block("paragraph", stripped))
        i += 1
    return blocks
