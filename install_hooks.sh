#!/bin/bash
# Point git at the hooks kept in this repo. Run once per clone.
cd "$(dirname "$0")" || exit 1
chmod +x hooks/pre-commit hooks/pre-push check_private_data.py
git config core.hooksPath hooks
echo "Hooks active: $(git config core.hooksPath)/  (pre-commit, pre-push)"
