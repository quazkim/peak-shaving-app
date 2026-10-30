"""Engine lọc trạm / thiết bị chạy Peak Shaving và tính thời điểm tác động.

Toàn bộ là luật cố định (không dùng LLM). Mỗi trạm/thiết bị bị loại đều có mã lý do.
Các bước theo skill bts-power-audit-analyzer của BO điện + yêu cầu bổ sung của anh Thảo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- cấu hình mặc định
# Dải đơn giá (đồng/kWh, trước VAT) nhận diện khung giờ. Lấy từ skill BO điện.
# Mỗi dải: (khung, thấp, cao). Cần cập nhật khi EVN đổi biểu giá.
DEFAULT_PRICE_BANDS = [
    ("TD", 1390.0, 1420.0),   # thấp điểm - biểu sản xuất / viễn thông
    ("BT", 2135.0, 2165.0),   # bình thường
    ("CD", 3910.0, 3950.0),   # cao điểm
    ("TD", 2066.0, 2076.0),   # thấp điểm - biểu kinh doanh (~2.071)
    ("BT", 3394.0, 3414.0),   # bình thường - biểu kinh doanh (~3.404)
    ("CD", 5846.0, 5866.0),   # cao điểm - biểu kinh doanh (~5.856)
    # Biểu giá năm 2024 (hóa đơn kỳ 2024: 1.279/1.954/3.579 trước 10/2024, 1.340/2.048/3.752 từ 10/2024)
    ("TD", 1270.0, 1290.0), ("BT", 1945.0, 1965.0), ("CD", 3570.0, 3590.0),
    ("TD", 1330.0, 1350.0), ("BT", 2040.0, 2056.0), ("CD", 3745.0, 3760.0),
]

REASONS = {
    "L11_NOT3G": "Không có dòng hóa đơn 3 giá (cao/thấp điểm)",
    "L11_ONLY_ONE": "Chỉ có dòng cao điểm hoặc chỉ thấp điểm (100%)",
    "L11_FEW_PRICES": "Chỉ có 1 đơn giá trong tháng (điện 1 giá)",
    "L12_NO_CSDL": "Mã trạm không có trong CSDL nhà trạm",
    "L12_CLASS": "Phân lớp trạm không phải 'Lớp truy nhập'",
    "L12_PRIORITY": "Trạm ưu tiên không thuộc danh sách cho phép",
    "L13_RAIN": "Dự báo mưa lớn vượt ngưỡng (mm/giờ)",
    "L13_HEAT": "Dự báo nhiệt độ vượt ngưỡng",
    "L14_NO_DATA": "Thiếu thời gian xả ắc quy",
    "L14_INVALID": "Thời gian xả ắc quy bất thường (vượt giá trị tối đa hợp lệ)",
    "L14_SHORT": "Thời gian xả ắc quy không vượt ngưỡng",
    "L15_NO_DEVICE": "Trạm không có thiết bị trong file thiết bị",
    "L15_DEVICE": "Thiết bị của trạm không đủ năng lực Peak Shaving",
    "L16_DEVICE_SHORT": "Thời gian xả của thiết bị không vượt ngưỡng (một số thiết bị của trạm)",
    "L17_NO_WINDOW": "Không còn thời gian khả dụng sau khi trừ dự phòng (một số thiết bị của trạm)",
}


@dataclass
class Params:
    tou_method: str = "price_count"   # "price_count": số đơn giá khác nhau trong cùng tháng (theo anh Thảo); "bands": theo dải đơn giá (skill BO)
    min_prices: int = 2               # trạm có từ ngần này đơn giá khác nhau trong cùng một tháng là trạm nhiều giá
    price_tol: float = 0.01           # hai đơn giá lệch nhau không quá 1% coi là cùng một đơn giá (sai số làm tròn)
    price_bands: list = field(default_factory=lambda: list(DEFAULT_PRICE_BANDS))
    classes: tuple = ("Lớp truy nhập",)
    priorities: tuple = ("UT2", "UT3")
    min_discharge_h: float = 3.0      # thời gian xả ắc quy phải lớn hơn
    max_valid_discharge_h: float = 24.0   # trên mức này coi là dữ liệu sai
    reserve_h: float = 1.0            # dự phòng giữ lại sau khi xả
    window_start: str = "17:30"       # đầu cửa sổ xả cho phép
    end_time: str = "22:30"           # giờ kết thúc xả
    capable_codes: tuple = ("320000841", "320002085")
    station_mode: str = "any"         # "any": có ít nhất một thiết bị đủ năng lực (đã xác nhận); "all": mọi thiết bị đều đủ năng lực
    rain_mm: float = 8.0              # loại trạm nếu dự báo mưa lớn hơn (mm/giờ)
    temp_c: float = 33.0              # loại trạm nếu dự báo nhiệt độ lớn hơn (độ C)
    weather_scope: str = "window"     # "window": trong khung xả; "day": cả ngày
    invoice_from: str = ""            # chỉ dùng dòng hóa đơn có 'Từ ngày' >= tháng này (định dạng YYYY-MM); rỗng = tất cả


# ---------------------------------------------------------------- đọc file
def _read_excel(src, **kw) -> pd.DataFrame:
    try:
        return pd.read_excel(src, engine="calamine", **kw)
    except Exception:
        if hasattr(src, "seek"):
            src.seek(0)
        return pd.read_excel(src, **kw)


def norm_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _find_header_row(src, must: set, scan: int = 15) -> int:
    """Dòng tiêu đề = dòng đầu tiên (trong `scan` dòng đầu) chứa đủ các tên cột trong `must`."""
    if hasattr(src, "seek"):
        src.seek(0)
    head = _read_excel(src, header=None, nrows=scan)
    for i in range(len(head)):
        if must <= {str(v).strip() for v in head.iloc[i].tolist()}:
            return i
    return 0


def read_invoices(sources) -> pd.DataFrame:
    """Đọc 1 hoặc nhiều file hóa đơn SAP (tự tìm dòng tiêu đề: file xuất mới có 2 dòng tiêu đề
    phía trên); ép kiểu số theo lưu ý của skill."""
    frames = []
    for src in sources:
        hdr = _find_header_row(src, {"btsCode", "Đơn giá", "consumeCount"})
        if hasattr(src, "seek"):
            src.seek(0)
        df = _read_excel(src, header=hdr, usecols=lambda c: str(c).strip() in {"btsCode", "Đơn giá", "consumeCount", "Từ ngày"})
        df.columns = [str(c).strip() for c in df.columns]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    need = {"btsCode", "Đơn giá", "consumeCount"}
    miss = need - set(df.columns)
    if miss:
        raise ValueError(f"File hóa đơn thiếu cột: {sorted(miss)}")
    for c in ("Đơn giá", "consumeCount"):
        df[c] = pd.to_numeric(df[c].astype(str).str.strip(), errors="coerce").fillna(0.0)
    df["bts"] = norm_code(df["btsCode"])
    keep = ["bts", "Đơn giá", "consumeCount"]
    if "Từ ngày" in df.columns:   # dùng để biết kỳ hóa đơn và lọc theo tháng
        df["tu_ngay"] = pd.to_datetime(df["Từ ngày"], dayfirst=True, errors="coerce")
        keep.append("tu_ngay")
    return df[keep]


def read_csdl(src) -> pd.DataFrame:
    """CSDL nhà trạm: tự tìm dòng tiêu đề (thường là dòng 6) chứa 'Trạm ưu tiên'."""
    if hasattr(src, "seek"):
        src.seek(0)
    raw = _read_excel(src, header=None)
    hdr = None
    for i in range(min(15, len(raw))):
        vals = raw.iloc[i].astype(str).tolist()
        if "Trạm ưu tiên" in vals and "Phân lớp trạm" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Không tìm thấy dòng tiêu đề (Trạm ưu tiên, Phân lớp trạm) trong CSDL nhà trạm")
    cols = raw.iloc[hdr].astype(str).tolist()
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]   # bỏ cột 'Mã trạm' lặp
    need = ["Mã trạm", "Phân lớp trạm", "Trạm ưu tiên", "Thời gian xả Accu (h)"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"CSDL nhà trạm thiếu cột: {miss}")
    keep = [c for c in ("Mã trạm", "Tên khu vực", "Tên tỉnh/thành phố", "Tên cụm", "Tên phường/xã", "Phân lớp trạm",
                        "Trạm ưu tiên", "Thời gian xả Accu (h)") if c in df.columns]
    df = df[keep].copy()
    df["bts"] = norm_code(df["Mã trạm"])
    df = df.drop(columns=["Mã trạm"])   # chỉ giữ cột chuẩn hóa 'bts', tránh trùng tên cột khi hiển thị
    df["Thời gian xả Accu (h)"] = pd.to_numeric(df["Thời gian xả Accu (h)"], errors="coerce")
    return df.drop_duplicates("bts")


def read_devices(src) -> pd.DataFrame:
    """File thiết bị. Tự nhận diện tiêu đề 2 dòng (nhóm cột + cột con, như Report_Detail_Station)
    và đổi ô chỉ có khoảng trắng thành rỗng."""
    if hasattr(src, "seek"):
        src.seek(0)
    raw = _read_excel(src, header=None, dtype=str)
    # tìm dòng tiêu đề: ô đầu là 'STT' và có cột 'Mã trạm' (file mới có vài dòng tiêu đề báo cáo phía trên)
    h = 0
    for i in range(min(20, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].tolist()]
        if vals and vals[0] == "STT" and "Mã trạm" in vals:
            h = i
            break
    raw = raw.iloc[h:].reset_index(drop=True)
    two_row = (len(raw) > 2 and str(raw.iat[0, 0]).strip() == "STT"
               and raw.iloc[1].isna().mean() > 0.6 and raw.iloc[0].notna().mean() > 0.5)
    if two_row:
        names, group = [], None
        for a, b in zip(raw.iloc[0], raw.iloc[1]):
            if pd.notna(a):
                group = str(a).strip()
            if pd.isna(b):
                names.append(str(a).strip())
            else:
                names.append(f"{group}|{str(b).strip()}")
        body = raw.iloc[2:].copy()
        body.columns = names
    else:
        body = raw.iloc[1:].copy()
        body.columns = [str(c).strip() for c in raw.iloc[0]]
    body = body.loc[:, ~pd.Index(body.columns).duplicated()]
    body = body.apply(lambda s: s.str.strip()).replace("", np.nan)
    return body.reset_index(drop=True)


# ---------------------------------------------------------------- bước 1.1
def tag_tier(prices: pd.Series, bands) -> pd.Series:
    t = pd.Series("K", index=prices.index, dtype=object)
    for name, lo, hi in bands:
        t[(prices >= lo) & (prices <= hi)] = name
    return t


def _tier_table(inv: pd.DataFrame, bands) -> pd.DataFrame:
    """Sản lượng và tỷ lệ theo khung giờ của từng trạm (nhận diện khung theo dải đơn giá)."""
    inv = inv.copy()
    inv["t"] = tag_tier(inv["Đơn giá"], bands)
    piv = inv.pivot_table(index="bts", columns="t", values="consumeCount",
                          aggfunc="sum", fill_value=0.0)
    for k in ("BT", "CD", "TD", "K"):
        if k not in piv:
            piv[k] = 0.0
    has3 = inv.groupby("bts")["t"].apply(lambda s: s.isin(["CD", "TD"]).any())
    piv["has_3g"] = has3.reindex(piv.index).fillna(False)
    piv["tong_kwh"] = piv[["BT", "CD", "TD", "K"]].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        piv["ty_le_cd"] = np.where(piv.tong_kwh > 0, piv.CD / piv.tong_kwh * 100, 0.0)
        piv["ty_le_td"] = np.where(piv.tong_kwh > 0, piv.TD / piv.tong_kwh * 100, 0.0)
    return piv.reset_index()


def step_tou(inv: pd.DataFrame, bands) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cách theo dải đơn giá (skill BO). Trả về (bảng trạm 3 giá hợp lệ, bảng trạm bị loại ở bước 1.1)."""
    piv = _tier_table(inv, bands)
    rej1 = piv[~piv.has_3g][["bts"]].assign(ma_ly_do="L11_NOT3G")
    three = piv[piv.has_3g]
    only1 = three[(three.ty_le_cd >= 99.995) | (three.ty_le_td >= 99.995)]
    rej2 = only1[["bts"]].assign(ma_ly_do="L11_ONLY_ONE")
    ok = three.drop(only1.index)
    return ok, pd.concat([rej1, rej2], ignore_index=True)


