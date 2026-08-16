# EDINET Lakehouse Manual

EDINETと複数ストレージからDatabricksへデータを取り込み、Bronze / Silver / Gold / Genie Oneへ進める流れを図解した静的サイトです。

## ローカル確認

```bash
npm ci
npm run dev
```

`http://localhost:3000` を開きます。

## ビルド

```bash
npm run build
GITHUB_ACTIONS=true npm run build:pages
```

後者はGitHub Pages用の `/edinet-test` ベースパスを付けて `out/` に静的ファイルを出力します。初回だけGitHubの `Settings → Pages → Build and deployment → Source` で **GitHub Actions** を選択します。その後は `main` ブランチへのpushを契機に `.github/workflows/pages.yml` が公開します。
