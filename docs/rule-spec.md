# SMA100_CLOSE_V1 判定仕様

採用日: 2026-09-22。これは旧ルールの復旧ではなく、新しい記録系列の開始です。最初の判定対象日と実際の生成日時は、`records/SMA100_CLOSE_V1/` に初めて確定保存された記録を正とします。過去ログへ新しい判定を埋め直しません。

## 採用理由

Model Dは2026-02-27に採用されています。今回、指定されたcommit `20957d63eefd6050a877beab7c0bc454d83a0713` と、同日の後続修正（最終 `7fb04b0ad4f33a6d5183c6842f0180d83c73f052`）、Workflow呼び出し、検証資料を照合しました。

数式のコードは残っていますが、Model Dが入力としていた `log.json.btc_price` は、取引所の確定日足に限定した値ではありません。過去のseedと日次処理時刻の価格snapshotが混在する系統であり、銘柄・取得元・確定時刻を一つの再現可能な入力契約として復元できませんでした。`DAILY_CUTOFF_JST=09:00` も足の確定を制御せず、表示timestampを整形する設定でした。EMA、PROBATION、週足、保有期間等の状態に過去系列が影響するため、別の確定日足を接続して同一ルールの復旧と呼ぶことはできません。

また、2026-03-07のcommit `d6dbf89283dd92a118b8a41425f54b7a65ea9fbf` でModel D実行はprivate結果取込へ変更されています。現存するMarketEdge参照や `private_current_signal` というラベルだけでは、その後の正式な判定仕様を確定できません。

このため、利用者が承認した「既知の資料で確実に復元できなければSMA100へ切り替える」という条件を適用しました。損益に合わせた閾値調整・旧ルールとの混合・失敗時のルール自動切替はありません。Model D等の旧コードと資料は履歴調査の対象として保存し、現行処理から呼びません。

## 固定したデータ契約

| 項目 | 固定値 |
|---|---|
| ルールID | `SMA100_CLOSE_V1` |
| 取得元 | Coinbase Exchange (`coinbase_exchange`) |
| 通貨ペア | `BTC-USD`、現物 |
| 公開API | `GET https://api.exchange.coinbase.com/products/BTC-USD/candles` |
| 日足長 | `granularity=86400` 秒 |
| 日の区切り | UTC 00:00:00以上、翌UTC 00:00:00未満 |
| 判定対象日 | 処理開始時点のUTC日付の前日 |
| 計算窓 | 対象日を含む連続した確定日足100本 |
| 終値 | Coinbaseの当該bucket最後の約定価格 |

取得範囲のstartは対象日の99日前00:00:00Z、endは対象日23:59:59Zです。APIが返すstart以前の追加bucketは計算窓から除外します。計算窓内の欠落・重複は補完しません。現在形成中の足や未来の足が返った場合も失敗とします。BTC/USDTや他の取引所・指数との置換・平均化は行いません。

APIの配列は `[time, low, high, open, close, volume]` として読取り、保存時は名前付きの100本だけに正規化します。timestampがUTC午前0時に一致すること、期待する100日が連続すること、価格が有限かつ正、volumeが有限かつ非負、安値・高値と始値・終値が整合することを検証します。取得中にUTC日付が変わった場合も失敗し、対象日を途中で取り替えません。

