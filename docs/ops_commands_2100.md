# Ops Commands 21:00 JST (Precheck Copy-Paste)

## Purpose
- 21:00 precheck を毎回同じ順序で実行する
- run_id / audit / sync terminal / 監査ログ終端を確認して誤実行を防ぐ

## Step 0: Repo / Branch / Status
```bash
cd /Users/Claw/tradep-test
pwd
git branch --show-current
git status --short
```

## Step 1: Execution Guard Check (No mix-up)
```bash
cd /Users/Claw/tradep-test
printf "ARMED=%s\nDRY_RUN=%s\nTRADING_ENABLED=%s\n" "${ARMED:-}" "${DRY_RUN:-}" "${TRADING_ENABLED:-}"
```

## Step 2: Precheck Execution (Expected: safe verification path)
```bash
cd /Users/Claw/tradep-test
.venv/bin/python scripts/stamp_2100_precheck.py
```

## Step 3: Minimum Result Check (run_id / audit / sync terminal)
```bash
cd /Users/Claw/tradep-test
ls -lt output | head
grep -RIn '"run_id"\|"checks.audit.pass"\|"sync_status"' output 2>/dev/null | tail -n 20
```

## Step 4: Audit Log Terminal Check (START-only禁止)
```bash
cd /Users/Claw/tradep-test
LOG="output/execution_sync_audit_$(date -u +%Y-%m).ndjson"
echo "$LOG"
tail -n 20 "$LOG" 2>/dev/null || true
```

## PASS/FAIL Rule
- PASS:
  - run_id が出ている
  - checks.audit.pass=true
  - sync_status が terminal（STARTだけで終わっていない）
- FAIL:
  - 実行停止
  - 原因を1行で docs/project_state_log.md に記録

## Optional Log (Only if needed)
```bash
cd /Users/Claw/tradep-test
printf '%s\n' '- 21:00 precheck: PASS/FAIL (reason)' >> docs/project_state_log.md
```

## Hard Rules
- 21:00はロジック変更しない
- FAIL時にその場で修正コミットしない（切り分け先行）
- precheck確認と公開repo操作を混ぜない
