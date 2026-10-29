"""Open-Meteo 16-day forecast -> WeatherSignal for an origin country. Public, no key (check licence terms for commercial use).
One representative industrial point per country (approximation, documented); unknown country -> ConnectorError."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sourceloop.connectors.base import ConnectorError, WeatherSignal
from sourceloop.connectors.catalog import OPEN_METEO_HOSTS
from sourceloop.net.safe_http import EgressBlocked, SafeHttp

URL = "https://api.open-meteo.com/v1/forecast"
SEVERE_PRECIP_MM = 50.0   # heavy-rain day (24 h total)
SEVERE_WIND_KMH = 60.0    # strong-gale threshold used here (Beaufort 7-8)
POINTS: dict[str, tuple[float, float]] = {  # (lat, lon): main manufacturing region, not a country centroid
    "CN": (31.2, 121.5), "VN": (10.8, 106.7), "MX": (25.7, -100.3), "DE": (48.8, 9.2), "PL": (51.1, 17.0), "TR": (40.8, 29.9),
    "IN": (19.1, 72.9), "US": (35.2, -80.8), "MY": (5.4, 100.3), "TH": (13.7, 100.5), "KR": (37.3, 127.0), "RU": (55.8, 37.6),
    "JP": (35.0, 137.0), "TW": (24.8, 120.9), "ID": (-6.2, 106.8), "CZ": (49.8, 15.5), "HU": (47.5, 19.0), "RO": (45.8, 24.1),
    "BR": (-23.5, -46.6), "IT": (45.5, 9.2), "ES": (41.4, 2.2), "FR": (45.8, 4.8), "GB": (52.5, -1.9), "PH": (14.6, 121.0),
}


class _Daily(BaseModel):
    model_config = ConfigDict(extra="ignore")
    precipitation_sum: list[float | None] = Field(max_length=16)
    wind_speed_10m_max: list[float | None] = Field(max_length=16)


class _Resp(BaseModel):
    model_config = ConfigDict(extra="ignore")
    daily: _Daily


class OpenMeteoWeather:
    def __init__(self, http: SafeHttp | None = None):
        self.http = http or SafeHttp(set(OPEN_METEO_HOSTS), timeout_s=8.0)

    async def signal(self, country: str) -> WeatherSignal:
        pt = POINTS.get(country.upper())
        if pt is None:
            raise ConnectorError("UNSUPPORTED_COUNTRY")
        try:
            r = await self.http.get(URL, params={"latitude": pt[0], "longitude": pt[1], "daily": "precipitation_sum,wind_speed_10m_max",
                                                 "forecast_days": 16, "timezone": "UTC"})
        except EgressBlocked as e:
            raise ConnectorError(f"EGRESS_{e.code}") from None
        except Exception:  # noqa: BLE001
            raise ConnectorError("UNREACHABLE") from None
        if r.status_code != 200:
            raise ConnectorError(f"HTTP_{r.status_code}")
        try:
            d = _Resp.model_validate_json(r.content).daily
        except ValidationError:
            raise ConnectorError("BAD_RESPONSE") from None
        rain = [x for x in d.precipitation_sum if x is not None]
        wind = [x for x in d.wind_speed_10m_max if x is not None]
        n = min(len(d.precipitation_sum), len(d.wind_speed_10m_max))
        if n == 0 or not rain or not wind:
            raise ConnectorError("NO_DATA")  # blank is never "calm weather"
        severe = sum(1 for p, w in zip(d.precipitation_sum, d.wind_speed_10m_max, strict=False)
                     if (p or 0) >= SEVERE_PRECIP_MM or (w or 0) >= SEVERE_WIND_KMH)
        try:
            return WeatherSignal(horizon_days=min(n, 16), max_precip_mm=max(rain), max_wind_kmh=max(wind), severe_days=severe,
                                 source_url=f"{URL}?latitude={pt[0]}&longitude={pt[1]}")
        except ValidationError:
            raise ConnectorError("BAD_RESPONSE") from None
