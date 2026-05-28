# Ankara LTAC Weather Bot

Ankara Esenboğa (LTAC) günlük maksimum sıcaklık tahmini, model karşılaştırması ve Polymarket fiyatlama analizi için Telegram botu.

## MVP kapsamı

- AviationWeather METAR/TAF adapteri
- Open-Meteo deterministic + ensemble adapteri
- HavaForum Ankara thread scraper + günlük forum analizi
- IEM ASOS LTAC geçmiş arşivi adapteri
- Polymarket Gamma/CLOB/Data read-only reader
- SQLAlchemy database modeli: observations, tafs, model_snapshots, forecast_runs, market_snapshots, daily_predictions, actual_results, source_status, backtest_scores, model_weights, analog_days
- Forecast engine: weighted ensemble, bias correction hook, live METAR adjustment, LTAC microclimate placeholder, advection, basınç/üst seviye, üst seviye/profil, cloud/radiation, rain/soil, confidence
- Telegram komutları: `/forecast`, `/today`, `/aviation` (`/ltac` alias), `/now`, `/metar`, `/taf`, `/models`, `/signals`, `/market`, `/edge`, `/backtest`, `/sources`, `/chart`, `/result`
- APScheduler: kanala her saat başı yalnızca bugünün maksimum sıcaklık tahmin özetini gönderir (`SCHEDULE_HOURLY_FORECAST_MINUTE=0`)
- FastAPI dashboard: LTAC model stack, METAR/TAF, Polymarket bracket edge, risk board, kaynak/env matrisi
- Wunderground final result: API key yoksa scraper + admin manual fallback

## Kurulum

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python -m src.main hourly --date 2026-05-24
python -m src.main report --date 2026-05-24
python -m src.main aviation --date 2026-05-24
python -m src.main forum --date 2026-05-24
python -m src.main web --host 127.0.0.1 --port 8000
python -m src.main bot
```

Gerçek tokenları `.env` içine koy; repo’ya yazma.

## PostgreSQL

Hostinger VPS için önerilen `.env`:

```env
DATABASE_URL=postgresql+psycopg://ltac:change-me@db:5432/ltac_weather
ANKARA_TELEGRAM_BOT_TOKEN=...
ANKARA_TELEGRAM_CHANNEL_ID=@ankarapm
ANKARA_TELEGRAM_ADMIN_IDS=1374723312
ANKARA_TELEGRAM_ALLOWED_CHAT_IDS=@ankarapm,1374723312
```

`ANKARA_TELEGRAM_*` değişkenleri aynı sunucuda başka botlar varsa özellikle tercih edilir; geriye dönük uyumluluk için eski `TELEGRAM_*` adları hâlâ okunur ama Ankara prefix'i varsa o kazanır.

Docker:

```bash
docker compose up -d --build
```

Systemd:

```bash
sudo cp deploy/hostinger-systemd.service /etc/systemd/system/ankara-ltac-weather-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now ankara-ltac-weather-bot
```

## Opsiyonel API key linkleri

V1 için şart değil; kalite/fallback için sonradan eklenebilir.

- CheckWX: https://www.checkwxapi.com/
- AVWX: https://avwx.rest/
- OpenWeather: https://openweathermap.org/api
- Weatherbit: https://www.weatherbit.io/api
- Visual Crossing: https://www.visualcrossing.com/weather-api/
- WeatherAPI: https://www.weatherapi.com/docs/
- Tomorrow.io: https://docs.tomorrow.io/reference/weather-forecast
- Meteoblue: https://docs.meteoblue.com/
- Windy: https://api.windy.com/
- Weather.com/Wunderground/TWC API: https://developer.weather.com/
- MapTiler/Mapbox/Cesium/HERE: dashboard harita katmanları için env-ready; tokenlar server-side tutulur.

## Market çözüm notu

Polymarket Ankara marketi Wunderground Esenboğa Intl Airport Station günlük en yüksek sıcaklığına göre, tam °C hassasiyetle resolve ediyor. Bot bu yüzden tahmini `final_tmax` olarak üretir ama bracket olasılıklarını integer-rounding sınırlarıyla hesaplar. Wunderground statik HTML final değeri göstermediğinde `/result <tmax> <YYYY-MM-DD>` admin komutu ile manuel final kayıt yapılır.

`/aviation` raporu LTAC METAR/TAF sinyallerini, Wunderground History URL'sini, canlı IEM ASOS/METAR maksimum proxy'sini, 13:30-15:30 lokal pik sıcaklık penceresini ve CB/bulut/radyasyon risklerini tek ekranda toplar. Bu rapor MGM'nin küsuratlı istasyon değerini değil, Wunderground'un METAR kaynaklı tam °C settlement mantığını esas alır.

## Güven skoru

0-100 deterministik skor; model spread, model availability, METAR freshness, live/model alignment, cloud/rain uncertainty, TAF availability, backtest history and market liquidity sinyallerinden oluşur. Veri eksikse skor düşer; örnek sayı basılmaz, alan `unavailable` olur.
