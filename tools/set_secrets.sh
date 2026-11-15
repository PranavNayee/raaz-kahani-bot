#!/usr/bin/env bash
# Saves the bot's keys as GitHub Actions secrets WITHOUT showing them on screen or in shell history.
# Run in your own terminal from the repo folder:   bash tools/set_secrets.sh            (asks for every secret)
#                                                  bash tools/set_secrets.sh GEMINI_API_KEY GROQ_API_KEY   (only these)
# Press Enter on an empty prompt to skip a secret (keeps any value already saved).
set -euo pipefail
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
ALL=(GEMINI_API_KEY GROQ_API_KEY SUPABASE_URL SUPABASE_SERVICE_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
     IG_USER_ID IG_ACCESS_TOKEN POLLINATIONS_TOKEN)
NAMES=("$@"); [ ${#NAMES[@]} -eq 0 ] && NAMES=("${ALL[@]}")
echo "Saving secrets to $REPO (input is hidden; Enter = skip)"
for n in "${NAMES[@]}"; do
  read -r -s -p "  $n: " v; echo
  if [ -n "$v" ]; then
    printf '%s' "$v" | gh secret set "$n" -R "$REPO" && echo "    ✓ saved"
  else
    echo "    - skipped"
  fi
  unset v
done
echo "YouTube secrets are saved by: python tools/youtube_auth.py client_secret_XXXX.json"
gh secret list -R "$REPO"
