# BTCSIGNAL Security Fixed Rules v1

最終更新: 2026-03-06

## 目的
BTCSIGNAL 運用において、秘密情報の漏洩・AI経由の誤操作・権限過大・ログ露出を防ぐ。  
再発防止は「気をつける」ではなく「できない構造」にする。

---

## 0. 適用範囲
このルールは以下に適用する。

- BTCSIGNAL 関連 repo
- tradep-test
- GitHub Actions
- Discord / X / Substack / Google / Cloudflare 等の投稿・配信・運用資格情報
- Bitget 等の資産系資格情報
- AI支援開発（Codex / Claude Code / その他エージェント / MCP）

---

## 1. 最優先原則

### 1-1. repo 直下の .env* を禁止する
- repo 直下に `.env`, `.env.*`, `.env.live` を置かない
- 正本は `secrets/` 配下のみに置く
- 例外運用は禁止

### 1-2. AI に見せる範囲を repo 単位に制限する
- `/Users/Claw` 全体を AI に見せない
- 必要な repo / 作業ディレクトリのみを可視範囲にする
- 本番 secrets 保存場所は AI 可視範囲から外す

### 1-3. shell で平文 secret を入力しない
- `export KEY=...` を禁止
- ターミナルへの平文貼り付けを禁止
- 読み込みはファイル経由または secure storage 経由のみ

### 1-4. 鍵は役割ごとに分離する
以下を同じ資格情報群で兼用しない。

- read-only
- trade
- post
- infra

### 1-5. 本番鍵と開発鍵を分離する
- 同居禁止
- 使い回し禁止
- 開発用の鍵を本番で流用しない
- 本番用の鍵を開発環境に置かない

### 1-6. 監査 PASS を完了条件にする
変更後は必ず監査を実施し、PASS が出るまで完了扱いにしない。

---

## 2. 資格情報の分類

### A. 資産系
例:
- BITGET_API_KEY
- BITGET_API_SECRET
- BITGET_PASSPHRASE

ルール:
- 最優先保護対象
- withdraw 権限は禁止
- read-only と trade を分離
- ログ、履歴、会話、画面共有への露出禁止
- 漏洩疑い時は即 revoke / reissue

### B. 投稿系
例:
- DISCORD_WEBHOOK_URL
- X 関連 token
- Substack 関連 token
- Telegram token

ルール:
- 資産系と分離
- 用途別に分離
- 不要 webhook / token は削除
- 本番用とテスト用を分離

### C. インフラ系
例:
- GitHub PAT
- Cloudflare
- ドメイン管理
- Vercel / Railway / Render など

ルール:
- repo/workflow/infra を最小権限化
- 資産系と同居禁止
- 投稿系とも可能な限り分離

### D. 読み取り専用
例:
- market data read-only
- analytics read-only

ルール:
- 書込権限を持たせない
- 本番鍵の代替に使う

---

## 3. 配置ルール

### 3-1. 許可される配置
- `secrets/` 配下
- AI 可視範囲外の専用 secrets 置き場
- secure storage / keychain 相当

### 3-2. 禁止される配置
- repo 直下 `.env*`
- ホーム直下の雑置き
- デスクトップや downloads
- チャット本文
- issue / PR / commit message
- shell history に残る形

### 3-3. .gitignore
以下は必ず ignore 対象にする。

- `.env`
- `.env.*`
- `secrets/`
- `*.pem`
- `id_rsa`
- `id_ed25519`

---

## 4. AI 運用ルール

### 4-1. AI に許可する範囲
- 対象 repo のコード編集
- 対象 repo 内のテスト
- 読み取り専用の確認作業

### 4-2. AI に許可しない範囲
- ホーム全体の探索
- secrets 保存場所の閲覧
- 本番鍵の読み取り
- 広告・決済・資産系の直接操作
- 不明な MCP / 外部ツールの追加実行

### 4-3. AI に渡す前提
- `.env*` は repo 直下に存在しないこと
- 本番鍵は AI 可視範囲外にあること
- 実行権限は最小であること

---

## 5. GitHub / Actions ルール

### 5-1. branch protection
- PR 必須
- force push 禁止
- 直 push 禁止
- required status checks 必須

### 5-2. Secrets
- 生きているものだけ残す
- 命名を用途別に明確化する
- 同じ用途でも test / prod を分離する
- 不明な secret は削除または再発行

### 5-3. ログ
- token, webhook, key を出力しない
- デバッグで secret 値を echo しない
- 漏洩疑い時は log を証拠保全後に失効対応

---

## 6. Bitget 固定ルール

### 6-1. 権限
- withdraw: OFF 固定
- read-only と trade を分離
- 本番 trade key は必要時のみ使用

### 6-2. 漏洩疑いの定義
以下のいずれかがあれば漏洩疑いとして扱う。

- shell history に痕跡
- AI 可視範囲で参照可能
- チャット / ログ / スクショに露出
- repo 直下 `.env*` に保存
- 不明なツール / MCP からアクセス可能

### 6-3. 漏洩疑い時の対応
- 旧 key revoke
- 新 key reissue
- 実行経路の停止
- ログ確認
- 再発防止ルール更新

---

## 7. Discord / X / Substack 固定ルール

### 7-1. Discord
- webhook は用途別に分離
- 成功している webhook は理由なく差し替えない
- test と production のチャンネルを分離
- 204 成功確認を採用基準にする

### 7-2. X
- 投稿 token は資産系と分離
- 自動投稿ジョブは enabled / disabled を定期確認
- 失敗通知経路を別に持つ

### 7-3. Substack
- daily sync と weekly publish を分離
- publish 権限トークンは用途限定
- URL 取得や公開手順は fail-closed

---

## 8. 監査チェックリスト
変更後は毎回、最低限これを確認する。

### 配置確認
- repo 直下に `.env*` がない
- secrets は許可された場所だけにある

### 権限確認
- Bitget withdraw OFF
- read-only / trade 分離
- GitHub token 最小権限
- 投稿 / インフラ / 資産が分離されている

### ログ確認
- shell history に平文なし
- GitHub Actions log に secret 痕跡なし
- AI 会話 / ログに秘密情報なし

### 実行範囲確認
- AI 可視範囲が repo 単位
- 不要な MCP / 外部連携なし

### 判定
- PASS が出るまで完了扱いにしない

---

## 9. インシデント即時対応手順
漏洩疑い時は以下の順番で実施する。

1. 資産系 key revoke
2. 投稿系 / infra 系 token rotate
3. webhook rotate
4. GitHub Actions 停止
5. 露出ログ確認
6. 実行範囲を縮小
7. 再発防止ルールに反映

---

## 10. 現時点の既知NG
2026-03-06 時点で確認された事項。

- `/Users/Claw/tradep-test/.env.live` が repo 直下に存在していた
- `/Users/Claw/.zsh_history` に Bitget API キー平文痕跡があった
- AI 可視範囲が `/Users/Claw` 配下まで広かった

これらは是正対象とする。

---

## 11. 今後の運用原則
- 「一時的にここへ置く」を禁止する
- 「あとで直す」を禁止する
- 動作優先で secrets 分離を後回しにしない
- セキュリティ例外を常態化しない
- 再発防止は構造で行う

---

## 12. 完了条件
以下を満たした時だけ、安全側へ移行したと判定する。

- repo 直下 `.env*` がゼロ
- Bitget key ローテーション完了
- AI 可視範囲が repo 単位
- shell 平文運用停止
- Actions / logs / secrets spot check PASS
- 本文書に反する運用が残っていない
