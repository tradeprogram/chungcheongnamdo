"""기획서가 3페이지에 들어가는지 A4 레이아웃으로 실제 측정한다.

공모전 양식은 "기타첨부 자료를 포함하여 절대 3페이지를 넘지 말아야" 한다고 못박는다.
글자수로 어림하면 표와 그림이 빠져 항상 낙관적으로 나온다. 그래서 본문을
한글 기본 여백(상하 20mm, 좌우 20mm)의 A4 에 휴먼명조 10pt 로 앉힌 HTML 을 만들고
브라우저에서 높이를 재는 쪽을 택했다.

    A4 = 210 x 297mm, 96dpi 기준 794 x 1123px
    한글 기본 여백을 빼면 본문 폭 170mm(643px), 높이 257mm(971px)

실행
    python notebooks/check_page_count.py
    -> web/_pagecheck.html 생성. 브라우저에서 높이를 재면 쪽수가 나온다.
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "docs" / "80_proposal_draft.md"
FIG = REPO_ROOT / "docs" / "90_submission" / "figure1_two_observations.png"
OUT = REPO_ROOT / "web" / "_pagecheck.html"

# 제출 대상 절만 센다. "제출 전 확인", "쓰지 않기로 한 것" 은 내부 메모다.
SUBMIT = ["아이디어 요약서", "① 아이디어 명칭", "② 분석 목적", "③ 핵심 내용",
          "④ 분석 방법", "⑤ 필요 데이터", "⑥ 활용방안(계획)"]

CSS = """
body { margin:0; background:#555; font-family:'HY중고딕','휴먼명조','바탕',Batang,serif; }
.page-body { width:643px; margin:0 auto; background:#fff; padding:0;
             font-size:10pt; line-height:1.5; color:#000; }
h2 { font-size:11pt; margin:10px 0 5px; font-weight:bold; }
p { margin:0 0 5px; text-align:justify; }
table { border-collapse:collapse; width:100%; margin:5px 0; font-size:9pt; }
td, th { border:1px solid #333; padding:2px 4px; }
img { width:100%; margin:5px 0; }
blockquote { margin:5px 0; padding-left:8px; border-left:2px solid #999; }
.ruler { position:fixed; top:0; left:0; }
"""


def md_to_html(md: str) -> str:
    out, in_table = [], False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        stripped = line.strip().lstrip("> ").strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if not stripped:
            continue
        if "figure1" in stripped:
            # file:// 은 http 문서에서 차단된다. 이미지가 안 실리면 그림 높이만큼
            # 쪽수가 낙관적으로 나오므로 같은 서버에 복사해 두고 상대경로로 건다.
            out.append('<img src="_fig1.png">')
            continue
        if stripped.startswith("---"):
            continue
        out.append(f"<p>{stripped}</p>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    blocks, keep, cur = [], False, []
    for line in text.splitlines():
        if line.startswith("## "):
            if keep:
                blocks.append("\n".join(cur))
            title = line[3:].strip()
            keep = title in SUBMIT
            cur = [line] if keep else []
            continue
        if keep:
            cur.append(line)
    if keep:
        blocks.append("\n".join(cur))

    body = md_to_html("\n".join(blocks))
    shutil.copy(FIG, OUT.parent / "_fig1.png")
    OUT.write_text(
        f"<meta charset='utf-8'><style>{CSS}</style>"
        f"<div class='page-body' id='body'>{body}</div>",
        encoding="utf-8")
    print(f"-> {OUT}")
    print("브라우저에서 #body 높이를 재고 971px 로 나누면 쪽수다.")


if __name__ == "__main__":
    main()
