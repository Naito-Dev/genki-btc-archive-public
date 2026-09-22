# BTC SIGNAL — 日次判定の長期ログ

決めたルールで BTC の確定日足を評価し、その結果と入力を保存する記録専用システムです。実売買・注文・口座残高の取得は行いません。`BTC` / `CASH` は計算上のラベルであり、実際の保有状態ではありません。

**日次自動更新を1本だけ再開しました。** 2026-09-22に保存・静的公開・Discord通知・課金停止制御を確認し、利用者の承認により毎日00:20 UTC（09:20 JST）の定期実行を有効にしました。再開設定後に手動計算は行わず、最初の定期実行を待ちます。

[切替の実施結果・45件のテスト・本番確認](docs/implementation-report-2026-09-22.md): 初回対象日は2026-09-21 UTC、結果はBTC。当初のDiscord HTTP 404は通知専用run [35740146310](https://github.com/Naito-Dev/genki-btc-archive-public/actions/runs/35740146310)で復旧し、再計算なしの実投稿とmessage IDを確認しました。切替報告は当時の状態の原記録として残します。

サイト: [www.btcsignal.org](https://www.btcsignal.org/)。[旧履歴の注意点](docs/history-notes.md)、[新ルール仕様](docs/rule-spec.md)を参照してください。

## 日次処理

```text
Coinbase Exchange 公開日足 API（BTC-USD）
  → 連続する確定 UTC 日足100本を検証
  → SMA100_CLOSE_V1 を計算
  → 入力・確定結果を変更不能で保存
  → 通常の PR / merge で Git に保存
  → 静的 HTML / JSON を GitHub Pages に公開・一致確認
  → 通知予定を先に Git に保存
  → 同じ確定結果を Discord に一度送信
  → 通知の成功・失敗を別ファイルに保存
```

Web は HTML / CSS / 保存済み JSON だけです。ブラウザ用 JavaScript、フォーム、計算 API、外部フォント、解析タグはありません。閲覧によって価格取得・判定計算・Actions・通知が起動しません。

### 対象日とルール

現在の唯一のルールは `SMA100_CLOSE_V1` です。対象日を含む連続100日分の終値の単純平均より、対象日の終値が厳密に上なら `BTC`、同じか下なら `CASH`。判定に表示用の丸め値は使いません。

実行開始時点で直近の確定済み UTC 日足を対象とします。実行予定は **00:20 UTC / 09:20 JST に1日1回**（`20 0 * * *`）です。UTC日足確定から20分の余裕を設けます。例えば9月22日09:20 JSTに実行すると、対象は **9月21日 UTC**です。実際の取得・生成・公開時刻を別々に記録します。再開後の初回予定は2026-09-23 09:20 JST（00:20 UTC）で、2026-09-22 UTC日足を対象にします。GitHub側の混雑等で開始が遅れる可能性があります。対象日は実際の処理開始時点のUTC前日を使い、欠けた過去日を自動で埋め直しません。

Model D の数式は残っていましたが、当時の価格系列には確定日足と実行時の価格取得が混在しており、入力元・日付・確定時刻まで忠実に復元できませんでした。承認された代替仕様として新ルールを採用し、旧記録とは別系列にします。採用日は2026-09-22、最初の判定対象日は実際に保存された `records/` の最初のファイルが根拠です。過去日を指定して正常ログを埋める本番 CLI はありません。

## 構成

```text
src/btc_signal/       検証・計算・変更不能保存・Discord通知
scripts/             記録 / 静的生成 / 保護検証 / 一方向の運用処理
records/<rule>/      確定結果（対象日ごとに一度だけ）
inputs/<rule>/       計算に使った100本の入力
status/              更新成功・判定不能のイベント
publication/         公開日時と公開した commit
delivery/           通知予定と送信結果（確定判定とは別管理）
site/                静的画面のスタイルと保守画面
docs/               Pages の公開用生成物・仕様・保存した過去資料
config/              運転モードと旧原本の SHA-256 一覧
tests/               ネット接続しないテスト
.github/workflows/   record.yml のみ
data/, logs/, archive/, verification/, proof/, output/
                     旧原本・検証資料（変更せず保管）
```

`_site/` はビルド専用で Git 管理外です。`scripts/stage_site.py` が生成物の許可リストだけを `docs/` へコピーします。Pages の公開元は `main:/docs`。旧原本の口座・残高等の項目は新しい表示データへ渡しません。旧原本は既存の Git 上にそのまま残ります。私有資料や秘密情報を新しく公開する構成ではありません。

## Actions と必要な権限

現行の独自 Workflow は **Record and publish BTC signal** (`record.yml`) 1本です。起動条件は毎日00:20 UTCの `schedule` 1件と、復旧用の `workflow_dispatch` だけです。push、repository_dispatch、workflow_runは使いません。両イベントともmainに限定し、scheduleは必ずrecordモードで直近確定日を処理します。通知だけの手動再送では計算用テストも起動しません。GitHub 管理の Pages build / deployment は静的公開のために残ります。旧6 Workflowは停止後に撤去しました。MacやOpenClawからの重複起動・監視・復旧は使いません。

標準の `ubuntu-latest`、最大15分、同時実行1件、Python標準ライブラリのみです。市場GETは各15秒、最大3回。Pagesの待機も上限があり、失敗しても自己起動しません。Actions artifact と cache は使用しません。

Workflow 全体は `permissions: {}`、実行 job だけ `contents: write`（結果保存）、`pull-requests: write`（既存のPR必須保護を守る）、`pages: write`（Pages更新）を使用します。保存は通常のPR・mergeのみで、管理者迂回・保護設定変更・自動承認は行いません。以前の保存PRが未完了なら新計算を止め、保存済み結果の解決を優先します。

`GITHUB_TOKEN` による更新では暗黙のPages更新が起きないため、保存後に Pages build API を明示的に呼び、対象commitの公開成功と配信JSONのbyte一致を確認します。APIの不許可や保護ルール変更は正常終了扱いにしません。

## 外部サービスと Secrets

| サービス | 用途・認証 |
|---|---|
| Coinbase Exchange | BTC-USDの公開日足GETのみ。APIキー・口座・入金は不要 |
| GitHub / Actions / Pages | Gitの原本保存・標準ランナー・静的公開。実行時の `GITHUB_TOKEN` |
| Discord | 確認用Webhookへの定型通知。Secret名 `DISCORD_WEBHOOK_URL` |
| 既存ドメイン / DNS | `www.btcsignal.org`。新契約や固定費変更なし |

必要な登録Secretは `DISCORD_WEBHOOK_URL` 1個です。値はフロントエンド・ファイル・ログに出しません。Bitget、GAS、メール、Substack、X、LLMは現行コードに接続しません。削除したSecretの名前や、サービス側の停止状態は切替報告で区別します。共有キーそのものは推測で失効させません。

Discordは同じ結果ID・SHA-256・対象日・判定・根拠数値を定型文で送ります。LLMを使用しません。`wait=true` の応答にmessage IDがある場合だけ送信済みにします。404は失敗、timeoutや応答不明は `uncertain` です。通知失敗で確定結果を消したり再計算したりしません。送信前のintentをGitへ保存してから送るため、runnerが消えた場合も別runからの無条件再送を止めます。

## 失敗時の確認と手動復旧

1. [Actions](https://github.com/Naito-Dev/genki-btc-archive-public/actions/workflows/record.yml) の失敗箇所・機械的なerror codeを確認します。
2. `status/latest.json` と `status/events/` は計算・入力の状態、`publication/` は公開状態、`delivery/` は通知状態です。通常の判定と混同しません。
3. 保存PRが残っていれば、そのPRの結果・入力を先に確認し、通常のreview/mergeで解決します。原本を捨てて再計算しません。保護設定を弱めません。
4. 公開失敗は Actions の **Run workflow → publish_only** で、保存済み `records/SMA100_CLOSE_V1/YYYY-MM-DD.json` を指定します。価格は再取得しません。
5. 404等の確定した通知失敗はWebhookの宛先・Secretを確認後、**notify_only** と同じ保存済みパスで再送します。公開済みファイルの一致確認を先に行います。
6. `pending` / `uncertain` は送信済みの可能性があります。Discord側で対象の記録IDを確認するまで再送しません。確認結果を運用資料に記録し、deliveryだけを通常のPRで処置します。確定結果には触れません。

入力の欠落・重複・古さ・不正値・未確定足・日付をまたぐ取得・計算例外では、新しい正常判定を作りません。失敗状態を保存して公開し、Workflowは失敗で終了します。前回の結果を表示する場合は対象日と更新失敗を併記します。古いCASHを現在の正常結果として繰り上げません。

ローカル検証（Python 3.11以上）:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/verify_history.py
python3 scripts/build_site.py --root . --output _site
```

これらのコマンドは取引・投稿・価格取得を行いません。本番の `operate.py` は指定したGitHubのmain上のschedule/手動実行コンテキスト以外を拒否します。`record_daily.py` 単体は実データGETと保存を行うため、テスト代わりに実行しないでください。

## 過去ログの意味

旧原本は削除・統一・上書きせず、そのまま凍結しています。`config/legacy-sha256.json` に基準commit `f60194683fbf7c92ba50e6ddcd519aa38883c807` の270ファイルのhashを記録し、実行前と保存前に照合します。整理前の両リポジトリ、未コミット変更、旧ローカルコピー、調査資料も別の非公開保全領域に保存しました。

過去の一部CASHは、ルールが正常に下落を判定した証拠ではなく、**計算不能時に代入された値がその日に保存されていた証拠**です。バックフィル・CSVの入力鮮度未確認・計算失敗時の代入・生成根拠未確認を別ラベルにし、正常性を一括認定しません。同じ日の集約・日別・別系列の不一致も残します。[詳しい区分](docs/history-notes.md)を参照してください。

撤去したSubstack/Xの最終送信状態9ファイルは、byteを変えず `archive/retired-integrations/` に移して保全しました。現行処理から参照しません。

過去の手順書や研究資料に残る古いサービス・キー名は当時の記録です。現在の運用手順として実行しません。非公開側はオフライン研究アーカイブに限定し、ここから呼び出しません。

## 費用と再開条件

新しい有料サービス、AI API、有料ランナー、課金増額は導入しません。公開リポジトリの標準ホストランナーはGitHubの無料対象です。既存ドメインの更新料は別の固定費です。[Actions料金](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

このWorkflowはartifact/cacheをアップロードしません。Gitで入力100本・結果・状態・静的ページを保存するため、Git容量は増加します。GitHubの既存のPages内部処理・ログ保持やアカウント全体の利用量までゼロと断定しません。大型ランナー・private実行・有料storageへ自動的に切り替える処理はありません。

**再開に必要なActions予算の上限到達時停止設定は、所有者の管理画面で確認済みです。** 既存の予算は変更していません。予算の詳細をこの公開資料に転載せず、確認記録を非公開の保全領域へ保存します。予算通知だけでは停止にならず、既存固定費や設定前の利用まで免除されるわけではありません。[予算と停止設定](https://docs.github.com/en/billing/how-tos/set-up-budgets)

原本保護・判定・本番の保存/公開・Discord投稿・課金停止制御を確認後、新Workflowだけのscheduleを追加しました。旧Workflow・Mac・OpenClawは再開しません。監視・自動復旧・失敗後の自己起動はありません。障害時はActionsの失敗と保存されたstatus/deliveryを確認し、必要な手動復旧だけを行います。