Coinbaseのmarket dataは公開APIで、今回の処理は認証・口座・有料API契約を使用しません。公式資料はbucket、終値、最大300本、取引がない区間にデータがないことを説明しています。[API概要](https://docs.cdp.coinbase.com/exchange/introduction/welcome)、[candles仕様](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)

HTTP timeoutは各15秒、取得は最大3回、待機は1秒・2秒です。429・5xx・通信失敗だけを限定再試行し、その他の4xxや不正JSONはその場で失敗します。1日1回の取得を想定し、Webサイトを見てもAPIは呼びません。[公式レート制限](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)

## 計算

100本の終値合計を100で割った値がSMA100です。対象日終値がSMA100を厳密に上回れば `BTC`、等しいか下回れば `CASH` と記録します。比較は10進Decimalで `100 × 対象日終値 > 終値合計` を用い、表示用の丸め値を比較に使いません。

これはルールの計算結果であり、実際の保有状態・発注指示・約定結果を表しません。計算不能をBTC・CASH・WAITへ変換しません。

例: 2026-09-22 09:20 JST（00:20 UTC）の実行は、2026-09-21 UTC日足を判定します。2026-09-22 08:59 JST（前日23:59 UTC）なら対象は2026-09-20です。生成日と対象日は別々に保存します。公開時刻も生成時刻とは別です。

## 確定結果と入力の保全

- 入力: `inputs/SMA100_CLOSE_V1/YYYY-MM-DD.json`。100本の公開OHLCV、source・product・対象日・実際の取得日時だけを保存します。
- 結果: `records/SMA100_CLOSE_V1/YYYY-MM-DD.json`。対象日、ルールID、判定、終値・合計・SMA、窓、実際の生成日時、入力参照・SHA-256、実装commitと実装ファイルSHA-256を保存します。
- 結果SHA-256: `result_sha256` を除いたJSONをキー昇順・空白なし・UTF-8・非ASCII保持で直列化したbyte列に対して計算します。改行は含めません。入力SHA-256は保存ファイルのbyte列（末尾改行を含む）を対象とします。
- 同一ルール・同一対象日は一度だけ確定します。fcntl lockで同時処理を直列化し、atomic hard-link作成により既存ファイルを上書きしません。
- 同日の再実行では保存済み結果と入力の整合性を検証し、その結果を返します。市場データを再取得せず、判定を再計算しません。保存途中の入力だけが残った場合は、検証に通るその入力から結果保存を完了します。
- 実装はcommitに加えファイルhashも記録するため、作業ツリーがcommitと異なる場合も計算に使ったコードの識別情報が残ります。

過去日を指定して正常記録を増やすCLIオプションはありません。テスト用過去データは一時ディレクトリのfixtureに限定し、本番に書きません。

独立した再現監査には `btc_signal.core.audit_record_calculation(root, path)` を使用できます。保存済み入力だけで再計算し、確定結果と比較する読み取り専用関数です。通常の再実行・公開・通知はこの関数を呼ばず、既存結果をそのまま使用します。

## 失敗、公開、通知

日次失敗は `status/events/` の固有実行IDと `status/latest.json` に `state=failed`、`label=判定不能`、機械的なerror codeを保存します。通常の判定結果は作成せず、前回の正常結果を上書きしません。例外の生文字列や秘密値はstatus・標準出力へ出しません。

公開後の状態は `delivery/<record_id>.json` に保存し、確定結果から分離します。通知はWebと同じ `presentation(record)` の項目を使い、結果IDとSHA-256を含む定型文を作ります。LLM・市場API・判定処理は呼びません。

Discord Webhookは環境変数 `DISCORD_WEBHOOK_URL` からのみ取得します。通知CLIは二段階です。`--prepare` でattempt IDとGitHub run ID／run attemptに結び付いたpending intentを保存し、WorkflowがそのファイルをGitへcommit・mergeしてから、同じ実行だけが `--send-attempt-id` で送信できます。異なるrunやrerun attemptから以前のpendingを再送できません。runnerが途中で消えても、remoteに残るpendingは次回の自動送信を止めます。

`wait=true` のPOSTを1回実行し、成功HTTP応答とmessage IDを得た場合だけ `sent` とします。404等は非0終了し、結果を消しません。timeout・応答不明は `uncertain` として再送を止めます。送信前に停止したpendingも含め、二重通知を避けるため人がDiscord側を確認してから状態を処置する必要があります。`sent` の再実行は送信を省略します。

課金・公開・停止設定の運用判断はREADMEとWorkflowに従います。市場データAPIを認証なしで使用することは、GitHubを含むアカウント全体の予算確認が済んだことを意味しません。
