import os

from webapp.utils.i18n import _get_current_lang_code


def tail_file(path: str, n: int = 20, max_chars: int = 6000) -> str:
    """Liest die letzten N Zeilen einer Datei, ohne die komplette Datei einzulesen."""
    try:
        if not os.path.exists(path):
            return ""

        # Wir lesen von hinten in BlÃ¶cken, bis wir genug Zeilen haben.
        block_size = 4096
        data = b""
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()

            while pos > 0 and data.count(b"\n") <= (n + 2) and len(data) < (max_chars * 6):
                step = min(block_size, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data

        text = data.decode("utf-8", errors="replace")
        out = "\n".join(text.splitlines()[-n:])
        if len(out) > max_chars:
            out = out[-max_chars:]
        return out.strip()
    except Exception:
        return ""


def _inline_notice_page(title: str, body_html: str, status: int = 200, html_lang: str | None = None) -> tuple[str, int]:
    lang = html_lang or _get_current_lang_code()
    html = (
        f"<!doctype html><html lang='{lang}'>"
        "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title></head>"
        "<body style='font-family:system-ui;background:#111;color:#eee;padding:20px;'>"
        f"<h1>{title}</h1>"
        f"{body_html}"
        "</body></html>"
    )
    return html, status
