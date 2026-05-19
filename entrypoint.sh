#!/bin/bash
set -euo pipefail

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ERROR: ANTHROPIC_API_KEY is required" >&2
    exit 1
fi

if [ -z "${EMAIL_USER:-}" ] || [ -z "${EMAIL_PASS:-}" ]; then
    echo "ERROR: EMAIL_USER and EMAIL_PASS are required" >&2
    exit 1
fi

if [ -z "${GIT_SSH_KEY:-}" ]; then
    echo "ERROR: GIT_SSH_KEY is required" >&2
    exit 1
fi

# Write the SSH private key from env and lock down permissions
mkdir -p ~/.ssh
echo "$GIT_SSH_KEY" > ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519
ssh-keyscan -t ed25519,rsa github.com >> ~/.ssh/known_hosts 2>/dev/null

echo "=== FathomWiki Email Processor ==="
echo "Email:  ${EMAIL_USER}"
echo "IMAP:   ${IMAP_HOST:-imap.gmail.com}:${IMAP_PORT:-993}"
echo "Domain: ${SENDER_DOMAIN:-fathom.video}"
echo "Output: ${OUTPUT_DIR:-/output}"
echo "Repo:   git@github.com:Alacrity-Education/Wiki.git"
echo "Poll:   ${POLL_INTERVAL:-0}s (0 = single run)"
echo "SSH:    key loaded"
echo "=================================="

exec python3 /app/process_fathom.py
