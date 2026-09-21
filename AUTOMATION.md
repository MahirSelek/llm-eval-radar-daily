# Eval Radar — Cursor Automation

Mac kapalıyken günlük brifing için Cursor’da **Automation** kur.

## Önerilen ayar

| Alan | Değer |
|------|--------|
| Name | LLM Eval Radar Daily |
| Trigger | Schedule — her gün **05:00 UTC** (= 08:00 Europe/Istanbul, yaz saatinde doğrula) |
| Repo | Bu proje (git’e push edilmiş olmalı) |
| Tools | Terminal / network |

## Agent talimatı (yapıştır)

```
You are Mahir's LLM Evaluation Radar harness.

1) In the repo root, ensure .env exists with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (secrets).
2) Run: python -m pip install -e . -q && python -m eval_radar.cli report
3) If report fails, diagnose briefly and retry once.
4) Scope ONLY: LLM evaluation / benchmarks / leaderboards / judges / contamination.
5) Be token-frugal: do not re-fetch manually if the CLI already collected; do not write long essays.
```

## Notlar

- İnteraktif Telegram sohbet için bot’un **polling** çalışması gerekir (`eval-radar bot`). Mac kapalıysa bot’u ücretsiz bir host’ta (Railway/Render/Fly) çalıştır; günlük push raporu Automation ile gelir.
- Secret’ları Automation ortamına ekle; repoya `.env` commit etme.
