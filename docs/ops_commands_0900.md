# Ops Commands 09:00 JST (Copy-Paste)

## Purpose
- 朝9時チェックを毎回同じ順序で実行する
- strict70_to0_rule_v1 の判定・文言・証跡の不一致を早期検出する

## Step 0: Repo / Branch / Status
```bash
cd /Users/Claw/tradep-test
pwd
git branch --show-current
git status --short
```

## Step 1: Input/Data Presence Check
```bash
cd /Users/Claw/tradep-test
ls -lh data/Binance_BTCUSDT_D1.csv data/Binance_BTCUSDT_15m.csv
```

## Step 2: DRY_RUN Execution (Morning Verification)
```bash
cd /Users/Claw/tradep-test
DRY_RUN=1 .venv/bin/python scripts/run_daily.py
```

## Step 3: Audit / Output Check (Minimum)
```bash
cd /Users/Claw/tradep-test
git status --short
tail -n 5 output/trades_live.csv 2>/dev/null || true
ls -lt output | head
```

## Step 4: PASS/FAIL Rule
- PASS: 判定・文言・証跡が矛盾なし → 次工程へ
- FAIL: 進めない。原因を1行で `docs/project_state_log.md` に記録

## Step 5: Optional Log (Only if needed)
```bash
cd /Users/Claw/tradep-test
printf '%s\n' '- 09:00 check: PASS/FAIL (reason)' >> docs/project_state_log.md
```

## Hard Rules
- 朝チェックでロジック変更しない
- FAIL時にその場で修正コミットしない（切り分けを先にする）
- 公開repo操作を混ぜない
