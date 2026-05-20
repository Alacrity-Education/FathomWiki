#!/usr/bin/env python3
import hashlib
import imaplib
import email
import email.policy
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
SENDER_DOMAIN = os.environ.get("SENDER_DOMAIN", "fathom.video")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "0"))
MARK_AS_READ = os.environ.get("MARK_AS_READ", "true").lower() == "true"
GIT_REPO_URL = "git@github.com:Alacrity-Education/Wiki.git"
GIT_AUTHOR_EMAIL = os.environ.get("GIT_AUTHOR_EMAIL", "nobody@alacrity.ro")
GIT_AUTHOR_NAME = GIT_AUTHOR_EMAIL.split("@")[0]


class HTMLTextExtractor(HTMLParser):
    """Extract readable text from HTML, preserving basic structure."""

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip = False
        self._block_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "li", "tr", "br"}

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip = True
        if tag in self._block_tags:
            self._pieces.append("\n")
        if tag == "li":
            self._pieces.append("- ")
        if tag == "br":
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = False
        if tag in self._block_tags:
            self._pieces.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._pieces.append(data)

    def get_text(self):
        text = "".join(self._pieces)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_notes_section(html_body: str) -> str:
    """Pull the ai_notes_html_content div from the Fathom email, or fall back to full body."""
    match = re.search(
        r'<div\s+class=["\']ai_notes_html_content["\']>(.*)',
        html_body,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        content = match.group(1)
        depth = 1
        pos = 0
        while depth > 0 and pos < len(content):
            open_m = re.search(r"<div[\s>]", content[pos:])
            close_m = re.search(r"</div>", content[pos:])
            if close_m is None:
                break
            if open_m and open_m.start() < close_m.start():
                depth += 1
                pos += open_m.end()
            else:
                depth -= 1
                if depth == 0:
                    content = content[:pos + close_m.start()]
                    break
                pos += close_m.end()
        return content

    return html_body


def html_to_text(html: str) -> str:
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def get_email_subject(msg: email.message.EmailMessage) -> str:
    subject = msg.get("Subject", "untitled")
    decoded_parts = email.header.decode_header(subject)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def meeting_hash(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:6]


def extract_meeting_title(subject: str) -> str:
    """Strip Fathom's 'Recap for \"...\"' wrapper to get the bare meeting title."""
    m = re.match(r'^Recap\s+for\s+"(.+)"$', subject, re.IGNORECASE)
    return m.group(1) if m else subject


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text).strip("-")
    return text[:80]


def git_run(*args, cwd=None):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=cwd or OUTPUT_DIR,
        capture_output=True,
        text=True,
    )
    return result


