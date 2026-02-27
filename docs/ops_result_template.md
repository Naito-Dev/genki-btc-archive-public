# Ops Result Template (09:00 / 21:00)

## Usage
- 毎日の運用結果をこの形式で記録する
- 人間/AIどちらでも同じフォーマットを使う

---

## Record
- 実行時刻:
- 対象: 09:00 / 21:00
- 判定: PASS / FAIL
- run_id: (取れた時のみ)
- audit.pass:
- sync_status: (21:00のみ)
- 理由（FAIL時必須）:

## Optional
- 関連コミット:
- メモ:


### HOLD当日限定：Bitget自動実行チェックリスト（固定）

前提（毎回守る）
- 通常日は BITGET_EXECUTE_ENABLED=0（自動売買OFF）
- HOLDが出た日だけ、当日限定で1回実行
- MAX_NOTIONAL_USD=10 固定
- 条件未達・例外時は SAFE_STOP（発注なし）
- 実行後は必ず BITGET_EXECUTE_ENABLED=0 に戻す

1) 事前確認（HOLD日、実行前）
cd /Users/Claw/genki-btc-archive-public
gh variable list | grep -E "BITGET_EXECUTE_ENABLED|BITGET_EXEC_DRY_RUN|MAX_NOTIONAL_USD|ALLOW_LIVE"

2) 当日だけON（HOLD日だけ）
gh variable set BITGET_EXECUTE_ENABLED --body "1"
gh variable set BITGET_EXEC_DRY_RUN --body "0"
gh variable set ALLOW_LIVE --body "YES"
gh variable set MAX_NOTIONAL_USD --body "10"

3) workflow_dispatch を1回だけ実行
gh workflow run "Daily Archive Update" --ref main
RUN_ID=$(gh run list --workflow "Daily Archive Update" --branch main --limit 1 --json databaseId -q '.[0].databaseId')
echo "RUN_ID=$RUN_ID"
gh run watch "$RUN_ID" --exit-status
gh run view "$RUN_ID" --log | grep -E "Execute Bitget from BTCSIGNAL|SAFE_STOP|EXECUTED|SKIPPED|ALREADY_RAN_TODAY|No changes to commit"

判定
- EXECUTED → 発注あり（約定ログ確認へ）
- SAFE_STOP / SKIPPED → 発注なし（正常停止）
- ALREADY_RAN_TODAY → 同日2回目扱い。今日は終了

4) 実行後は必ずOFFに戻す（同日中）
gh variable set BITGET_EXECUTE_ENABLED --body "0"
gh variable set BITGET_EXEC_DRY_RUN --body "1"
gh variable set ALLOW_LIVE --body "NO"

5) 最終確認（OFFに戻ったか）
gh variable list | grep -E "BITGET_EXECUTE_ENABLED|BITGET_EXEC_DRY_RUN|ALLOW_LIVE|MAX_NOTIONAL_USD"

期待値
- BITGET_EXECUTE_ENABLED=0
- BITGET_EXEC_DRY_RUN=1
- ALLOW_LIVE=NO
- MAX_NOTIONAL_USD=10
