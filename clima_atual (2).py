"""
Clima Urbano — Estação inteligente (versão Python / Streamlit, 100% dados reais)

Como rodar:
    pip install streamlit requests
    streamlit run clima_atual.py

Tudo na tela vem de APIs gratuitas, sem chave:
  - Open-Meteo Forecast  : temperatura, sensação, umidade, ponto de orvalho, pressão,
                           vento, chuva, radiação solar, histórico (30 dias) e previsão (7 dias)
  - Open-Meteo Air Quality: AQI (EUA) e PM2.5
  - Open-Meteo Geocoding : busca de cidade
  - Nominatim (OSM)      : nome da cidade/UF a partir das coordenadas
  - OpenStreetMap        : mapa
  - ipwho.is             : localização aproximada pelo IP
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st
import streamlit.components.v1 as components

REFRESH_SECONDS = 600  # atualização automática a cada 10 min
PAST_DAYS = 30

DEFAULT_LOCATION = {
    "latitude": -22.7253, "longitude": -47.6492,
    "city": "Piracicaba", "region": "SP",
    "accuracy": None, "source": "default",
}

COLORS = {"coral": "#e96c52", "blue": "#4386ca", "violet": "#826cc2", "green": "#197657"}
WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


# ----------------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------------
def fmt(value: float | None, decimals: int = 0) -> str:
    """Formata número no padrão pt-BR (1.014,5)."""
    if value is None:
        return "—"
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def coordinates_label(lat: float, lon: float) -> str:
    return (f"{abs(lat):.3f}° {'N' if lat >= 0 else 'S'} · "
            f"{abs(lon):.3f}° {'L' if lon >= 0 else 'O'}")


def clean(values: list) -> list[float]:
    return [v for v in values if v is not None]


def spark_points(values: list[float | None], n: int = 10) -> str:
    """Converte uma série em pontos de polyline (viewBox 108x30)."""
    vals = clean(values)
    if len(vals) < 2:
        return "0,15 108,15"
    if len(vals) > n:
        vals = [vals[round(k * (len(vals) - 1) / (n - 1))] for k in range(n)]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(vals):
        x = i / (len(vals) - 1) * 108
        y = 27 - (v - lo) / span * 24 if hi != lo else 15
        pts.append(f"{x:.0f},{y:.1f}")
    return " ".join(pts)


def weather_icon(code: int | None) -> str:
    if code is None:
        return "❔"
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄️"
    if code >= 95:
        return "⛈️"
    return "🌧️"  # garoa, chuva, pancadas


def hour_index(times: list[str], now_iso: str) -> int:
    key = now_iso[:13] + ":00"
    return times.index(key) if key in times else len(times) - 1


# ----------------------------------------------------------------------------
# APIs
# ----------------------------------------------------------------------------
def locate_by_ip() -> dict | None:
    try:
        d = requests.get("https://ipwho.is/", timeout=10).json()
        if not d.get("success"):
            return None
        return {"latitude": d["latitude"], "longitude": d["longitude"], "accuracy": 5000}
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float) -> tuple[str, str] | None:
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "json", "lat": lat, "lon": lon,
                    "addressdetails": 1, "accept-language": "pt-BR"},
            headers={"User-Agent": "clima-urbano-python/2.0"}, timeout=10)
        if not r.ok:
            return None
        a = r.json().get("address", {})
        city = a.get("city") or a.get("town") or a.get("municipality") or a.get("village")
        if not city:
            return None
        iso = a.get("ISO3166-2-lvl4")
        return city, (iso.split("-")[-1] if iso else a.get("state", ""))
    except Exception:
        return None


def search_city(name: str) -> list[dict]:
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 5, "language": "pt", "format": "json"},
                         timeout=10)
        return r.json().get("results", []) if r.ok else []
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load_data(lat: float, lon: float) -> dict:
    """Busca tudo de uma vez. Levanta exceção se o clima falhar (não vai pro cache)."""
    t0 = time.time()
    w = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "past_days": PAST_DAYS, "forecast_days": 7,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                       "surface_pressure,wind_speed_10m,precipitation,shortwave_radiation",
            "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,dew_point_2m,"
                      "precipitation_probability,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,precipitation_sum",
        }, timeout=20)
    w.raise_for_status()
    weather = w.json()
    latency_ms = round((time.time() - t0) * 1000)

    air = None
    try:
        a = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={"latitude": lat, "longitude": lon, "timezone": "auto",
                    "current": "us_aqi,pm2_5", "hourly": "us_aqi",
                    "past_days": 2, "forecast_days": 1}, timeout=20)
        if a.ok:
            air = a.json()
    except Exception:
        pass

    return {"weather": weather, "air": air, "fetched_at": time.time(), "latency_ms": latency_ms}


# ----------------------------------------------------------------------------
# Regras de negócio (derivam tudo dos dados)
# ----------------------------------------------------------------------------
def build_metrics(data: dict | None, failed: bool) -> list[dict]:
    base = [
        dict(title="Temperatura", unit="°C", icon="🌡️", color="coral"),
        dict(title="Umidade", unit="%", icon="💧", color="blue"),
        dict(title="Pressão", unit="hPa", icon="🧭", color="violet"),
        dict(title="Qualidade do ar", unit="AQI", icon="🌬️", color="green"),
    ]
    if data is None:
        for m in base:
            m.update(value="—", points="0,15 108,15",
                     detail="Dados indisponíveis" if failed else "Buscando leitura atual",
                     status="Offline" if failed else "Atualizando")
        return base

    w, air = data["weather"], data["air"]
    cur, hr = w["current"], w["hourly"]
    i = hour_index(hr["time"], cur["time"])
    last24 = lambda key: hr[key][max(0, i - 23): i + 1]

    # Temperatura
    t = cur["temperature_2m"]
    base[0].update(
        value=fmt(t, 1), detail=f"Sensação de {fmt(cur['apparent_temperature'], 1)}°",
        status="Quente" if t > 30 else "Frio" if t < 15 else "Agradável",
        points=spark_points(last24("temperature_2m")))

    # Umidade
    h = cur["relative_humidity_2m"]
    base[1].update(
        value=fmt(h), detail=f"Ponto de orvalho {fmt(hr['dew_point_2m'][i])}°",
        status="Ideal" if 30 <= h <= 70 else "Atenção",
        points=spark_points(last24("relative_humidity_2m")))

    # Pressão (tendência real em 3h)
    p = cur["surface_pressure"]
    p3 = hr["surface_pressure"][i - 3] if i >= 3 and hr["surface_pressure"][i - 3] else p
    d = p - p3
    if abs(d) < 1:
        status, detail = "Estável", "Estável nas últimas 3h"
    elif d > 0:
        status, detail = "Subindo", f"+{fmt(d, 1)} hPa em 3h"
    else:
        status, detail = "Caindo", f"{fmt(d, 1)} hPa em 3h"
    base[2].update(value=fmt(p), detail=detail, status=status,
                   points=spark_points(last24("surface_pressure")))

    # Qualidade do ar
    if air and air.get("current"):
        aqi, pm = air["current"]["us_aqi"], air["current"]["pm2_5"]
        ai = hour_index(air["hourly"]["time"], air["current"]["time"])
        base[3].update(
            value=fmt(aqi), detail=f"PM2.5 em {fmt(pm, 1)} µg/m³",
            status="Boa" if aqi <= 50 else "Moderada" if aqi <= 100 else "Atenção",
            points=spark_points(air["hourly"]["us_aqi"][max(0, ai - 23): ai + 1]))
    else:
        base[3].update(value="—", detail="Dados indisponíveis", status="Offline",
                       points="0,15 108,15")
    return base


def build_history(data: dict, period: str) -> dict:
    """Série real de temperatura para 24h / 7d / 30d."""
    w = data["weather"]
    hr, cur = w["hourly"], w["current"]
    i = hour_index(hr["time"], cur["time"])
    hours = {"24h": 24, "7d": 168, "30d": 720}[period]
    start = max(0, i - hours + 1)
    times = hr["time"][start: i + 1]
    vals = hr["temperature_2m"][start: i + 1]
    pairs = [(t, v) for t, v in zip(times, vals) if v is not None]
    nums = [v for _, v in pairs]
    return {
        "pairs": pairs,
        "avg": sum(nums) / len(nums) if nums else None,
        "max": max(nums) if nums else None,
        "min": min(nums) if nums else None,
    }


def build_forecast(data: dict) -> list[dict]:
    w = data["weather"]
    daily, cur = w["daily"], w["current"]
    today = cur["time"][:10]
    start = daily["time"].index(today) if today in daily["time"] else PAST_DAYS
    out = []
    for k in range(5):
        j = start + k
        if j >= len(daily["time"]):
            break
        dt = datetime.strptime(daily["time"][j], "%Y-%m-%d")
        out.append(dict(
            day="Hoje" if k == 0 else WEEKDAYS[dt.weekday()],
            icon=weather_icon(daily["weather_code"][j]),
            high=f"{fmt(daily['temperature_2m_max'][j])}°",
            low=f"{fmt(daily['temperature_2m_min'][j])}°",
            rain=f"{fmt(daily['precipitation_probability_max'][j] or 0)}%",
        ))
    return out


def build_alert(data: dict) -> dict:
    """Procura chuva nas próximas 24h a partir das previsões horárias."""
    w = data["weather"]
    hr, cur = w["hourly"], w["current"]
    i = hour_index(hr["time"], cur["time"])
    rng = range(i, min(i + 24, len(hr["time"])))
    wet = [k for k in rng
           if (hr["precipitation_probability"][k] or 0) >= 50
           or (hr["precipitation"][k] or 0) >= 0.3]
    if not wet:
        return {"rain": False, "title": "Sem previsão de chuva",
                "text": "Nenhuma chuva significativa prevista para as próximas 24 horas."}
    run = [wet[0]]
    for k in wet[1:]:
        if k == run[-1] + 1:
            run.append(k)
        else:
            break
    total = sum(hr["precipitation"][k] or 0 for k in run)
    peak = max(hr["precipitation"][k] or 0 for k in run)
    sev = "leve" if peak < 2.5 else "moderada" if peak < 7.6 else "forte"
    h0 = int(hr["time"][run[0]][11:13])
    h1 = (int(hr["time"][run[-1]][11:13]) + 1) % 24
    when = "A partir de agora" if run[0] == i else f"Entre {h0}h e {h1}h"
    return {"rain": True, "title": f"Possibilidade de chuva {sev}",
            "text": f"{when}, com acumulado estimado de {fmt(total, 1)} mm. Leve um guarda-chuva."}


def build_environment(data: dict) -> dict:
    w = data["weather"]
    cur, daily = w["current"], w["daily"]
    rad = cur.get("shortwave_radiation") or 0
    lux = rad * 120  # eficácia luminosa média da luz solar ≈ 105–120 lm/W
    wind = cur.get("wind_speed_10m") or 0
    today = cur["time"][:10]
    j = daily["time"].index(today) if today in daily["time"] else PAST_DAYS
    rain_today = daily["precipitation_sum"][j] or 0
    return {
        "lux": lux, "lux_pct": min(100, rad / 1000 * 100), "rad": rad,
        "wind": wind, "wind_pct": min(100, wind / 50 * 100),
        "rain": rain_today, "rain_pct": min(100, rain_today / 20 * 100),
    }


def station_status(data: dict | None) -> dict:
    if not data:
        return {"online": False, "sensors": 0, "latency": None}
    cur = data["weather"]["current"]
    air = (data["air"] or {}).get("current") or {}
    checks = [cur.get("temperature_2m"), cur.get("relative_humidity_2m"),
              cur.get("surface_pressure"), cur.get("wind_speed_10m"), air.get("us_aqi")]
    n = sum(v is not None for v in checks)
    return {"online": n > 0, "sensors": n, "latency": data["latency_ms"]}


def location_now(data: dict | None) -> datetime:
    """Hora local do lugar monitorado (usa o fuso devolvido pela API)."""
    offset = (data or {}).get("weather", {}).get("utc_offset_seconds")
    if offset is None:
        return datetime.now()
    return datetime.now(timezone(timedelta(seconds=offset)))


def updated_label(fetched_at: float | None, just_refreshed: bool) -> str:
    if fetched_at is None:
        return "sem dados"
    age = time.time() - fetched_at
    if just_refreshed and age < 60:
        return "neste instante"
    if age < 10:
        return "agora"
    if age < 60:
        return "há menos de 1 min"
    return f"há {int(age // 60)} min"


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------
CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap");
/* Cores derivadas do tema ativo do Streamlit: currentColor = cor do texto do tema */
:root{--ink:currentColor;
--muted:color-mix(in srgb,currentColor 68%,transparent);
--subtle:color-mix(in srgb,currentColor 48%,transparent);
--line:color-mix(in srgb,currentColor 14%,transparent);
--surface:color-mix(in srgb,currentColor 5%,transparent);
--canvas:color-mix(in srgb,currentColor 8%,transparent);
--topbar:color-mix(in srgb,currentColor 5%,transparent);
--brand:#26a37a;--brand-soft:color-mix(in srgb,#26a37a 16%,transparent);
--positive:#33a474;--coral:#e96c52;--blue:#4f93d8;--violet:#8f7ad0;--sun:#e4a72a;--warning:#eba942;
--al-line:color-mix(in srgb,#eba942 40%,transparent);--al-bg:color-mix(in srgb,#eba942 12%,transparent);
--al-ink:currentColor;--al-copy:color-mix(in srgb,currentColor 75%,transparent);
--ok-line:color-mix(in srgb,#33a474 40%,transparent);--ok-bg:color-mix(in srgb,#33a474 12%,transparent);
--ok-ink:currentColor;--ok-copy:color-mix(in srgb,currentColor 75%,transparent);
--ic-coral:color-mix(in srgb,#e96c52 16%,transparent);--ic-blue:color-mix(in srgb,#4f93d8 16%,transparent);
--ic-violet:color-mix(in srgb,#8f7ad0 16%,transparent);
--shadow:0 1px 2px rgb(0 0 0/8%),0 8px 28px rgb(0 0 0/8%);}
/* Modo claro: fundo branco, cartões brancos com borda suave */
:root[data-theme="light"]{--ink:#14201b;--muted:#607068;--subtle:#8a9790;--line:#e2e8e3;
--surface:#fff;--canvas:#f5f7f3;--topbar:#fff;--brand:#197657;--brand-soft:#e8f4ee;
--blue:#4386ca;--violet:#826cc2;
--al-line:#f5e1bd;--al-bg:#fdf7ed;--al-ink:#6c4511;--al-copy:#8b5d1b;
--ok-line:#cfe8da;--ok-bg:#f1f9f4;--ok-ink:#14573f;--ok-copy:#2d7a5a;
--ic-coral:#fcecea;--ic-blue:#ecf3fa;--ic-violet:#f0edf8;
--shadow:0 1px 2px rgb(20 32 27/4%),0 8px 28px rgb(20 32 27/5%);}
html,body,[data-testid="stAppViewContainer"],.stApp{font-family:"Manrope",sans-serif;}
[data-testid="stHeader"]{background:transparent;}
.block-container{max-width:80rem;padding-top:1rem;padding-bottom:3rem;}
.topbar{display:flex;align-items:center;justify-content:space-between;background:var(--topbar);
border-bottom:1px solid var(--line);padding:.9rem 1.2rem;margin:0 0 2rem;border-radius:0 0 1rem 1rem;}
.logo{display:flex;align-items:center;gap:.75rem}
.logo .mark{width:40px;height:40px;border-radius:12px;background:var(--brand);color:#fff;display:flex;
align-items:center;justify-content:center;font-size:20px;box-shadow:0 8px 20px rgb(25 118 87/22%)}
.logo b{display:block;font-size:1rem;line-height:1}
.logo small{display:block;margin-top:4px;font-size:10px;font-weight:800;letter-spacing:.16em;
text-transform:uppercase;color:var(--brand)}
.nav-active{color:var(--brand);font-weight:700;font-size:.8rem;border-bottom:2px solid var(--brand);padding:.5rem 0}
.where{color:var(--brand);font-size:.875rem;font-weight:500;margin-bottom:.6rem}
h1.hello{font-size:2.2rem;font-weight:600;letter-spacing:-.02em;margin:0;padding:0}
.lead{color:var(--muted);font-size:.95rem;margin-top:.5rem;max-width:36rem}
.upd{text-align:right}.upd b{display:block;font-size:.75rem;color:var(--muted)}
.upd span{font-size:.75rem;color:var(--subtle)}
.alert{display:flex;align-items:center;gap:.85rem;border:1px solid var(--al-line);border-radius:1rem;
background:var(--al-bg);padding:.8rem 1rem}
.alert.ok{border-color:var(--ok-line);background:var(--ok-bg)}
.alert .ic{width:40px;height:40px;border-radius:12px;background:rgb(235 169 66/15%);display:flex;
align-items:center;justify-content:center;font-size:20px}
.alert.ok .ic{background:rgb(51 164 116/15%)}
.alert b{display:block;font-size:.875rem;color:var(--al-ink)}.alert.ok b{color:var(--ok-ink)}
.alert span{font-size:.75rem;color:var(--al-copy)}.alert.ok span{color:var(--ok-copy)}
.card{border:1px solid var(--line);border-radius:1.25rem;background:var(--surface);
box-shadow:var(--shadow);padding:1.25rem;height:100%;transition:transform .18s,box-shadow .18s}
.card.hover:hover{transform:translateY(-2px);box-shadow:0 12px 34px rgb(20 32 27/8%)}
.row{display:flex;justify-content:space-between;align-items:flex-start}
.icon-box{width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:19px}
.icon-coral{background:var(--ic-coral)}.icon-blue{background:var(--ic-blue)}.icon-violet{background:var(--ic-violet)}.icon-green{background:var(--brand-soft)}
.pill{display:inline-flex;align-items:center;gap:.375rem;border:1px solid var(--line);border-radius:999px;
padding:.3rem .55rem;color:var(--muted);font-size:.65rem;font-weight:700}
.dot{width:6px;height:6px;border-radius:50%;background:var(--positive)}.dot.off{background:#c0554a}
.mt-title{margin:1.2rem 0 0;font-size:.875rem;font-weight:600;color:var(--muted)}
.mt-val{display:flex;align-items:flex-end;gap:6px;margin-top:4px}
.mt-val strong{font-size:1.875rem;font-weight:600;letter-spacing:-.02em}
.mt-val span{font-size:.875rem;font-weight:600;color:var(--muted);margin-bottom:4px}
.mt-foot{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin-top:.75rem}
.mt-foot p{margin:0;font-size:.75rem;color:var(--subtle)}
.eyebrow{color:var(--brand);font-size:.65rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;margin:0}
h2.t{font-size:1.1rem;font-weight:700;letter-spacing:-.01em;margin:.25rem 0 0;padding:0}
.stats{display:flex;gap:1.25rem;border-bottom:1px solid var(--line);padding:1rem 0}
.stats span{font-size:.75rem;color:var(--subtle)}
.stats p{margin:0;font-family:"DM Mono",monospace;font-size:.875rem;font-weight:700}
.chart{position:relative;margin-top:1.5rem;height:14rem}
.chart .grid{position:absolute;inset:12px 0;background-image:linear-gradient(to bottom,var(--line) 1px,transparent 1px);
background-size:100% 25%}
.chart svg{position:relative;width:100%;height:100%;overflow:visible}
.chart svg circle.hit{fill:transparent;cursor:crosshair}
.chart svg circle.hit:hover{fill:#e96c5233}
.chart .hours{position:absolute;left:0;right:0;bottom:0;display:flex;justify-content:space-between;
font-size:.75rem;color:var(--subtle)}
.fc{display:flex;align-items:center;gap:.9rem;border-radius:.8rem;padding:.85rem .75rem}
.fc.today{background:var(--brand-soft)}
.fc .d{width:3rem;font-size:.875rem;font-weight:600}
.fc .r{flex:1;font-size:.75rem;color:var(--blue)}
.fc .h{font-family:"DM Mono",monospace;font-size:.875rem;font-weight:700}
.fc .l{width:2rem;font-family:"DM Mono",monospace;font-size:.875rem;color:var(--subtle)}
.prog-h{display:flex;justify-content:space-between;font-size:.875rem;margin:1.5rem 0 .5rem}
.prog-h span{font-weight:600}
.track{height:.4rem;border-radius:999px;background:var(--canvas);overflow:hidden}
.track i{display:block;height:100%;border-radius:inherit}
.hint{font-size:.65rem;color:var(--subtle);margin-top:1.25rem}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-top:1.75rem}
.tile{display:grid;grid-template-columns:auto 1fr;gap:.25rem .5rem;border:1px solid var(--line);
border-radius:.9rem;background:var(--canvas);padding:.8rem}
.tile span{color:var(--subtle);font-size:.65rem;font-weight:600}
.tile b{grid-column:2;font-family:"DM Mono",monospace;font-size:.8rem}
.next{border-top:1px solid var(--line);padding-top:1.25rem;margin-top:1.5rem}
.next .l{display:flex;justify-content:space-between;font-size:.75rem;color:var(--subtle)}
.next .l b{font-family:"DM Mono",monospace;color:var(--brand)}
.next p{font-size:.75rem;color:var(--muted);line-height:1.6;margin:.75rem 0 0}
.footer{border-top:1px solid var(--line);background:var(--surface);margin-top:2.5rem;padding:1.5rem;
display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem;font-size:.75rem;color:var(--subtle)}
div.stButton>button{border-radius:.75rem;font-weight:700;font-size:.75rem}
div.stButton>button[kind="primary"]{background:var(--brand);border-color:var(--brand);color:#fff;
box-shadow:0 8px 20px rgb(25 118 87/22%)}
</style>
"""


