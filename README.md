# NewsHub Pages Digest

静的HTMLの「朝刊サイト」を生成し、**GitHub Pages（Project Pages）**で公開するための最小パッケージです。

- **投資ニュース**（JPX公式RSS / EDINET）を収集して、スマホで見やすく整形
- **経済以外ニュース**も Topic Pack を追加するだけで拡張可能
- 公開サイトは安全側（A:一般情報のみ）で運用
- **B'（資産クラス比率のみ）**はスイッチで表示可能（デフォルトOFF）

---

## 0. 重要（公開運用の注意）

- GitHub Pages は **機密情報のやりとり用途に向きません**。
- パスワード、口座番号、総資産額、個別保有金額などは **公開しない**でください。
- 本ツールは「リンク＋要約」中心です。ニュース本文の転載は避けてください。

---

## 1. これは何？（GitHub Pages / GitHub Actions）

- **GitHub Pages**: GitHub上のリポジトリから静的Webサイト（HTML/CSS/JS）をホスティングする機能
- **GitHub Actions**: GitHub上で定期実行（cron）やビルド/デプロイを行うCI/CD機能

このリポジトリは GitHub Actions が毎朝ニュースを取得し、HTMLを生成し、`gh-pages` ブランチへデプロイします。
あなたのPCを常時稼働させる必要はありません。

---

## 2. セットアップ（Project Pages）

### 2.1 リポジトリ作成（Public）

1. GitHubで新規リポジトリを作成（Public）
2. このパッケージ一式を展開し、リポジトリ直下に配置
3. commit & push

### 2.2 EDINET APIキーを Secrets に登録（任意）

EDINET API v2 を使うには APIキーが必要です（キーは公開しない）。

1. GitHubのリポジトリ → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**
3. Name: `EDINET_API_KEY`
4. Value: あなたのEDINET APIキー

> EDINETを使わない場合は未設定でも動きます（その場合、EDINETはスキップされます）。

### 2.3 GitHub Pages を有効化

1. リポジトリ → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `gh-pages` / root
4. 保存

公開URLは通常：

- `https://<ユーザー名>.github.io/<リポジトリ名>/`

### 2.4 初回デプロイ（手動実行）

1. リポジトリ → **Actions**
2. `Publish NewsHub` → **Run workflow**
3. 数分後、Pages URL を開いて確認

---

## 3. 運用

- 毎朝 06:10 JST に自動更新（GitHub Actions schedule）
- すぐに更新したい時は Actions の `Run workflow` を押す

---

## 4. 公開ポリシー（A / B'）

- **A（推奨）**: 公開サイトは一般情報のみ
- **B'（オプション）**: 資産クラス比率だけを表示（5%刻み）

B'を使うには `news_hub/config/public.yaml` を編集：

```yaml
public_site:
  show_asset_mix: true
  asset_mix_rounding: 0.05
  asset_mix:
    equity: 0.70
    bond: 0.15
    reit: 0.15
    cash: 0.00
```

---

## 5. 拡張（経済以外のニュース）

`news_hub/config/news.yaml` の `world_general` を `enabled: true` にし、RSS URL を差し替えてください。

---

## 6. ファイル構成

- `news_hub/config/news.yaml` : ニュースソース / Topic Pack
- `news_hub/config/llm.yaml` : 手動/無料APIフォールバック設定（MVPではスタブ）
- `news_hub/config/public.yaml` : 公開情報の安全スイッチ
- `news_hub/scripts/build_site.py` : ニュース取得→HTML生成
- `news_hub/outputs/site/` : 生成される静的サイト
- `.github/workflows/publish-news.yml` : 毎朝ビルド＆デプロイ

---

## 7. トラブルシューティング

### 7.1 EDINETが取得できない
- `EDINET_API_KEY` が Secrets に入っているか確認
- 未設定でもRSSだけでサイト生成は可能

### 7.2 schedule が動かない/遅れる
- schedule はベストエフォートで遅延することがあります。必要なら手動 `Run workflow` を使ってください。
- 公開リポジトリで60日無活動の場合、自動で無効化されることがあります。`settings.html` の最終更新時刻を確認してください。

### 7.3 Pagesが表示されない
- Settings → Pages が `gh-pages` を見ているか確認
- Actionsのログで `Deploy to gh-pages` が成功しているか確認

