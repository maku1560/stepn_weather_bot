# STEPN Weather Bot (Discord)

関西弁フレンドリー仕様の**直近3時間の天気Bot**やで。  
メンション＋地名、もしくは `/weather` で場所を入力すると、Open‑Meteo の無料API（APIキー不要）でサクッと返す。

---

## 使い方（超速）

1. **Discord Developer Portal** でアプリ作成 → Botを作成  
   - Privileged Gateway Intents で **MESSAGE CONTENT INTENT** を `ON` にする  
   - Bot をサーバーに招待（スコープ：`bot applications.commands`、権限：`Send Messages`, `Read Message History` など）
2. `.env` を作り、`DISCORD_BOT_TOKEN` を設定
3. ローカル or ホスティングで実行：
   ```bash
   pip install -r requirements.txt
   python main.py
   ```
4. Discordで **@Bot 札幌** のようにメンション＋地名、または `/weather location:札幌` を実行

---

## ここまで全部（ローカルで動かす）

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# .env を作成してトークン設定
python main.py
```

### .env の例
```
DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> **注意**: メッセージ本文を扱うため、開発者ポータルで **MESSAGE CONTENT INTENT** を必ずONにしてや。

---

## ホスティング（例：Railway）

1. GitHubにこのプロジェクトをpush  
2. Railwayで新規プロジェクト → GitHubリポジトリを選択  
3. `Variables` に `DISCORD_BOT_TOKEN` を追加  
4. `Deploy` したらOK（Procfileの `worker: python main.py` を自動で使う構成）

Render/Heroku系でも同様。

---

## 仕様

- 入力：メンション + 地名、または `/weather location:<地名>`
- 出力：**直近3時間**の「時刻 / 天気アイコン / 気温 / 降水確率 / 降水量」
- API：
  - ジオコーディング：`https://geocoding-api.open-meteo.com/v1/search`
  - 天気：`https://api.open-meteo.com/v1/forecast`
- タイムゾーン：原則返却されたTZ。なければ `Asia/Tokyo`。表示フッターはJST。

---

## トラブルシュート

- `RuntimeError: 環境変数...` → `.env` の設定忘れ
- 何も反応しない → Botに権限があるか、サーバーに招待できているか確認
- 地名がヒットしない → 「市」「駅」などを付ける or 別スペルで再トライ

---

## ライセンス

MIT でどうぞ。
