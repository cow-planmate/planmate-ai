"""텍스트 정규화 — HTML 태그 스트립 + 공백 정리.

tourdata 자유텍스트(`overview`, `opentimefood` 등)에 `<br>`, `<a>` 태그가 섞여 있음.
임베딩 입력 조립 · (나중에) 상세 표시에서 공용.
"""

import re

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = _BR.sub(" ", s)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()
