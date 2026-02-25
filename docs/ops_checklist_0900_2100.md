# Ops Checklist (09:00 / 21:00)

## Purpose
- strict70_to0_rule_v1 の検証運用を、毎日同じ順序で実行する
- 判定・文言・証跡の不一致を防ぐ

## 09:00 JST (Morning Check)
1. リポジトリ状態確認（差分/ブランチ）
2. データ更新状態の確認（当日分の入力が揃っているか）
3. DRY_RUN 実行（判定・文言・証跡の出力確認）
4. 監査チェック（PASS/FAIL の確認）
5. 公開訴求との整合確認（30%訴求が止まっていること）
6. 結果記録（必要なら docs/project_state_log.md に1行）

## 21:00 JST (Pre-Run / Evening Check)
1. リポジトリ状態確認（差分なし前提）
2. 実行前ガード確認（DRY_RUN/LIVEの取り違え防止）
3. precheck 実行（run_id / audit / sync terminal 確認）
4. 監査ログ終端確認（STARTだけで終わっていないこと）
5. 異常時の分岐
   - FAIL: 実行停止、原因を1行記録
   - PASS: 次の定時処理へ進行
6. 結果記録（必要なら docs/project_state_log.md に1行）

## Hard Rules
- ロジック変更と運用作業を同じターンで混ぜない
- 公開repoには存在ファイル限定差分のみ反映
- 30%訴求は保留（strict70_to0_rule_v1 検証完了まで）
- 監査FAIL時は進めない

## Done Criteria
- 朝/夜ともに「実行順」が固定され、毎回この順番で回せる状態
