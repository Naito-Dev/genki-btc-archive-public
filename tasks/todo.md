# BTCSIGNAL Todo Log

## 2026-02-28
- Plan Mode運用をBTCSIGNAL標準手順として固定。
- 非自明タスクは Goal/Scope/Risks/Checklist/Verification/Rollback を先出しする方針を適用。
- 監査証跡は tasks/todo.md と tasks/lessons.md に必ず追記してから完了判定する。
- missed-run-check の EXPECTED_DATE_UTC 参照を安全化し、KeyError停止を防止。
- missed-run-check の Discord 通知は失敗時（403含む）に WARN 出力のみでジョブ失敗扱いにしない挙動へ修正。
- 変更は daily.yml の監査サブジョブに限定し、本体の日次更新ロジックは未変更。
- Daily Archive Update を 12:05 UTC 起動 + 12:12 UTC publish gate に変更し、定刻公開の前提を固定。
- run-daily に publish SLO ログ（PUBLISHED_AT_UTC / PUBLISH_DELAY_SEC / PUBLISH_SLO_STATUS）を追加。
- concurrency を cancel-in-progress=false に変更し、sleep中の重複実行競合を抑止。
