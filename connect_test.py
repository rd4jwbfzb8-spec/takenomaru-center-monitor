
name: Takenomaru Center Monitor

on:
  schedule:
    - cron: "0,30 * * * *"
  workflow_dispatch:

concurrency:
  group: takenomaru-monitor
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 45

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Restore previous state
        uses: actions/cache@v4
        with:
          path: state.json
          key: takenomaru-state-${{ github.run_id }}-${{ github.run_attempt }}
          restore-keys: |
            takenomaru-state-

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install playwright requests
          playwright install --with-deps chromium

      - name: Run monitor
        run: python connect_test.py

      - name: Add monitor report to summary
        if: always()
        run: |
          if [ -f artifacts/monitor_report.json ]; then
            echo '## Takenomaru Monitor Report' >> "$GITHUB_STEP_SUMMARY"
            python - <<'PY'
          import json
          from pathlib import Path
          p = Path("artifacts/monitor_report.json")
          data = json.loads(p.read_text(encoding="utf-8"))
          print("```json")
          print(json.dumps(data, ensure_ascii=False, indent=2))
          print("```")
          PY
          fi

      - name: Upload diagnostic artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: takenomaru-monitor-report
          path: artifacts/
          if-no-files-found: ignore
          retention-days: 7
