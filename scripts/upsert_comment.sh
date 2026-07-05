#!/usr/bin/env bash
#
# upsert_comment.sh — post or update the single diff-sommelier review-menu
# comment on a pull request (backlog #5).
#
# It finds a prior comment authored by the running actor that carries the
# hidden marker (<!-- diff-sommelier:review-menu -->) and PATCHes it in place;
# if none exists it POSTs a new one. This keeps exactly one review-menu comment
# per PR, updated on every push, with no comment spam.
#
# Usage: upsert_comment.sh <owner/repo> <pr-number> <body-file>
#
# Requires: gh (authenticated via GH_TOKEN), jq.
set -euo pipefail

REPO="${1:?owner/repo required}"
PR_NUMBER="${2:?pr number required}"
BODY_FILE="${3:?body file required}"

MARKER="<!-- diff-sommelier:review-menu -->"

if [ ! -s "$BODY_FILE" ]; then
  echo "diff-sommelier: refusing to post an empty comment body ($BODY_FILE)." >&2
  exit 1
fi

# Find an existing menu comment by our hidden marker. Page through issue
# comments (a PR is an issue for the comments API). --paginate + -q flattens
# every page into one id-per-line stream; take the first match.
existing_id="$(
  gh api --paginate \
    "repos/${REPO}/issues/${PR_NUMBER}/comments" \
    -q ".[] | select(.body | contains(\"${MARKER}\")) | .id" \
    2>/dev/null | head -n1 || true
)"

if [ -n "${existing_id}" ]; then
  echo "Updating existing review-menu comment #${existing_id}."
  url="$(
    gh api --method PATCH \
      "repos/${REPO}/issues/comments/${existing_id}" \
      -F body=@"${BODY_FILE}" \
      -q .html_url
  )"
else
  echo "No existing review-menu comment; posting a new one."
  url="$(
    gh api --method POST \
      "repos/${REPO}/issues/${PR_NUMBER}/comments" \
      -F body=@"${BODY_FILE}" \
      -q .html_url
  )"
fi

echo "comment-url=${url}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "Review menu comment: ${url}"
