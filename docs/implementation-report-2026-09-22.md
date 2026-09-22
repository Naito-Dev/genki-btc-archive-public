# 2026-09-22 記録専用システムへの切替報告

**実装・静的公開・単発本番記録は完了。Discordは未復旧。自動更新は停止中です。**

## 1. 停止・撤去した処理

実施済み: 旧 GitHub Actions 6本を先に無効化し、書込み中・待機中の対象実行がないことを確認してからコードを撤去しました。対象は Daily Archive Update、Daily Publish Watchdog、DevLog Weekly、Private Signal Build、Substack Status Monitor、Substack Weekly Ops Draft です。最終API確認では6本とも deleted です。

Macの日次起動と5分間隔の起動窓ジョブ2件は、無効化・unload後にplistを退避しました。OpenClawのBTC SIGNAL専用9ジョブも無効化し、最終確認でrunning状態なしです。WAITを作っていた前日記録欠落時のX原稿生成経路もこの停止対象です。OpenClaw全体、共有Bot、他プロジェクトは停止していません。旧Substack autohealの未完了PR #545・#546・#547も閉じ、履歴とブランチを残しました。

公開側の旧Workflow6本・旧scripts23本・メール登録ページ2本・不要な運用手順を撤去しました。非公開側は売買・転送・旧Workflow・運用画面を撤去し、研究コード3本を研究専用ディレクトリへ分離しています。GitHubの不要な当該リポジトリSecret登録4件を削除し、残る登録Secretは `DISCORD_WEBHOOK_URL` のみです。外部の共有キー自体の失効とは区別します。

## 2. 原本保全

実施済み・検証済み: 公開側270ファイル（元の場所261、退役した公開/投稿状態9）と、非公開側のCSV・採用/検証資料等8ファイルは、切替前に独立保存したSHA-256と一致しています。9ファイルは `archive/retired-integrations/` へbyteを変えず移しました。過去記録の再計算・判定の置換・不一致の統一はしていません。

Git bundle、作業ファイル、未コミット差分、旧ローカルコピー、実行時の証跡、一時ディレクトリにあった調査資料を、公開対象外の継続保存領域へ保全しました。対象一覧6762項目のSHA-256 manifestがあります。Git履歴の書換え・force pushはありません。

過去のCASHの一部は「ルールが正常計算した結果」ではなく「計算不能時の代入値が当時保存されていた記録」です。日別209件のMINIMAL_MODE、詳細ログ212件の計算不能経路、バックフィル、CSV入力鮮度未確認、生成根拠未確認を区別します。詳細は[過去ログの注意点](history-notes.md)。

## 3. 新ルールと開始記録

採用: `SMA100_CLOSE_V1`。Model Dの採用・修正commitと実行経路を調べましたが、当時の価格系列の取得元・日足の確定条件を忠実に復元できませんでした。利用者が承認した代替仕様を適用し、新系列として開始しました。ルールの混合や性能目的の閾値調整はありません。

Coinbase Exchange BTC-USDの確定UTC日足を使用し、対象日込み100本の終値平均より対象日終値が上ならBTC、以下ならCASHです。[固定仕様](rule-spec.md)。

初回の対象日は **2026-09-21 UTC**、生成日時は **2026-09-22 13:49:29.707399 UTC / 22:49:29.707399 JST**、結果は **BTC**。過去日のテストデータではなく、本番実行時の直近確定日足です。

