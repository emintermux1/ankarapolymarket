# Ankara LTAC Weather Bot

Ankara Esenboğa (LTAC) günlük maksimum sıcaklık tahmini, model karşılaştırması ve Polymarket fiyatlama analizi için Telegram botu.

## MVP kapsamı

- AviationWeather METAR/TAF adapteri
- Open-Meteo deterministic + ensemble adapteri
- HavaForum Ankara thread scraper + günlük forum analizi
- IEM ASOS LTAC geçmiş arşivi adapteri
- Polymarket Gamma/CLOB/Data read-only reader
- Ek kaynak paketi: MET Norway Locationforecast, Open-Meteo ECMWF HRES 9 km, Open-Meteo Previous Runs, NOAA ISD LTAC arşivi, RainViewer radar, EUMETSAT MSG Cloud Mask kataloğu, DWD ICON Open Data, NASA POWER ve OGIMET health/resource entegrasyonları
- SQLAlchemy database modeli: observations, tafs, model_snapshots, forecast_runs, market_snapshots, daily_predictions, actual_results, source_status, backtest_scores, model_weights, analog_days
- Forecast engine: weighted ensemble, bias correction hook, live METAR adjustment, LTAC microclimate placeholder, advection, basınç/üst seviye, üst seviye/profil, cloud/radiation, rain/soil, confidence
- Telegram komutları: `/hourly`, `/today`, `/aviation` (`/ltac` alias), `/now`, `/metar`, `/metars`, `/taf`, `/models`, `/signals`, `/market`, `/edge`, `/backtest`, `/sources`, `/chart`, `/result`
- APScheduler: varsayılan kanal modu `hourly_max`; 08:00-20:00 arasında saat başı tek kısa maksimum sıcaklık tahmini ve yeni LTAC/LTFM METAR geldiği anda kısa sensör alarmı gönderir. Eski 09:00/12:00/15:00/21:00 raporları için `TELEGRAM_CHANNEL_MODE=legacy_reports` kullanılır.
- FastAPI dashboard: LTAC model stack, METAR/TAF, Polymarket bracket edge, risk board, kaynak/env matrisi
- Wunderground final result: API key yoksa scraper + admin manual fallback

## Kurulum

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
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
TELEGRAM_CHANNEL_MODE=hourly_max
ANKARA_TELEGRAM_HOURLY_FORECAST_ENABLED=true
ANKARA_TELEGRAM_HOURLY_FORECAST_CHANNEL_ID=@ankarapm
TELEGRAM_HOURLY_FORECAST_START_HOUR=08
TELEGRAM_HOURLY_FORECAST_END_HOUR=20
TELEGRAM_HOURLY_FORECAST_MINUTE=00
ANKARA_TELEGRAM_METAR_ALERTS_ENABLED=true
ANKARA_TELEGRAM_METAR_ALERT_CHANNEL_ID=@ankarapm
ANKARA_TELEGRAM_METAR_ALERT_STATION_IDS=LTAC,LTFM
ANKARA_TELEGRAM_METAR_ALERT_INTERVAL_SECONDS=60
```

`ANKARA_TELEGRAM_*` değişkenleri aynı sunucuda başka botlar varsa özellikle tercih edilir; geriye dönük uyumluluk için eski `TELEGRAM_*` adları hâlâ okunur ama Ankara prefix'i varsa o kazanır.

Saatlik kanal mesajı uzun analiz göndermez; yalnız bugünün beklenen resmi maksimum derecesini, model merkezini, canlı/gün içi maksimumu, güveni, yuvarlama sınır riskini ve Polymarket bracket/fair/edge özetini verir. `TELEGRAM_CHANNEL_MODE=both` hem saatlik kısa tahmini hem legacy raporları açar.

METAR alarmı her 60 saniyede LTAC ve LTFM’i kontrol eder; aynı gözlem zamanı için tekrar göndermez. Mesaj sıcaklık/çiy/nem, rüzgâr/gust, basınç, görüş, bulut, hava olayı, yağış/kar ve raw METAR satırını içerir.

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
