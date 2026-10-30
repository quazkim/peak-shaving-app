"""Thời tiết cho bước 1.3 (loại trạm có nguy cơ mất điện): mưa và nhiệt độ dự báo từ Open-Meteo.

- Trạm -> phường/xã (cột 'Tên tỉnh/thành phố', 'Tên phường/xã' trong báo cáo cảnh báo trạm).
- Phường/xã -> tọa độ: thư viện vietnamadminunits (34 tỉnh sau sáp nhập 2025, dữ liệu có sẵn trong gói).
- Dự báo theo giờ cho hôm nay: precipitation (mm), temperature_2m (độ C), weather_code.
Chỉ gọi mạng khi người dùng bật; lỗi mạng không làm hỏng luồng lọc (app hiện cảnh báo và chạy tiếp).
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

import pandas as pd

API = "https://api.open-meteo.com/v1/forecast"
BATCH = 50

# WMO weather_code -> mô tả ngắn
_WMO = [
    (range(0, 1), "Quang"), (range(1, 3), "Ít mây"), (range(3, 4), "Nhiều mây"), (range(45, 49), "Sương mù"),
    (range(51, 58), "Mưa phùn"), (range(61, 68), "Mưa"), (range(71, 78), "Tuyết"), (range(80, 83), "Mưa rào"),
    (range(85, 87), "Tuyết rào"), (range(95, 100), "Dông"),
]


def wmo_text(code) -> str:
    if code is None or pd.isna(code):
        return ""
    c = int(code)
    for r, t in _WMO:
        if c in r:
            return t
    return "Nhiều mây"


def hours_scope(window_start: str, end_time: str, scope: str) -> tuple[int, int]:
    """Khoảng giờ (đầu, cuối, cuối không tính) cần xét. scope: 'window' = khung xả, 'day' = cả ngày."""
    if scope == "day":
        return 0, 24
    a = int(window_start.split(":")[0])
    h, m = (int(x) for x in end_time.split(":"))
    b = h + (1 if m > 0 else 0)
    return a, min(max(b, a + 1), 24)


# Tên tỉnh lệch chính tả còn gặp trong CSDL nội bộ (chữ "c" thay cho "k") -> tên thư viện nhận diện được.
_PROVINCE_ALIASES = {"đắc lắc": "Đắk Lắk"}


def ward_coords(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """pairs: [(tỉnh, phường/xã)] -> DataFrame tinh, phuong, lat, lon (NaN nếu không khớp).

    Luôn kiểm tra tỉnh suy ra từ phường/xã có khớp tỉnh đầu vào: nếu không nhận diện được tỉnh
    (tên cũ trước sáp nhập, lệch chính tả...), thư viện không báo lỗi mà âm thầm khớp theo tên
    phường/xã trên toàn quốc, có thể ra tọa độ ở một tỉnh hoàn toàn khác.
    """
    try:
        from vietnamadminunits import ParseMode, parse_address
    except ImportError as ex:  # thư viện chưa cài
        raise RuntimeError("Chưa cài thư viện vietnamadminunits (pip install vietnamadminunits)") from ex
    rows = []
    for tinh, phuong in pairs:
        lat = lon = None
        if tinh and phuong:
            tinh_chuan = _PROVINCE_ALIASES.get(tinh.strip().lower(), tinh)
            want = parse_address(tinh_chuan, mode=ParseMode.FROM_2025).province_key
            u = parse_address(f"{phuong}, {tinh_chuan}", mode=ParseMode.FROM_2025)
            if u.ward and u.latitude is not None and want and u.province_key == want:
                lat, lon = float(u.latitude), float(u.longitude)
        rows.append({"tinh": tinh, "phuong": phuong, "lat": lat, "lon": lon})
    return pd.DataFrame(rows)


# Bản miễn phí Open-Meteo giới hạn 600 vị trí/phút (mỗi vị trí trong 1 lệnh gọi nhiều-vị-trí tính
# như một lệnh gọi riêng) - giãn cách giữa các lô để không vượt ngưỡng này.
BATCH_DELAY_S = 6.0


class _HTTPStatusError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _ssl_context():
    """Một số bản Python (ví dụ cài từ python.org trên macOS) không có sẵn kho chứng chỉ CA của hệ điều hành,
    làm urlopen báo lỗi CERTIFICATE_VERIFY_FAILED dù mạng và URL đều ổn. Dùng kho chứng chỉ của gói certifi
    khi có, để không phụ thuộc vào việc máy đã cài đặt CA hệ thống đúng cách hay chưa."""
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


_SSL_CTX = _ssl_context()


def _http_get(url: str, timeout: int = 25) -> list | dict:
    req = urllib.request.Request(url, headers={"User-Agent": "peak-shaving-demo"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:  # noqa: S310 (URL cố định, https)
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        raise _HTTPStatusError(ex.code, f"HTTP {ex.code}") from ex


def fetch_forecast(coords: list[tuple[float, float]], get: Callable[[str], list | dict] | None = None) -> list[dict]:
    """Gọi Open-Meteo theo lô, có giãn cách giữa các lô; trả về list 'hourly' (cùng thứ tự với coords)."""
    get = get or _http_get
    out: list[dict] = []
    for i in range(0, len(coords), BATCH):
        if i:
            time.sleep(BATCH_DELAY_S)
        chunk = coords[i:i + BATCH]
        q = {"latitude": ",".join(f"{c[0]:.4f}" for c in chunk), "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
             "hourly": "precipitation,temperature_2m,weather_code", "timezone": "Asia/Bangkok", "forecast_days": 1}
        url = f"{API}?{urllib.parse.urlencode(q, safe=',')}"
        last = None
        for attempt in range(4):
            try:
                data = get(url)
                break
            except Exception as ex:  # lỗi mạng hoặc vượt giới hạn tần suất: thử lại, chờ lâu hơn nếu bị chặn (429)
                last = ex
                wait = 20.0 * (attempt + 1) if getattr(ex, "code", None) == 429 else 1.5 * (attempt + 1)
                time.sleep(wait)
        else:
            raise RuntimeError(f"Không gọi được Open-Meteo: {last}")
        items = data if isinstance(data, list) else [data]
        if len(items) != len(chunk):
            raise RuntimeError("Open-Meteo trả số vị trí không khớp số vị trí đã gửi")
        out.extend(it["hourly"] for it in items)
    return out


def ward_weather(pairs: list[tuple[str, str]], h0: int, h1: int,
                 get: Callable[[str], list | dict] | None = None) -> pd.DataFrame:
    """Mỗi (tỉnh, phường/xã) -> mưa tối đa (mm/giờ), nhiệt độ tối đa, mã thời tiết nặng nhất trong khoảng giờ [h0, h1)."""
    pairs = sorted(set(pairs))
    co = ward_coords(pairs)
    ok = co.lat.notna()
    res = co.copy()
    for c in ("rain_max", "temp_max", "wx_code"):
        res[c] = math.nan
    if ok.any():
        hourly = fetch_forecast(list(zip(co.loc[ok, "lat"], co.loc[ok, "lon"])), get)
        for idx, h in zip(co.index[ok], hourly):
            t = pd.to_datetime(pd.Series(h["time"]))
            sel = ((t.dt.hour >= h0) & (t.dt.hour < h1)).to_numpy()
            def pick(key):
                s = pd.to_numeric(pd.Series(h.get(key, [])), errors="coerce")
                return s[sel[: len(s)]]
            rain, temp, code = pick("precipitation"), pick("temperature_2m"), pick("weather_code")
            res.loc[idx, "rain_max"] = rain.max() if len(rain.dropna()) else math.nan
            res.loc[idx, "temp_max"] = temp.max() if len(temp.dropna()) else math.nan
            res.loc[idx, "wx_code"] = code.max() if len(code.dropna()) else math.nan
    res["wx_text"] = res["wx_code"].map(wmo_text)
    return res


def attach(stations: pd.DataFrame, ward_df: pd.DataFrame) -> pd.DataFrame:
    """Gắn mưa/nhiệt độ của phường/xã vào bảng trạm (cần cột 'Tên tỉnh/thành phố', 'Tên phường/xã')."""
    s = stations[["bts", "Tên tỉnh/thành phố", "Tên phường/xã"]].rename(
        columns={"Tên tỉnh/thành phố": "tinh", "Tên phường/xã": "phuong"})
    m = s.merge(ward_df, on=["tinh", "phuong"], how="left")
    return m[["bts", "rain_max", "temp_max", "wx_code", "wx_text"]].drop_duplicates("bts")
