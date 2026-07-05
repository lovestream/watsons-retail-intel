# QwenPaw Project Agent Rules

## Project Scope

This project is an instant retail / local life / beauty and personal care intelligence pipeline.

Primary goal:
- Collect, filter, extract, score, analyze, review, and generate daily industry reports.
- Avoid wasting tokens on noisy raw article dumps, full logs, huge JSON, irrelevant RSS output, or repeated full-pipeline runs.

## Sensitive Data Rules

- Do not print API keys, `.env`, credentials, private RSS secrets, local cookies, or raw proprietary data.
- Tavily and other API keys must stay in environment variables, not project files.
- Do not dump full raw article pools unless explicitly required.

## Command Output / Context Protection

1. Prefer targeted inspection:
   - `rg "keyword" config/ scripts/ src/`
   - `sed -n '1,160p' file`
   - `python -m json.tool file.json | head -c 4000`
   - `jq 'keys' file.json`
   - `jq '.items | length' file.json`
   - `tail -n 120 data/logs/YYYY-MM-DD/*.log`

2. Prefer RTK if available:
   - `rtk git status`
   - `rtk git diff`
   - `rtk pytest`
   - `rtk python -m pytest`
   - `rtk grep`
   - `rtk find`
   - `rtk ls`

3. Any command with unknown or potentially large output must be capped:

```bash
COMMAND 2>&1 | head -c 4000
```

4. Preserve exit status when correctness matters:

```bash
tmp=$(mktemp)
COMMAND >"$tmp" 2>&1
status=$?
head -c 4000 "$tmp"
echo
echo "[exit=$status]"
rm -f "$tmp"
exit $status
```

5. For JSON/log inspection:
   - Never print entire raw_articles.json, events_raw.json, events_scored.json, events_analyzed.json, or long log files by default.
   - First inspect counts, keys, sample IDs, error summaries, and top-level structure.
   - Use jq slices, not full dumps.

6. For pipeline testing:
   - Prefer step-level tests before end-to-end tests.
   - Do not repeatedly run the full daily pipeline after every small change.
   - When debugging LLM truncation or fallback logic, inspect only the relevant date directory and relevant step output.

7. If output is truncated:
   - Rerun a narrower command.
   - Search for errors with `rg -n "ERROR|Exception|Traceback|failed|timeout|reject|fallback"`.
   - Do not simply increase the cap unless the missing part is known to be needed.