def metric_card_html(m: dict) -> str:
    c = COLORS[m["color"]]
    return f"""
<div class="card hover">
  <div class="row"><div class="icon-box icon-{m['color']}">{m['icon']}</div>
    <span class="pill"><span class="dot"></span>{m['status']}</span></div>
  <p class="mt-title">{m['title']}</p>
  <div class="mt-val"><strong>{m['value']}</strong><span>{m['unit']}</span></div>
  <div class="mt-foot"><p>{m['detail']}</p>
    <svg viewBox="0 0 108 30" width="96" height="32" style="opacity:.8;color:{c}" role="img"
         aria-label="Tendência de {m['title']} nas últimas 24h">
      <polyline points="{m['points']}" fill="none" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round"/></svg></div>
</div>"""


def temperature_chart_html(hist: dict, period: str) -> str:
    pairs = hist["pairs"]
    if len(pairs) < 2:
        return '<div class="chart"><p style="color:var(--subtle)">Sem dados para o período.</p></div>'
    # reduz a ~96 pontos para manter o SVG leve (sempre mantém o último)
    step = max(1, len(pairs) // 96)
    pts = pairs[::-1][::step][::-1]
    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    xy = [(i / (len(pts) - 1) * 760, 170 - (v - lo) / span * 130) for i, (_, v) in enumerate(pts)]
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in xy)
    area = f"{line} L760 220 L0 220 Z"
    hits = "".join(
        f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="7"><title>'
        f'{t[8:10]}/{t[5:7]} {t[11:16]} — {fmt(v, 1)} °C</title></circle>'
        for (x, y), (t, v) in zip(xy, pts))
    lx, ly = xy[-1]

    def label(k: int) -> str:
        t = pts[min(len(pts) - 1, round(k / 6 * (len(pts) - 1)))][0]
        return f"{t[8:10]}/{t[5:7]}" if period != "24h" else f"{t[11:13]}h"

    labels = "".join(f"<span>{label(k)}</span>" for k in range(6)) + "<span>Agora</span>"
    return f"""
<div class="chart"><div class="grid"></div>
  <svg viewBox="0 0 760 220" preserveAspectRatio="none" role="img"
       aria-label="Variação de temperatura nos últimos {period}">
    <defs><linearGradient id="tempArea" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#e96c52" stop-opacity=".28"/>
      <stop offset="100%" stop-color="#e96c52" stop-opacity="0"/></linearGradient></defs>
    <path d="{area}" fill="url(#tempArea)"/>
    <path d="{line}" fill="none" stroke="#e96c52" stroke-width="3" stroke-linecap="round"
          stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    <circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="#e96c52" stroke="#e96c52" stroke-width="3"/>
    {hits}
  </svg>
  <div class="hours">{labels}</div>
</div>"""


