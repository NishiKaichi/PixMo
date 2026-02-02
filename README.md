# PixMo

<div align="center">

![PixMo](https://img.shields.io/badge/PixMo-Image%20Processing-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)

**高度な画像処理機能を備えた、モダンなウェブアプリケーション**

[デモ](#デモ) • [特徴](#特徴) • [セットアップ](#セットアップ) • [使い方](#使い方)

</div>

---

## 🎯 概要

**PixMo** は、複数の画像を効率的に処理できるフル機能のウェブアプリケーションです。セッション管理、バッチ処理、リアルタイムフィードバックを備えており、シームレスな画像編集体験を提供します。

## ✨ 特徴

- 🖼️ **複数画像同時処理** - 複数の画像を効率的にバッチ処理
- 📦 **セッション管理** - プロジェクトの状態を保持・復元
- ⚡ **リアルタイム処理** - 高速な画像変換とプレビュー
- 🎨 **モダンUI** - 直感的で美しいユーザーインターフェース
- 💾 **結果の一括ダウンロード** - ZIPフォーマットでの出力
- 🔧 **強力なAPI** - RESTful APIによる拡張性
- 🗑️ **自動クリーンアップ** - 期限切れセッションの自動管理

## 🛠️ 技術スタック

### バックエンド

| 技術 | 説明 |
|------|------|
| ![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white) | 高性能Pythonウェブフレームワーク |
| ![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white) | プログラミング言語 |
| ![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-CC2927?logo=sqlalchemy&logoColor=white) | SQLツールキット・ORM |
| ![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white) | 組み込みデータベース |
| ![Pillow](https://img.shields.io/badge/Pillow-12.0-3776AB?logo=python&logoColor=white) | 画像処理ライブラリ |

### フロントエンド

| 技術 | 説明 |
|------|------|
| ![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white) | UIライブラリ |
| ![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white) | 型安全なJavaScript |
| ![Vite](https://img.shields.io/badge/Vite-7.2-646CFF?logo=vite&logoColor=white) | 高速ビルドツール |
| ![Node.js](https://img.shields.io/badge/Node.js-20+-339933?logo=node.js&logoColor=white) | JavaScriptランタイム |

## 🚀 セットアップ

### 前提条件

- Python 3.8以上
- Node.js 18以上
- npm または yarn

### インストール手順

#### 1. リポジトリのクローン

```bash
git clone https://github.com/yourusername/PixMo.git
cd PixMo
```

#### 2. バックエンドのセットアップ

```bash
cd backend

# 仮想環境の作成
python -m venv venv

# 仮想環境の有効化
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 依存パッケージのインストール
pip install -r requirements.txt

# データベースの初期化
python -m backend.db
```

#### 3. フロントエンドのセットアップ

```bash
cd ../frontend

# 依存パッケージのインストール
npm install

# 開発サーバーの起動
npm run dev
```

#### 4. バックエンドサーバーの起動

```bash
# backend ディレクトリで
python -m uvicorn backend.main:app --reload --port 8000
```

## 📖 使い方

### バックエンドAPI

バックエンドは以下のエンドポイントを提供します：

| エンドポイント | メソッド | 説明 |
|---------|---------|------|
| `/api/sessions` | POST | 新しいセッションを作成 |
| `/api/sessions/{session_id}` | GET | セッション情報を取得 |
| `/api/upload/materials` | POST | 材料画像をアップロード |
| `/api/upload/targets` | POST | ターゲット画像をアップロード |
| `/api/process` | POST | 画像を処理 |
| `/api/results` | GET | 処理結果をダウンロード |

### 開発

```bash
# フロントエンド開発サーバー（ホットリロード有効）
npm run dev

# ビルド
npm run build

# リント
npm run lint
```

## 📁 プロジェクト構造

```
PixMo/
├── backend/                 # バックエンドアプリケーション
│   ├── main.py             # メインアプリケーション
│   ├── db.py               # データベース設定
│   ├── models.py           # データベースモデル
│   ├── settings.py         # 設定ファイル
│   ├── cleanup.py          # クリーンアップ処理
│   ├── requirements.txt     # Python依存関係
│   ├── db/                 # SQLiteデータベース
│   ├── uploads/            # アップロードファイル保存
│   └── results/            # 処理結果保存
│
├── frontend/               # フロントエンドアプリケーション
│   ├── src/
│   │   ├── App.tsx         # メインコンポーネント
│   │   ├── main.tsx        # エントリーポイント
│   │   ├── App.css         # スタイル
│   │   └── assets/         # 静的アセット
│   ├── public/             # 公開ファイル
│   ├── vite.config.ts      # Vite設定
│   ├── tsconfig.json       # TypeScript設定
│   └── package.json        # npm設定
│
└── README.md              # このファイル
```

## 🔧 環境変数

`.env` ファイルを作成して以下の環境変数を設定してください：

```env
# バックエンド設定
DATABASE_URL=sqlite:///./db/pixmo.sqlite3
UPLOADS_DIR=./uploads
RESULTS_DIR=./results
SESSION_TTL_MINUTES=60
```

## 🐛 トラブルシューティング

### ポート競合エラー
```bash
# 別のポートで起動
uvicorn backend.main:app --reload --port 8001
```

### データベースエラー
```bash
# データベースを再初期化
python -m backend.db
```

### フロントエンドの依存関係エラー
```bash
# キャッシュをクリアしてインストール
rm -rf node_modules
npm install
```

## 📊 パフォーマンス

- **画像処理**: 複数画像の並列処理対応
- **メモリ管理**: キャッシュ機構により高速処理を実現
- **スケーラビリティ**: セッション管理により大規模処理に対応

## 🤝 貢献

プルリクエストを歓迎します。大きな変更の場合は、まずissueを開いて変更内容を議論してください。

## 📝 ライセンス

このプロジェクトはMITライセンスの下で公開されています。詳細は[LICENSE](LICENSE)を参照してください。

## 📧 お問い合わせ

質問や提案がある場合は、GitHubのissueを作成してください。

---

<div align="center">

**⭐ このプロジェクトが役に立つ場合は、スターをいただけると幸いです！**

</div>