- 終値: 86594.94 USD
- SMA100: 68506.0938 USD
- 結果ID: `SMA100_CLOSE_V1-2026-09-21`
- 結果SHA-256: `1be6f72cb352d3db8c13c9ab2f8d38576f06a16d0063ad534beb271ac9861be3`
- [実際の公開JSON](https://www.btcsignal.org/records/SMA100_CLOSE_V1/2026-09-21.json)

保存入力からの独立したオフライン検算でも、判定・合計・SMAが一致しました。この検算は既存結果を変更せず、通常の表示・通知の処理では行いません。

## 4. 新構成

公開リポジトリに、価格取得→検証→計算→固定結果保存→PRによるGit保存→静的Pages→Discordを集約しました。非公開側は研究アーカイブです。公開範囲は変更していません。

残す外部サービスはCoinbase公開市場API、GitHub/Actions/Pages、Discord、既存ドメイン/DNSです。新しい有料サービス・有料AI・有料ランナーはありません。

[実装PR #548](https://github.com/Naito-Dev/genki-btc-archive-public/pull/548)を通常mergeしました。非公開側は元のGitHubリポジトリが空でPRの比較元がなかったため、履歴を維持した整理commit `c517011` を既存の非公開リポジトリのmainへ通常pushしました。Workflowはありません。

## 5. テストと実公開・通知

テスト済み: オフライン45件成功。SMA境界、入力不足/欠落/重複/古い足/不正値、UTC/JST境界、同日再実行、実2プロセス同時実行、immutable保存、原本改変拒否、公開失敗時の記録保全、通知前intent保存、404、timeout、再送防止、公開データ制限を検証しました。

実確認済み: [本番run 35736037904](https://github.com/Naito-Dev/genki-btc-archive-public/actions/runs/35736037904)は本番の `workflow_dispatch` と `GITHUB_TOKEN` を使いました。保存PR [#549](https://github.com/Naito-Dev/genki-btc-archive-public/pull/549)、通知intent [#550](https://github.com/Naito-Dev/genki-btc-archive-public/pull/550)、通知結果 [#551](https://github.com/Naito-Dev/genki-btc-archive-public/pull/551)を通常mergeし、Pages build APIによる公開を確認しました。公開JSONは保存原本とbyte単位で一致します。

**Discord通知はHTTP 404で失敗しました。** message IDは取得できず、送信成功とは扱っていません。通知は1回のみ、確定結果の削除・再計算なし。失敗receiptをGitとWebへ公開し、Workflow全体もfailureで終了しました。これは保存・公開の失敗とは異なります。[公開通知状態](https://www.btcsignal.org/delivery/SMA100_CLOSE_V1-2026-09-21.json)。

[実サイト](https://www.btcsignal.org/)は「自動更新停止中・単発検証」、対象日、新ルール、Discord送信失敗を表示しています。デスクトップ/モバイル表示、過去履歴の区分も確認しました。実行するscript/form/iframeはなく、CSP `connect-src 'none'`、読込資産は同一サイトのCSSだけです。

## 6. 費用と停止制御

確認済み: 標準 `ubuntu-latest` の公開リポジトリ実行はGitHubの無料対象です。独自Workflowはartifact/cacheを使用しません。既存ドメインの更新料は別の固定費で、変更していません。[公式料金](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。

保存容量も確認しました。切替前の対象リポジトリAPIではcacheは0件/0byte、artifactは30件（29件期限切れ）でした。GitHub管理のPages内部ビルドが使うartifactと、Gitに保存する長期記録の容量は別です。容量や請求が無制限に無料であるとは断定しません。

**未確認: アカウントの請求・予算・超過時停止設定。** 請求画面は未ログインで確認できず、共通設定は変更していません。予算通知だけでなく、該当するActions/storage予算の `Stop usage when budget limit is reached` を所有者が確認する必要があります。[公式の予算設定](https://docs.github.com/en/billing/how-tos/set-up-budgets)。

## 7. サービス側と未完了事項

実施済み: 旧フォームの送信先と完全一致するGASの `mail`（version 4）デプロイを、Googleの管理画面でアーカイブしました。完了表示を確認し、プロジェクトのトリガーは0件でした。送信テストでメールアドレスや顧客行を作成していません。顧客データ・スプレッドシートは削除していません。

未確認のため未実施: 同じGASプロジェクトの別の有効デプロイ2件は、この用途との関係を確定できず、そのままです。Bitget/X/Substackの実行経路とプロジェクト専用起動は撤去/停止しましたが、サービスアカウント全体の削除や共有キーの失効はしていません。

未復旧: Discord。所有者は確認用チャンネルの有効なWebhookを確認し、[リポジトリSecrets設定](https://github.com/Naito-Dev/genki-btc-archive-public/settings/secrets/actions)の `DISCORD_WEBHOOK_URL` を更新してください。値をチャットへ貼る必要はありません。その後、同じ保存済みファイルを指定した `notify_only` で復旧できます。

運用上の逸脱: 最初の保守画面pushは、GitHubが管理者権限によるPR必須ルールの迂回として受け付けました。明示的な管理者迂回オプションは指定していませんが、結果としてルール迂回になったため記録します。以後は通常のPR経由に切り替え、本番記録も通常PR mergeで保存しました。履歴を隠すための書換えはしていません。

## 8. 現在の運転状態

**自動更新は停止中です。** 新Workflowは手動実行のみで、cron/push/外部dispatch/自動復旧の起動条件はありません。旧6 Workflowは削除状態、Mac2件とOpenClaw9件は停止を再確認しました。

再開前に必要なのは、課金・超過時停止制御の確認です。Discordだけ未復旧なら記録系と分けて扱えます。確認後に新Workflow1本だけの00:20 UTC（09:20 JST）scheduleを通常PRで追加します。今の状態では日々の新記録は自動追加されません。