UI_BRIDGE = r"""
<script>
(function () {
  try {
    const P = window.parent, D = P.document;
    if (P.__climaStop) P.__climaStop();

    // ---- Tradução dos menus e diálogos do próprio Streamlit ----
    const T = {
      "Share": "Compartilhar", "Rerun": "Executar novamente", "Clear cache": "Limpar cache",
      "Print": "Imprimir", "Record screen": "Gravar tela", "Stop recording": "Parar gravação",
      "Settings": "Configurações", "System": "Sistema", "Light": "Claro", "Dark": "Escuro",
      "Theme": "Tema", "Run on save": "Executar ao salvar", "Wide mode": "Modo amplo",
      "Show app in wider layout": "Exibir o app em layout mais amplo",
      "Automatically updates the app when the underlying code is updated":
        "Atualiza o app automaticamente quando o código é alterado",
      "Choose app theme, colors and fonts": "Escolha o tema, as cores e as fontes do app",
      "Report a bug": "Relatar um erro", "Get help": "Obter ajuda", "About": "Sobre",
      "Deploy": "Implantar", "Developer options": "Opções do desenvolvedor",
      "Close": "Fechar", "Stop": "Parar", "Running...": "Executando...", "Source": "Código-fonte"
    };
    const ROOTS = '[data-testid="stHeader"], [data-testid="stToolbar"], [data-baseweb="popover"], ' +
                  '[data-testid="stMainMenuPopover"], [role="dialog"], [role="menu"], [role="tooltip"]';
    function translate() {
      D.querySelectorAll(ROOTS).forEach(function (root) {
        const w = D.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const nodes = []; let n;
        while ((n = w.nextNode())) nodes.push(n);
        nodes.forEach(function (node) {
          const raw = node.nodeValue, t = raw.trim();
          if (!t) return;
          let out = T[t];
          if (!out && t.indexOf("Made with Streamlit") === 0) out = t.replace("Made with Streamlit", "Feito com Streamlit");
          if (out && out !== t) node.nodeValue = raw.replace(t, out);
        });
        root.querySelectorAll("[aria-label],[title]").forEach(function (el) {
          ["aria-label", "title"].forEach(function (a) {
            const v = el.getAttribute(a);
            if (v && T[v]) el.setAttribute(a, T[v]);
          });
        });
      });
    }

    // ---- Tema real da página (claro/escuro) pela cor de fundo ----
    function theme() {
      const app = D.querySelector(".stApp") || D.body;
      const m = P.getComputedStyle(app).backgroundColor.match(/[\d.]+/g);
      if (!m) return;
      const lum = 0.299 * m[0] + 0.587 * m[1] + 0.114 * m[2];
      const v = lum < 128 ? "dark" : "light";
      if (D.documentElement.getAttribute("data-theme") !== v) D.documentElement.setAttribute("data-theme", v);
    }

    let timer = null;
    const mo = new MutationObserver(function () {
      if (timer) return;
      timer = setTimeout(function () { timer = null; translate(); theme(); }, 60);
    });
    mo.observe(D.body, { childList: true, subtree: true });
    const iv = setInterval(theme, 700);
    translate(); theme();
    P.__climaStop = function () { mo.disconnect(); clearInterval(iv); };
  } catch (e) {}
})();
</script>
"""


