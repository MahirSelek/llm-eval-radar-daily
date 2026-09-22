# LLM Eval Radar

Açık web (arXiv + GitHub + Reddit) → günlük LLM **evaluation** brifingi → **Telegram**.

X API / şifre yok. Pioneer seed: `config/seeds.yaml`.

## Hızlı kurulum

```bash
cd /Users/mahirselek/Desktop/DSPhD/MS/agentic-twitter
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# .env içine TELEGRAM_BOT_TOKEN yapıştır (chate yazma)
```

### 1) Botu bir kez dinlet + /start

```bash
python -m eval_radar.cli bot
```

Telegram’da botuna git → **Start** / `/start`  
Chat id `data/memory/chat_id.txt` içine yazılır. İstersen aynı id’yi `.env` → `TELEGRAM_CHAT_ID=` olarak da koy.

Bot **açıkken** otomatik:
- her `COLLECT_INTERVAL_MINUTES` (varsayılan 90) → taze sinyal toplayıp **aynı gün havuzuna** merge
- `DAILY_DISPATCH_HOUR:MINUTE` (varsayılan 08:00 `REPORT_TZ`) → **Telegram + GitHub Pages aynı havuzdan**, günde bir kez
- `AUTO_GIT_PUSH=1` ise `docs/` / `content/` commit+push → github.io güncellenir

Mac kapalıysa yedek: Actions `05:00 UTC` publish.

### 2) Manuel rapor / ortak dispatch

```bash
python -m eval_radar.cli collect --accumulate   # sadece gün havuzuna ekle
python -m eval_radar.cli report                 # havuz + Telegram
python -m eval_radar.cli dispatch --force       # Telegram + site (+ git push)
```

Telegram: `/report` · `/dispatch` (hemen ortak yayın)

### LLM (Cursor Premium — tercih edilen)

IDE sohbeti otomatik akmaz. Cursor hesabından API key gerekir (usage aynı hesaba yazılır):

1. https://cursor.com/dashboard/integrations → API key oluştur  
2. `.env` → `CURSOR_API_KEY=...`  
3. İsteğe bağlı: `CURSOR_MODEL=composer-2.5` (veya hesabındaki başka model id)  
4. Bot + dashboard’u **restart** et  

Öncelik: **Cursor → Gemini → OpenAI**. Token’lar dashboard’da `usage.jsonl` ile görünür.


| Komut | Ne yapar |
|-------|----------|
| `/report` | Gün havuzuna ekle + rapor at |
| `/dispatch` | Telegram + github.io aynı havuzdan |
| `/memory` | Öğrenilen tercihler |
| `/help` | Yardım |
| `boost: topic` | Öncelik |
| `mute: topic` | Sustur |
| `takip et: Name` | Extra seed onayla |

Serbest mesajlar da `data/memory/` altına not düşer (ikinci beyin).

## Lokal dashboard (Flask)

```bash
python -m eval_radar.cli dashboard
# → http://127.0.0.1:8765
```

Güvenlik notu:
- Dashboard login sayfası aktiftir (`/login`).
- Şifre: `.env` içindeki `DASHBOARD_PASSWORD`
- Session imzalama anahtarı: `.env` içindeki `FLASK_SECRET_KEY`
- `DASHBOARD_LOCAL_ONLY=1` iken sadece loopback (`127.0.0.1` / `::1`) erişimine izin verilir.

Göreceğin şeyler:
- aktif model + bugün/toplam token
- digests, yazarlar, konu başlıkları, kanallar
- memory / sohbet logu
- Settings: token’lar, caps, seeds YAML, profil
- Chat LLM: kendi agent’ınla pekiştirme sohbeti

Token log dosyası: `data/memory/usage.jsonl`

## GitHub publisher agent

Bu projeye ikinci bir agent eklendi: günlük teknik yazı üretip `docs/` içine statik site basar.

Yerelde test:

```bash
python -m eval_radar.cli publish
# veya aynı havuzdan Telegram+site:
python -m eval_radar.cli dispatch --force
```

`dispatch` / bot saati:
- gün boyu biriken digest’ten LLM makale üretir,
- Telegram uzun raporu + `docs/` site’ini **aynı payload** ile basar,
- `AUTO_GIT_PUSH=1` ise push eder.

`publish` tek başına hâlâ taze collect + site (Actions yedeği).

Model seçimi:
- varsayılan: `CURSOR_MODEL`
- yayın için override: `.env` içine `PUBLISHER_MODEL=grok-4.5` gibi bir değer koyabilirsin.

### GitHub Actions ile günlük otomatik yayın

Workflow dosyaları:
- `.github/workflows/daily-publish.yml` (her gün 05:00 UTC üretim + commit)
- `.github/workflows/deploy-pages.yml` (`docs/` klasörünü GitHub Pages’e deploy)

Repository secrets:
- `CURSOR_API_KEY` (zorunlu)
- `CURSOR_MODEL` (opsiyonel)
- `PUBLISHER_MODEL` (opsiyonel; Grok kullanmak istersen)

Pages aktif olunca site:
- `https://<github-kullanici-adin>.github.io/<repo-adi>/`


Bkz. `AUTOMATION.md` — Cursor Automation schedule ~08:00 Europe/Istanbul.

İnteraktif bot 7/24 için bot process’inin bir yerde ayakta olması gerekir (Mac veya ucuz host).

## Klasörler

```
config/seeds.yaml      # pioneer + repo + subreddit + keywords
data/memory/           # profil, tercihler, chat log
data/digests/          # günlük JSON
src/eval_radar/        # kod
```
