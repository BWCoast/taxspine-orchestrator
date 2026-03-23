# Hook: Pre-Push Checklist

**Trigger:** Before executing any `git push` command (including `git push origin main`,
`git push --force`, `git push -u origin <branch>`, or any variant).

---

## What to do

Before running `git push`, Claude MUST:

1. **Run the automated checks:**
   ```bash
   bash .githooks/pre-push
   ```
   If the script exits non-zero, stop and report the failures. Do not push.

2. **Report the result** to the user with a short summary:
   - What passed
   - What (if anything) failed or needs manual verification
   - Whether it is safe to push

3. **Reference `docs/CI_BUILD_GOTCHAS.md`** if any check needs explanation.

---

## Manual checks (not automated)

After the script passes, remind the user to verify manually:

- **GH_READ_TOKEN:** Is it still valid and does it have `repo` scope?
  ```bash
  curl -sf -H "Authorization: Bearer $GH_READ_TOKEN" \
    "https://api.github.com/repos/BWCoast/tax-nor/commits/main" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('sha','ERROR')[:12])"
  ```

- **Local-only test failures:** Are the 2 `TestPriceTableMissingPath` failures the only
  ones? (They're expected locally. Any other failures must be investigated.)

---

## Do not bypass this hook

Even if the user says "just push it quickly" — run the checks first.
The Docker build takes ~10 minutes. Catching a lockfile or diverged-config issue
here saves the entire build cycle.

See: `docs/CI_BUILD_GOTCHAS.md` for full context.