def map_html(loc: dict, place: str, acc: str) -> str:
    lat, lon = loc["latitude"], loc["longitude"]
    bbox = f"{lon - 0.06}%2C{lat - 0.04}%2C{lon + 0.06}%2C{lat + 0.04}"
    src = (f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}"
           f"&layer=mapnik&marker={lat}%2C{lon}")
    return f"""
<body style="margin:0;background:transparent">
<div id="box" style="--card:rgba(255,255,255,.95);--ink:#14201b;--sub:#8a9790;--muted:#607068;--line:#e2e8e3;
position:relative;height:288px;border-radius:20px;overflow:hidden;border:1px solid var(--line);
box-shadow:0 8px 28px rgb(0 0 0/10%);font-family:Manrope,system-ui,sans-serif">
  <iframe id="osm" src="{src}" style="border:0;width:100%;height:100%" loading="lazy"></iframe>
  <div style="position:absolute;left:12px;top:12px;background:var(--card);border-radius:12px;
  padding:12px 14px;box-shadow:0 4px 18px rgb(0 0 0/18%)">
    <div style="color:#26a37a;font-size:10px;font-weight:800;letter-spacing:.12em">LOCALIZAÇÃO</div>
    <div style="font-size:14px;font-weight:700;margin-top:4px;color:var(--ink)">{place}</div>
    <div style="font-size:12px;color:var(--sub);margin-top:4px">{coordinates_label(lat, lon)}</div>
  </div>
  <div style="position:absolute;right:10px;bottom:24px;background:var(--card);border-radius:8px;
  padding:6px 10px;font-size:10px;font-weight:600;color:var(--muted)">{acc}</div>
</div>
<script>
try {{  // acompanha o tema real do Streamlit (claro/escuro)
  const doc = window.parent.document;
  const app = doc.querySelector('.stApp') || doc.body;
  const m = window.parent.getComputedStyle(app).backgroundColor.match(/[\\d.]+/g).map(Number);
  if ((0.299 * m[0] + 0.587 * m[1] + 0.114 * m[2]) < 128) {{
    const b = document.getElementById('box').style;
    b.setProperty('--card', 'rgba(23,31,27,.95)'); b.setProperty('--ink', '#e8efeb');
    b.setProperty('--sub', '#8fa097'); b.setProperty('--muted', '#a6b5ad'); b.setProperty('--line', '#27342e');
    document.getElementById('osm').style.filter = 'invert(1) hue-rotate(180deg) brightness(.92) contrast(.9)';
  }}
}} catch (e) {{}}
</script></body>"""