def git_sync_repo():
    """Ensure OUTPUT_DIR is a clean, up-to-date clone of GIT_REPO_URL."""
    git_dir = OUTPUT_DIR / ".git"

    if git_dir.is_dir():
        print(f"  Git repo exists at {OUTPUT_DIR}, pulling...")
        git_run("config", "user.email", GIT_AUTHOR_EMAIL)
        git_run("config", "user.name", GIT_AUTHOR_NAME)
        result = git_run("pull", "--rebase")
        if result.returncode == 0:
            print("  Pull succeeded.")
            return
        print(f"  Pull failed or conflict detected:\n    {result.stderr.strip()}")
        print("  Nuking repo and recloning...")
        shutil.rmtree(OUTPUT_DIR)

    print(f"  Cloning {GIT_REPO_URL} into {OUTPUT_DIR}...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", GIT_REPO_URL, str(OUTPUT_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Clone failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    git_run("config", "user.email", GIT_AUTHOR_EMAIL)
    git_run("config", "user.name", GIT_AUTHOR_NAME)
    print("  Clone succeeded.")


def git_commit_and_push(filename: str):
    print(f"    Git: adding {filename}")
    git_run("add", filename)
    result = git_run(
        "commit", "-m", f"New meeting note generated by Fathom: {filename}",
    )
    if result.returncode != 0:
        print(f"    Git commit failed: {result.stderr.strip()}", file=sys.stderr)
        return
    print("    Git: pushing...")
    result = git_run("push")
    if result.returncode != 0:
        print(f"    Git push failed: {result.stderr.strip()}", file=sys.stderr)
        return
    print("    Git: pushed successfully.")


def transform_with_claude(text: str, subject: str) -> str:
    prompt = f"""Transform the following meeting notes into clean, well-structured Markdown.

Rules:
- Use a top-level # heading with the meeting title
- Use ## for major sections (Key Takeaways, Topics, Action Items, etc.)
- Use bullet points for lists
- Use **bold** for speaker names or emphasis
- Clean up any artifacts from HTML conversion
- Preserve all factual content — do not summarize or omit details
- If there are action items, format them as a checklist with - [ ]
- Add a metadata block at the top with date and attendees if available

Meeting title: {subject}

--- BEGIN NOTES ---
{text}
--- END NOTES ---"""

    result = subprocess.run(
        ["claude", "-p", "--output-format", "text", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"  claude -p failed (exit {result.returncode}): {result.stderr}", file=sys.stderr)
        return text

    return result.stdout.strip()


def process_inbox():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Connecting to {IMAP_HOST}...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")

    search_criteria = f'(FROM "@{SENDER_DOMAIN}")'
    print(f"  Searching: {search_criteria}", flush=True)
    status, data = mail.search(None, search_criteria)

    if status != "OK":
        print(f"  Search failed: {status}", file=sys.stderr, flush=True)
        mail.logout()
        return

    msg_ids = data[0].split()
    if not msg_ids:
        print("  No Fathom emails found.", flush=True)
        mail.logout()
        return

    print(f"  Found {len(msg_ids)} email(s).", flush=True)
    git_sync_repo()

    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            print(f"  Failed to fetch message {msg_id}", file=sys.stderr)
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email, policy=email.policy.default)
        subject = get_email_subject(msg)
        sender = msg.get("From", "")
        date_str = msg.get("Date", "")
        print(f"\n  Processing: {subject}")
        print(f"    From: {sender}")
        print(f"    Date: {date_str}")

        html_body = None
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    html_body = part.get_content()
                    break
        elif msg.get_content_type() == "text/html":
            html_body = msg.get_content()

        if not html_body:
            print("    No HTML body found, skipping.", file=sys.stderr)
            continue

        notes_html = extract_notes_section(html_body)
        notes_text = html_to_text(notes_html)

        if len(notes_text.strip()) < 20:
            print("    Extracted notes too short, skipping.", file=sys.stderr)
            continue

        title = extract_meeting_title(subject)
        mid = meeting_hash(subject)

        existing = list(OUTPUT_DIR.glob(f"*-{mid}.md"))
        if existing:
            print(f"    Already processed (MeetingID {mid}): {existing[0].name}, skipping.")
            if MARK_AS_READ:
                mail.store(msg_id, "+FLAGS", "\\Seen")
            continue

        print(f"    Extracted {len(notes_text)} chars of meeting notes.")
        print("    Running claude -p to convert to markdown...")

        markdown = transform_with_claude(notes_text, title)

        footer = (
            "\n\n---\n\n"
            "> Summary generated by Fathom and inserted into the Wiki by FathomWiki with Claude.\n"
            f"> MeetingID: `{mid}`\n"
            "{.is-info}\n"
        )
        markdown += footer

        slug = slugify(title)
        filename = f"{slug}-{mid}.md"
        out_path = OUTPUT_DIR / filename

        out_path.write_text(markdown, encoding="utf-8")
        print(f"    Saved: {out_path}")

        git_commit_and_push(filename)

        if MARK_AS_READ:
            mail.store(msg_id, "+FLAGS", "\\Seen")

    mail.logout()
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Done.")


def main():
    if POLL_INTERVAL > 0:
        print(f"Running in poll mode (every {POLL_INTERVAL}s)...")
        while True:
            try:
                process_inbox()
            except Exception as e:
                print(f"Error during processing: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)
    else:
        process_inbox()


if __name__ == "__main__":
    main()