def count_prices(inv: pd.DataFrame, tol: float = 0.01) -> pd.Series:
    """Số đơn giá khác nhau của mỗi trạm trong cùng một tháng (lấy tháng nhiều nhất).
    Chỉ tính dòng có sản lượng dương; các đơn giá lệch nhau không quá `tol` (theo tỷ lệ) coi là một."""
    x = inv[inv["consumeCount"] > 0]
    if "tu_ngay" in x.columns and x["tu_ngay"].notna().any():
        month = x["tu_ngay"].dt.to_period("M").astype(str)
    else:
        month = pd.Series("all", index=x.index)
    u = pd.DataFrame({"bts": x["bts"], "m": month, "p": x["Đơn giá"]}).dropna().drop_duplicates()
    u = u.sort_values(["bts", "m", "p"])
    prev = u.groupby(["bts", "m"])["p"].shift()
    u["k"] = (prev.isna() | ((u["p"] - prev) / prev.where(prev > 0) > tol)).astype(int)
    return u.groupby(["bts", "m"])["k"].sum().groupby("bts").max()


def step_tou_count(inv: pd.DataFrame, bands, min_prices: int = 2,
                   tol: float = 0.01) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trạm điện 1 giá: mọi khung giờ cùng một đơn giá. Trạm nhiều giá: trong cùng một tháng có từ
    `min_prices` đơn giá khác nhau trở lên. Bảng kết quả giữ thêm sản lượng theo khung (nhận diện theo dải giá, để tham khảo)."""
    n = count_prices(inv, tol)
    piv = _tier_table(inv, bands)
    piv["so_don_gia"] = piv["bts"].map(n).fillna(0).astype(int)
    ok = piv[piv["so_don_gia"] >= min_prices]
    rej = piv[piv["so_don_gia"] < min_prices][["bts"]].assign(ma_ly_do="L11_FEW_PRICES")
    return ok, rej


# ---------------------------------------------------------------- bước 1.2 + 1.4
def step_infra(tou: pd.DataFrame, csdl: pd.DataFrame, p: Params):
    m = tou.merge(csdl, on="bts", how="left", indicator=True)
    rej = []
    def reject(mask, code):
        if mask.any():
            rej.append(m.loc[mask, ["bts"]].assign(ma_ly_do=code))
    alive = pd.Series(True, index=m.index)

    mask = alive & (m["_merge"] == "left_only"); reject(mask, "L12_NO_CSDL"); alive &= ~mask
    mask = alive & ~m["Phân lớp trạm"].isin(p.classes); reject(mask, "L12_CLASS"); alive &= ~mask
    mask = alive & ~m["Trạm ưu tiên"].isin(p.priorities); reject(mask, "L12_PRIORITY"); alive &= ~mask
    h = m["Thời gian xả Accu (h)"]
    mask = alive & h.isna(); reject(mask, "L14_NO_DATA"); alive &= ~mask
    mask = alive & (h > p.max_valid_discharge_h); reject(mask, "L14_INVALID"); alive &= ~mask
    mask = alive & ~(h > p.min_discharge_h); reject(mask, "L14_SHORT"); alive &= ~mask
    ok = m[alive].drop(columns="_merge")
    return ok, (pd.concat(rej, ignore_index=True) if rej else pd.DataFrame(columns=["bts", "ma_ly_do"]))


# ---------------------------------------------------------------- bước 1.3: thời tiết
def step_weather(stations: pd.DataFrame, wx: pd.DataFrame, p: Params):
    """Loại trạm có dự báo mưa hoặc nhiệt độ vượt ngưỡng. Trạm không có dữ liệu thời tiết được giữ lại
    (chỉ loại khi có số liệu vượt ngưỡng). Trả về (trạm giữ lại, bảng loại, bảng trạm bị loại kèm thông tin)."""
    m = stations.merge(wx, on="bts", how="left")
    rain = m["rain_max"] > p.rain_mm
    heat = ~rain & (m["temp_max"] > p.temp_c)
    rej = pd.concat([m.loc[rain, ["bts"]].assign(ma_ly_do="L13_RAIN"),
                     m.loc[heat, ["bts"]].assign(ma_ly_do="L13_HEAT")], ignore_index=True)
    return m[~(rain | heat)].copy(), rej, m[rain | heat].copy()


# ---------------------------------------------------------------- bước 1.5 + thời điểm tác động
def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_hm(minutes: float) -> str:
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def compute_timing(avail_h: pd.Series, p: Params) -> pd.DataFrame:
    """Thời gian khả dụng = thời gian xả - dự phòng; bắt đầu = giờ kết thúc - thời gian khả dụng.
    Không để bắt đầu sớm hơn đầu cửa sổ xả cho phép."""
    end = _hm(p.end_time)
    win = end - _hm(p.window_start)
    usable_min = ((avail_h - p.reserve_h).clip(lower=0) * 60).clip(upper=win)
    out = pd.DataFrame(index=avail_h.index)
    out["thoi_gian_kha_dung_h"] = (usable_min / 60).round(2)
    out["bat_dau_xa"] = [fmt_hm(end - u) for u in usable_min]
    out["ket_thuc_xa"] = fmt_hm(end)
    return out


def step_devices(stations: pd.DataFrame, dev: pd.DataFrame, colmap: dict, p: Params):
    """dev: bảng thiết bị. colmap: {'device':..., 'model':..., 'station':..., 'code':..., 'avail_h':(tùy chọn)}."""
    d = dev.copy()
    d["bts"] = norm_code(d[colmap["station"]])
    dev_id = d[colmap["device"]].astype("string").str.strip()
    d["nguon_ma"] = "Mã thiết bị"
    if colmap.get("serial"):
        ser = d[colmap["serial"]].astype("string").str.strip()
        miss = dev_id.isna() | (dev_id == "")
        dev_id = dev_id.where(~miss, ser)
        d.loc[miss, "nguon_ma"] = "Serial (thiếu mã thiết bị)"
    d["ma_thiet_bi"] = dev_id.fillna("")
    d["model"] = d[colmap["model"]].astype(str).str.strip() if colmap.get("model") else ""
    d["ma_ktts"] = d[colmap["code"]].astype(str).str.strip()
    d["capable"] = d["ma_ktts"].isin([c.strip() for c in p.capable_codes])
    if colmap.get("avail_h"):
        d["xa_tb_h"] = pd.to_numeric(d[colmap["avail_h"]], errors="coerce")
    for key, col in (colmap.get("extra") or {}).items():
        d[key] = d[col]

    by = d.groupby("bts")["capable"].agg(all_ok="all", any_ok="any")
    st = stations.merge(by, left_on="bts", right_index=True, how="left")
    rej = []
    mask = st["all_ok"].isna()
    if mask.any():
        rej.append(st.loc[mask, ["bts"]].assign(ma_ly_do="L15_NO_DEVICE"))
    ok_flag = st["all_ok"] if p.station_mode == "all" else st["any_ok"]
    mask = ~st["all_ok"].isna() & ~ok_flag.fillna(False).astype(bool)
    if mask.any():
        rej.append(st.loc[mask, ["bts"]].assign(ma_ly_do="L15_DEVICE"))
    keep = st[ok_flag.fillna(False).astype(bool)]

    dd = d[d.bts.isin(keep.bts)]
    if p.station_mode == "any":
        dd = dd[dd.capable]
    res = dd.merge(keep.drop(columns=["all_ok", "any_ok"]), on="bts", how="left")

    # thời gian xả dùng để tính: của thiết bị nếu có, không thì của trạm (CSDL)
    st_h = res["Thời gian xả Accu (h)"]
    if colmap.get("avail_h"):
        own = res["xa_tb_h"]
        res["thoi_gian_xa_h"] = own.where(own.notna(), st_h)
        res["nguon_thoi_gian_xa"] = np.where(own.notna(), "Thiết bị", "Trạm (CSDL)")
    else:
        res["thoi_gian_xa_h"] = st_h
        res["nguon_thoi_gian_xa"] = "Trạm (CSDL)"

    # thiết bị có thời gian xả riêng không vượt ngưỡng thì không tác động
    short = ~(res["thoi_gian_xa_h"] > p.min_discharge_h)
    if short.any():
        rej.append(res.loc[short, ["bts"]].drop_duplicates().assign(ma_ly_do="L16_DEVICE_SHORT"))
    res = res[~short].copy()
    timing = compute_timing(res["thoi_gian_xa_h"], p)
    res = pd.concat([res, timing], axis=1)
    nowin = res["thoi_gian_kha_dung_h"] <= 0
    if nowin.any():
        rej.append(res.loc[nowin, ["bts"]].drop_duplicates().assign(ma_ly_do="L17_NO_WINDOW"))
    res = res[~nowin].copy()
    return res.reset_index(drop=True), (pd.concat(rej, ignore_index=True) if rej else pd.DataFrame(columns=["bts", "ma_ly_do"]))


# ---------------------------------------------------------------- chạy toàn luồng
def run_pipeline(inv: pd.DataFrame, csdl: pd.DataFrame, dev: pd.DataFrame | None,
                 colmap: dict | None, p: Params, weather_fn=None) -> dict:
    """weather_fn(trạm đã qua lọc hạ tầng) -> bảng bts, rain_max, temp_max, wx_code, wx_text; None = bỏ qua thời tiết."""
    period = None
    if "tu_ngay" in inv.columns and inv["tu_ngay"].notna().any():
        period = (inv["tu_ngay"].min().strftime("%m/%Y"), inv["tu_ngay"].max().strftime("%m/%Y"))
        if p.invoice_from:
            inv = inv[inv["tu_ngay"] >= pd.Period(p.invoice_from, "M").start_time]
            if inv.empty:
                raise ValueError(f"Không có dòng hóa đơn nào từ {p.invoice_from}. Kỳ hóa đơn trong file: "
                                 f"{period[0]} - {period[1]}")
    if p.tou_method == "bands":
        k_share = float((tag_tier(inv["Đơn giá"], p.price_bands) == "K").mean())
        tou, rej11 = step_tou(inv, p.price_bands)
    else:
        k_share = 0.0   # cách đếm đơn giá không phụ thuộc dải giá
        tou, rej11 = step_tou_count(inv, p.price_bands, p.min_prices, p.price_tol)
    infra, rej_infra = step_infra(tou, csdl, p)
    funnel = [("Trạm có trong hóa đơn", inv["bts"].nunique()),
              ("Trạm dùng điện 3 giá", len(tou)),
              ("Qua lọc hạ tầng và thời gian xả", len(infra))]
    rejected = [rej11, rej_infra]
    wx_excluded = None
    if weather_fn is not None and len(infra):
        wx = weather_fn(infra)
        if wx is not None:
            infra, rej_w, wx_excluded = step_weather(infra, wx, p)
            rejected.append(rej_w)
            funnel.append(("Qua lọc thời tiết", len(infra)))
    devices = None
    if dev is not None and colmap:
        devices, rej15 = step_devices(infra, dev, colmap, p)
        rejected.append(rej15)
        funnel.append(("Trạm có thiết bị đủ năng lực", devices["bts"].nunique()))
        funnel.append(("Thiết bị cần tác động", len(devices)))
    rej = pd.concat(rejected, ignore_index=True)
    rej["ly_do"] = rej["ma_ly_do"].map(REASONS)
    return {"tou": tou, "stations": infra, "devices": devices, "rejected": rej, "funnel": funnel,
            "period": period, "k_share": k_share, "weather_excluded": wx_excluded}


# ---------------------------------------------------------------- dữ liệu thiết bị mẫu (để thử)
def make_sample_devices(stations: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    """Sinh danh sách thiết bị GIẢ LẬP để chạy thử giao diện. KHÔNG phải dữ liệu thật."""
    rng = np.random.default_rng(seed)
    rows = []
    for b in stations["bts"]:
        for i in range(int(rng.integers(1, 3))):
            code = rng.choice(["320000841", "320002085", "320009999"], p=[0.45, 0.35, 0.20])
            model = {"320000841": "MAU_THIET_BI_A", "320002085": "MAU_THIET_BI_B"}.get(code, "MAU_THIET_BI_KHAC")
            rows.append({"Mã thiết bị": f"MAU-{b}-{i + 1}", "Model": model, "Mã KTTS": code, "Mã trạm": b})
    return pd.DataFrame(rows)


def export_excel(result: dict, params: Params, path_or_buf, sample_devices: bool = False):
    stations = result["stations"].copy()
    devices = result["devices"]
    with pd.ExcelWriter(path_or_buf, engine="xlsxwriter") as xw:
        if devices is not None:
            cols = {"ma_thiet_bi": "Mã thiết bị", "nguon_ma": "Nguồn mã", "model": "Model", "ma_ktts": "Mã KTTS",
                    "bts": "Mã trạm", "Trạm ưu tiên": "Trạm ưu tiên", "Phân lớp trạm": "Phân lớp trạm",
                    "thoi_gian_xa_h": "Thời gian xả ắc quy (h)", "nguon_thoi_gian_xa": "Nguồn thời gian xả",
                    "thoi_gian_kha_dung_h": "Thời gian khả dụng (h)", "bat_dau_xa": "Bắt đầu xả",
                    "ket_thuc_xa": "Kết thúc xả", "hang": "Hãng", "ip": "IP",
                    "CD": "Sản lượng cao điểm tháng (kWh)", "wx_text": "Thời tiết dự báo",
                    "rain_max": "Mưa tối đa (mm/giờ)", "temp_max": "Nhiệt độ tối đa (°C)"}
            out = devices[[c for c in cols if c in devices.columns]].rename(columns=cols)
            out.to_excel(xw, "Thiet_bi_tac_dong", index=False)
        stations.rename(columns={"bts": "Mã trạm", "BT": "kWh bình thường", "CD": "kWh cao điểm",
                                 "TD": "kWh thấp điểm", "K": "kWh khác", "tong_kwh": "Tổng kWh",
                                 "ty_le_cd": "Tỷ lệ cao điểm (%)", "ty_le_td": "Tỷ lệ thấp điểm (%)"}
                       ).drop(columns=["has_3g"], errors="ignore").to_excel(xw, "Tram_dat_dieu_kien", index=False)
        result["rejected"][["bts", "ma_ly_do", "ly_do"]].rename(
            columns={"bts": "Mã trạm", "ma_ly_do": "Mã lý do", "ly_do": "Lý do loại"}
        ).to_excel(xw, "Tram_bi_loai", index=False)
        pd.DataFrame(result["funnel"], columns=["Bước", "Số lượng"]).to_excel(xw, "Phễu lọc", index=False)
        info = {
            "Ngưỡng thời gian xả ắc quy (h, lớn hơn)": p_val(params.min_discharge_h),
            "Dự phòng giữ lại sau xả (h)": p_val(params.reserve_h),
            "Giờ kết thúc xả": params.end_time,
            "Đầu cửa sổ xả cho phép": params.window_start,
            "Trạm ưu tiên cho phép": ", ".join(params.priorities),
            "Phân lớp cho phép": ", ".join(params.classes),
            "Mã KTTS đủ năng lực": ", ".join(params.capable_codes),
            "Điều kiện thiết bị của trạm": "Mọi thiết bị đủ năng lực" if params.station_mode == "all" else "Có ít nhất một thiết bị đủ năng lực",
            "Nhận diện trạm 3 giá": ("Từ %d đơn giá khác nhau trở lên trong cùng một tháng (lệch dưới %g%% coi là một)" % (
                params.min_prices, params.price_tol * 100)) if params.tou_method == "price_count"
                else "Theo dải đơn giá cao/bình thường/thấp điểm (skill BO)",
            "Dữ liệu thiết bị": "MẪU GIẢ LẬP - không phải dữ liệu thật" if sample_devices else "File người dùng cung cấp",
            "Thời tiết": ("Đã áp dụng (Open-Meteo): loại nếu mưa > %s mm/giờ hoặc nhiệt độ > %s°C, %s" % (
                p_val(params.rain_mm), p_val(params.temp_c),
                "cả ngày" if params.weather_scope == "day" else "trong khung xả"))
            if result["funnel"] and any(n == "Qua lọc thời tiết" for n, _ in result["funnel"])
            else "Chưa áp dụng",
        }
        pd.DataFrame(list(info.items()), columns=["Tham số", "Giá trị"]).to_excel(xw, "Tham_so", index=False)
        for ws in xw.sheets.values():
            ws.freeze_panes(1, 0)
            ws.set_column(0, 12, 22)


def p_val(x):
    return float(x)