# ----------------------------------------------------------------------------
# Fragmentos com atualização automática
# ----------------------------------------------------------------------------
@st.fragment(run_every=1)
def clock_fragment() -> None:
    ss = st.session_state
    now = location_now(ss.get("data"))
    label = updated_label(ss.get("fetched_at"), ss.get("just_refreshed", False))
    st.markdown(
        f'<div class="upd"><b>Última atualização</b><span>{label} · {now:%H:%M}</span></div>',
        unsafe_allow_html=True)


@st.fragment(run_every=1)
def system_fragment() -> None:
    ss = st.session_state
    remaining = int(ss.get("next_refresh_at", time.time()) - time.time())
    if remaining <= 0:  # hora da próxima leitura: recarrega a página toda
        ss.just_refreshed = False
        st.rerun(scope="app")
    st_ = station_status(ss.get("data"))
    mm, ss_ = divmod(max(remaining, 0), 60)
    lat = f"{st_['latency']} ms" if st_["latency"] is not None else "—"
    st.markdown(
        f"""
<div class="card" style="min-height:18rem;display:flex;flex-direction:column">
  <div class="row"><div><p class="eyebrow">Sistema</p><h2 class="t">Status da estação</h2></div>
    <span class="pill"><span class="dot {'' if st_['online'] else 'off'}"></span>
    {'Online' if st_['online'] else 'Offline'}</span></div>
  <div class="tiles">
    <div class="tile"><span style="font-size:17px">⚡</span><span>Latência</span><b>{lat}</b></div>
    <div class="tile"><span style="font-size:17px">📈</span><span>Sensores</span><b>{st_['sensors']} / 5</b></div>
  </div>
  <div class="next" style="margin-top:auto">
    <div class="l"><span>Próxima leitura</span><b>{mm:02d}:{ss_:02d}</b></div>
    <p>Dados do Open-Meteo e Open-Meteo Air Quality, consultados automaticamente a cada 10 minutos.</p>
  </div>
</div>""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Estado / localização
# ----------------------------------------------------------------------------
def init_state() -> None:
    ss = st.session_state
    ss.setdefault("period", "24h")
    ss.setdefault("dismissed_alert", None)
    ss.setdefault("location", dict(DEFAULT_LOCATION))
    ss.setdefault("location_status", "idle")  # idle | active | unavailable
    ss.setdefault("just_refreshed", False)
    ss.setdefault("geo_results", [])
    ss.setdefault("data", None)
    ss.setdefault("fetched_at", None)
    ss.setdefault("next_refresh_at", time.time())


def request_location() -> None:
    ss = st.session_state
    with st.spinner("Obtendo localização..."):
        pos = locate_by_ip()
        if not pos:
            ss.location_status = "unavailable"
            return
        loc = dict(ss.location)
        loc.update(latitude=pos["latitude"], longitude=pos["longitude"],
                   accuracy=pos["accuracy"], source="ip")
        place = reverse_geocode(loc["latitude"], loc["longitude"])
        if place:
            loc["city"], loc["region"] = place[0], place[1] or loc["region"]
        ss.location, ss.location_status = loc, "active"


def apply_geo_choice() -> None:
    ss = st.session_state
    idx = ss.get("geo_choice")
    if idx is None or idx >= len(ss.geo_results):
        return
    r = ss.geo_results[idx]
    ss.location = {"latitude": r["latitude"], "longitude": r["longitude"],
                   "city": r["name"], "region": r.get("admin1") or r.get("country_code", ""),
                   "accuracy": None, "source": "search"}
    ss.location_status = "active"
    ss.geo_results = []


# ----------------------------------------------------------------------------
# Página
# ----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Clima Urbano", page_icon="⛅", layout="wide")
    init_state()
    ss = st.session_state
    st.markdown(CSS, unsafe_allow_html=True)
    components.html(UI_BRIDGE, height=0)

    if ss.location_status == "idle":
        request_location()
    loc = ss.location

    # Carrega dados reais (cache de 10 min por coordenada)
    failed = False
    try:
        data = load_data(loc["latitude"], loc["longitude"])
        ss.fetched_at = data["fetched_at"]
        ss.next_refresh_at = data["fetched_at"] + REFRESH_SECONDS
    except Exception:
        data, failed = None, True
        ss.next_refresh_at = time.time() + 30  # tenta de novo em 30 s
    ss.data = data

    # Cabeçalho
    st.markdown(
        """<div class="topbar"><div class="logo"><span class="mark">⛅</span>
        <span><b>Clima Urbano</b><small>Estação inteligente</small></span></div>
        <span class="nav-active">Visão geral</span></div>""", unsafe_allow_html=True)

    origin = {"default": "Localização padrão", "ip": "Sua localização",
              "search": "Localização escolhida"}[loc["source"]]
    left, right = st.columns([3, 1.4], vertical_alignment="bottom")
    with left:
        st.markdown(
            f"""<div class="where">📍 {origin} · {loc['city']}, {loc['region']}</div>
            <h1 class="hello">Bem-vindo ao Clima.</h1>
            <p class="lead">Condições ambientais em tempo real para uma cidade mais
            consciente e preparada.</p>""", unsafe_allow_html=True)
    with right:
        c1, c2 = st.columns([1.3, 1], vertical_alignment="center")
        with c1:
            clock_fragment()
        with c2:
            if st.button("🔄 Atualizar", type="primary", use_container_width=True):
                with st.spinner("Atualizando..."):
                    load_data.clear()
                    ss.just_refreshed = True
                st.rerun()

    # Alerta (calculado a partir da previsão horária real)
    if data:
        alert = build_alert(data)
        key = alert["title"] + alert["text"]
        if ss.dismissed_alert != key:
            st.write("")
            a1, a2 = st.columns([30, 1], vertical_alignment="center")
            with a1:
                st.markdown(
                    f"""<div class="alert {'' if alert['rain'] else 'ok'}">
                    <div class="ic">{'🌧️' if alert['rain'] else '🌤️'}</div><div style="flex:1">
                    <b>{alert['title']}</b><span>{alert['text']}</span></div></div>""",
                    unsafe_allow_html=True)
            with a2:
                if st.button("✕", key="close_alert", help="Fechar alerta"):
                    ss.dismissed_alert = key
                    st.rerun()
    elif failed:
        st.error("Não foi possível obter os dados meteorológicos. Nova tentativa em instantes.")

    st.write("")

    # Indicadores
    for col, m in zip(st.columns(4), build_metrics(data, failed)):
        col.markdown(metric_card_html(m), unsafe_allow_html=True)

    st.write("")

    # Histórico + previsão
    hist_col, fc_col = st.columns([1.7, 1])
    with hist_col:
        with st.container(border=True):
            h1, h2 = st.columns([2, 1.2], vertical_alignment="center")
            h1.markdown('<p class="eyebrow">Histórico</p><h2 class="t">Variação de temperatura</h2>',
                        unsafe_allow_html=True)
            ss.period = h2.segmented_control(
                "Período", ["24h", "7d", "30d"], default=ss.period,
                label_visibility="collapsed", key="period_ctl") or ss.period
            if data:
                hist = build_history(data, ss.period)
                st.markdown(
                    f"""<div class="stats">
                    <div><span>Média</span><p>{fmt(hist['avg'], 1)} °C</p></div>
                    <div><span>Máxima</span><p style="color:#e96c52">{fmt(hist['max'], 1)} °C</p></div>
                    <div><span>Mínima</span><p style="color:#4386ca">{fmt(hist['min'], 1)} °C</p></div></div>""",
                    unsafe_allow_html=True)
                st.markdown(temperature_chart_html(hist, ss.period), unsafe_allow_html=True)
            else:
                st.info("Histórico indisponível no momento.")
    with fc_col:
        rows = "".join(
            f"""<div class="fc {'today' if i == 0 else ''}"><span class="d">{f['day']}</span>
            <span style="font-size:20px">{f['icon']}</span><span class="r">💧 {f['rain']}</span>
            <span class="h">{f['high']}</span><span class="l">{f['low']}</span></div>"""
            for i, f in enumerate(build_forecast(data))) if data else \
            '<p style="padding:1rem;color:var(--subtle)">Previsão indisponível.</p>'
        st.markdown(
            f"""<div class="card" style="padding:1.5rem 1rem">
            <div style="padding:0 .5rem .5rem"><p class="eyebrow">Próximos dias</p>
            <h2 class="t">Previsão do tempo</h2></div>{rows}</div>""", unsafe_allow_html=True)

    st.write("")

    # Mapa / ambiente / sistema
    m_col, e_col, s_col = st.columns([1.15, 1, 1])
    with m_col:
        acc = (f"Precisão de ~{round(loc['accuracy'] / 1000)} km (IP)" if loc["accuracy"]
               else "Localização definida")
        components.html(map_html(loc, f"{loc['city']}, {loc['region']}", acc), height=300)

        with st.form("geo_form", clear_on_submit=True, border=False):
            q = st.text_input("Buscar outra cidade", placeholder="Ex.: Campinas",
                              label_visibility="collapsed")
            b1, b2 = st.columns(2)
            search = b1.form_submit_button("🔎 Buscar cidade", use_container_width=True)
            use_ip = b2.form_submit_button("📍 Minha localização", use_container_width=True)
        if search and q.strip():
            ss.geo_results = search_city(q.strip())
            if not ss.geo_results:
                st.caption("Nenhuma cidade encontrada.")
        if use_ip:
            request_location()
            st.rerun()
        if ss.geo_results:
            st.selectbox(
                "Resultados", range(len(ss.geo_results)), key="geo_choice", index=None,
                placeholder="Escolha a cidade", on_change=apply_geo_choice,
                format_func=lambda i: ", ".join(
                    x for x in (ss.geo_results[i]["name"], ss.geo_results[i].get("admin1"),
                                ss.geo_results[i].get("country")) if x))
        if ss.location_status == "unavailable":
            st.caption("Não foi possível obter sua localização pelo IP; mantendo a atual.")

    with e_col:
        env = build_environment(data) if data else None

        def bar(icon, name, value, pct, color):
            return f"""<div class="prog-h"><span>{icon} {name}</span><b>{value}</b></div>
            <div class="track"><i style="width:{pct:.0f}%;background:{color}"></i></div>"""

        body = (
            bar("☀️", "Luminosidade", f"{fmt(env['lux'])} lux", env["lux_pct"], "#e4a72a")
            + bar("🌬️", "Vento", f"{fmt(env['wind'])} km/h", env["wind_pct"], "#4386ca")
            + bar("🌧️", "Chuva hoje", f"{fmt(env['rain'], 1)} mm", env["rain_pct"], "#826cc2")
            + f'<p class="hint">Luminosidade estimada a partir da radiação solar ({fmt(env["rad"])} W/m²).</p>'
        ) if env else '<p style="margin-top:1.5rem;color:var(--subtle)">Dados indisponíveis.</p>'
        st.markdown(
            f"""<div class="card" style="min-height:18rem"><div class="row"><div>
            <p class="eyebrow">Ambiente</p><h2 class="t">Outros indicadores</h2></div>
            <span style="font-size:21px">📈</span></div>{body}</div>""", unsafe_allow_html=True)
    with s_col:
        system_fragment()

    st.markdown(
        """<div class="footer"><p style="margin:0">Clima Urbano · Tecnologia a serviço de cidades mais sustentáveis.</p>
        <p style="margin:0">Projeto acadêmico PD-II · Dados: Open-Meteo e OpenStreetMap</p></div>""",
        unsafe_allow_html=True)


if __name__ == "__main__":
    main()
