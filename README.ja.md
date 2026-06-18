# siturai — Vivliostyle 画像配置 PoC (Stage 1)

[English](README.md) ・ 日本語

VLM（ビジョン言語モデル）が、VFM/Markdown 原稿に対して **使える画像配置の判断**を
出せるかどうかを見極めるための最小実験です。OpenClaw などのエージェント機構は
まだ使いません。ここで「配置品質が実用になるか」を確かめてから、Stage 2 で
エージェント化します。

リポジトリ名 `siturai`（しつらひ）は、「場にふさわしく整え据える」という古語に
由来します。画像を文章の意味にふさわしい場所へ据える、というこのツールの働きに
合わせた名付けです。

## 何をするか

原稿（ブロックに分割）と画像フォルダを渡すと、画像ごとに VLM へ問い合わせ、

- どのブロックの後ろに置くか（**画像内容と本文の意味的マッチング**）
- 配置種別（inline / figure / figure-full-width / float）
- キャプション・alt・幅ヒント

を構造化 JSON で受け取り、VFM に変換して**原稿のコピー**に挿入し、差分を出します。
オプションで Vivliostyle CLI でビルドしてレンダーまで行います。

VLM には得意なこと（意味的マッチングと文言生成）だけをさせ、**レンダー結果の
体裁が良いかの判断はさせません**。そこは人が目で見ます。

## 設計上の約束

- **バックエンド非依存** — OpenAI 互換 `/v1`（ビジョン対応）のエンドポイントなら
  何でも。いまはローカル（Ollama）、あとで Spark の vLLM に環境変数を変えるだけで
  移行できます。
- **propose, don't commit** — ソース原稿は決して書き換えません。`output/` に
  修正版コピー・`proposals.json`・`changes.diff` を出すだけです。Git リポジトリ内
  なら、提案を別ブランチ（`proposal/image-placement-...`）にコミットし、元の
  ブランチは触らずに戻します。
- **VFM 記法は `vfm_render.py` に隔離** — 実際の挿入記法はそこだけにあります。
  現行 VFM に合わせて修正するのはこの 1 ファイルだけです。

## 構成

```
siturai/
├── place_images.py        メインドライバ（バックエンド非依存）
├── vfm_render.py          VFM 記法の隔離モジュール（ここだけが VFM を知る）
├── config.example.env     環境変数の雛形（.env にコピーして使う）
├── sample/
│   ├── manuscript.md      サンプル原稿「四季の庭園」
│   └── images/            サンプル画像（spring/summer/autumn/winter）
├── LICENSE                MIT
└── README.md
```

## セットアップ

```bash
# 1) ローカル VL（推奨：手元で確実に動かす）
ollama pull qwen3-vl:8b
ollama serve            # 既に起動していれば不要

# 2) 設定
cp config.example.env .env
# .env を編集（既定は Ollama / qwen3-vl:8b のローカル）
set -a; source .env; set +a

# 3) 実行（ビルドまで行うなら --build）
python place_images.py sample/manuscript.md --images sample/images --build
```

Vivliostyle CLI（ビルド用、任意）: `npm i -g @vivliostyle/cli`

Python は標準ライブラリのみ。追加 pip 不要です。

### Spark（vLLM）に向ける場合

`.env` を次のように変えるだけで、コードは変更不要です。

```
VL_BASE_URL=http://edgexpert-b0a9.tailXXXXXX.ts.net:8001/v1
VL_MODEL=Qwen/Qwen3-VL-8B-Instruct
```

## 使い方

```
python place_images.py <原稿.md> --images <画像フォルダ> [--out output] [--build] [--no-git]
```

- `--out` 出力先ディレクトリ（既定 `output`）
- `--build` 修正版コピーを Vivliostyle CLI でビルドして PDF を出す
- `--no-git` Git リポジトリ内でも提案ブランチを作らない

実行すると `output/` に次が出ます。**元の原稿は書き換わりません。**

- `<原稿>.modified.md` — 画像を挿入した修正版コピー
- `proposals.json` — VLM の生の配置提案
- `changes.diff` — 元原稿との差分
- `images/` — 自己完結のため画像をコピーしたもの

## 結果の見かた（評価ルーブリック）

`output/proposals.json` と、ビルドした PDF を見て、次を判定します。

1. **アンカー妥当性** — 各画像は、内容に対応する季節の節に寄ったか？
   （春の写真 → 「春」の節、など。ここが PoC の合否の核。）
2. **文言品質** — キャプション・alt はそのまま使える水準か、要手直しか。
3. **配置種別の妥当性** — figure / full-width などの選択は妥当か。
4. **ビルド健全性** — 挿入後の原稿が VFM として壊れずビルドできるか。

サンプル原稿「四季の庭園」は、四季の節を持つ短文です。季節の写真を入れると、
各画像が対応する季節の段落に正しく寄るかで配置精度を判定できます。

## 補足

- サンプル原稿「四季の庭園」およびサンプル画像（春・夏・秋・冬の 4 点）は、
  小形克宏が作成・撮影したものです。
- `place_images.py` / `vfm_render.py` は、AI コーディング支援（Claude）により
  生成されたコードを含みます。
- 使用 VLM（Qwen3-VL 系）および Vivliostyle CLI 等の第三者ソフトウェアは、
  各々のライセンスに従います。
