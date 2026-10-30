"""Dựng dữ liệu cho giao diện mini app 'Tối ưu năng lượng' (dashboard.html).

Giao diện làm theo thiết kế app-toi-uu-nang-luong.html trong project: thanh lọc, 4 thẻ KPI, biểu đồ 24 giờ,
bảng danh sách có chọn dòng và duyệt. Mọi số liệu lấy từ kết quả của engine, không có số minh họa.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import engine as e

TEMPLATE = Path(__file__).with_name("dashboard.html")
_DOW = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
_EXCL_REASONS = ("L15_NO_DEVICE", "L15_DEVICE", "L16_DEVICE_SHORT", "L17_NO_WINDOW")
_WX_REASONS = ("L13_RAIN", "L13_HEAT")


def tier_prices(inv: pd.DataFrame, bands) -> dict:
    """Đơn giá bình quân theo khung giờ (đ/kWh) từ hóa đơn; chỉ lấy 2 tháng mới nhất của file để không lẫn biểu giá cũ."""
    x = inv.copy()
    x["t"] = e.tag_tier(x["Đơn giá"], bands)
    if "tu_ngay" in x.columns and x["tu_ngay"].notna().any():
        last = x["tu_ngay"].max().to_period("M")
        x = x[x["tu_ngay"].dt.to_period("M") >= last - 1]
    out = {}
    for k in ("TD", "BT", "CD"):
        s = x[x["t"] == k]
        if s.empty:
            out[k] = None
            continue
        w = s["consumeCount"].clip(lower=0)
        out[k] = round(float(np.average(s["Đơn giá"], weights=w) if w.sum() > 0 else s["Đơn giá"].mean()))
    return out


def _f(v, nd=2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _s(v, default=""):
    return default if v is None or pd.isna(v) or str(v).strip() == "" else str(v).strip()


def _row(meta: pd.DataFrame, bts: str, **kw) -> dict:
    """Một dòng của bảng; tỉnh, khu vực, ưu tiên lấy từ bảng trạm."""
    m = meta.loc[bts] if bts in meta.index else None
    g = (lambda k: _s(m.get(k))) if m is not None else (lambda k: "")
    base = {"id": "—", "src": "", "model": "—", "bts": bts, "tinh": g("Tên tỉnh/thành phố"),
            "kv": g("Tên khu vực"), "ut": g("Trạm ưu tiên"), "h": None, "hs": "", "av": None,
            "st": None, "en": None, "ex": 0, "why": "",
            "wx": g("wx_text"), "temp": _f(m.get("temp_max")) if m is not None else None,
            "rain": _f(m.get("rain_max")) if m is not None else None}
    base.update(kw)
    return base


def _why(code: str, meta: pd.DataFrame, bts: str, params: e.Params) -> str:
    """Lý do loại; với thời tiết thì kèm số liệu dự báo và ngưỡng."""
    m = meta.loc[bts] if bts in meta.index else None
    if code == "L13_RAIN" and m is not None:
        return f"Dự báo mưa {m.get('rain_max'):.1f} mm/giờ, vượt ngưỡng {params.rain_mm:g} mm/giờ".replace(".", ",")
    if code == "L13_HEAT" and m is not None:
        return f"Dự báo nhiệt độ {m.get('temp_max'):.1f}°C, vượt ngưỡng {params.temp_c:g}°C".replace(".", ",")
    return e.REASONS.get(code, code)


def _notices(res: dict, params: e.Params, wx_notices: list[dict] | None) -> list[dict]:
    """Danh sách lưu ý gộp lại để hiện trong MỘT chip trên giao diện (không hiện chuỗi lỗi kỹ thuật thô
    ngoài phần 'detail' gập lại). Mỗi mục: {level: 'warn'|'error', text: câu dễ hiểu, detail: chi tiết kỹ thuật hoặc None}."""
    out: list[dict] = []
    k_share = res.get("k_share") or 0
    if k_share > 0.5:
        out.append({"level": "error",
                    "text": f"{k_share:.0%} dòng hóa đơn có đơn giá không khớp dải nào (cao/bình thường/thấp điểm). Kết quả 3 giá sẽ thiếu trạm.",
                    "detail": "Kiểm tra kỳ hóa đơn và bảng 'Dải đơn giá' ở trang Cài đặt."})
    if res.get("period") and not params.invoice_from:
        out.append({"level": "warn",
                    "text": f"Đang dùng toàn bộ hóa đơn kỳ {res['period'][0]} – {res['period'][1]}.",
                    "detail": "Danh sách 3 giá nên lấy từ 2-3 tháng gần nhất: đặt 'Chỉ dùng hóa đơn từ tháng' ở trang Cài đặt."})
    if res.get("devices") is not None:
        rejected = res["rejected"]
        nd = rejected[rejected.ma_ly_do == "L15_NO_DEVICE"].bts.nunique()
        total = nd + dict(res["funnel"]).get("Trạm có thiết bị đủ năng lực", 0)
        if total and nd / total > 0.3:
            reg = res["stations"][res["stations"].bts.isin(rejected[rejected.ma_ly_do == "L15_NO_DEVICE"].bts)]
            top = reg["Tên khu vực"].value_counts().to_dict() if "Tên khu vực" in reg else {}
            out.append({"level": "warn",
                        "text": f"File thiết bị không có {nd:,}/{total:,} trạm đã đạt điều kiện hóa đơn và hạ tầng.",
                        "detail": ("Theo khu vực: " + ", ".join(f"{k} ({v})" for k, v in top.items())) if top
                                  else "Kiểm tra file thiết bị có đủ các khu vực chưa."})
    for m in (wx_notices or []):
        out.append({"level": m.get("level", "warn"), "text": m["text"], "detail": m.get("detail")})
    return out


def build_payload(res: dict, params: e.Params, prices: dict | None, sample: bool = False,
                  wx_notices: list[dict] | None = None) -> dict:
    now = datetime.now(timezone(timedelta(hours=7)))
    stations = res["stations"]
    meta_cols = ["bts", "Tên khu vực", "Tên tỉnh/thành phố", "Trạm ưu tiên", "Thời gian xả Accu (h)",
                 "wx_text", "temp_max", "rain_max"]
    wx_ex = res.get("weather_excluded")
    allst = pd.concat([stations] + ([wx_ex] if wx_ex is not None and len(wx_ex) else []), ignore_index=True)
    meta = allst[[c for c in meta_cols if c in allst.columns]].drop_duplicates("bts").set_index("bts")
    has_wx = any(n == "Qua lọc thời tiết" for n, _ in res["funnel"])

    rows: list[dict] = []
    devices = res["devices"]
    level = "device" if devices is not None else "station"
    if devices is not None:
        for _, d in devices.iterrows():
            rows.append(_row(meta, str(d["bts"]), id=_s(d.get("ma_thiet_bi"), "—"), src=_s(d.get("nguon_ma")),
                             model=_s(d.get("model"), "—"), h=_f(d.get("thoi_gian_xa_h")),
                             hs=_s(d.get("nguon_thoi_gian_xa")), av=_f(d.get("thoi_gian_kha_dung_h")),
                             st=_s(d.get("bat_dau_xa")) or None, en=_s(d.get("ket_thuc_xa")) or None))
        # trạm qua lọc hóa đơn + hạ tầng nhưng bị loại ở bước thiết bị: hiện mờ trong bảng
        rej = res["rejected"]
        rej = rej[rej["ma_ly_do"].isin(_EXCL_REASONS + _WX_REASONS)].drop_duplicates("bts")
        for _, r in rej.iterrows():
            bts = str(r["bts"])
            h = meta.loc[bts, "Thời gian xả Accu (h)"] if bts in meta.index else None
            rows.append(_row(meta, bts, h=_f(h), hs="Trạm (CSDL)", ex=1,
                             why=_why(r["ma_ly_do"], meta, bts, params)))
    else:
        st = stations
        if "bat_dau_xa" not in st.columns:
            st = pd.concat([st, e.compute_timing(st["Thời gian xả Accu (h)"], params)], axis=1)
        for _, d in st.iterrows():
            rows.append(_row(meta, str(d["bts"]), h=_f(d["Thời gian xả Accu (h)"]), hs="Trạm (CSDL)",
                             av=_f(d["thoi_gian_kha_dung_h"]), st=_s(d["bat_dau_xa"]) or None,
                             en=_s(d["ket_thuc_xa"]) or None))
        for _, r in res["rejected"][res["rejected"]["ma_ly_do"].isin(_WX_REASONS)].drop_duplicates("bts").iterrows():
            h = meta.loc[r["bts"], "Thời gian xả Accu (h)"] if r["bts"] in meta.index else None
            rows.append(_row(meta, str(r["bts"]), h=_f(h), hs="Trạm (CSDL)", ex=1,
                             why=_why(r["ma_ly_do"], meta, str(r["bts"]), params)))
    for i, r in enumerate(rows):
        r["i"] = i

    funnel = dict(res["funnel"])
    return {
        "date": f"{_DOW[now.weekday()]}, {now:%d/%m/%Y}",
        "updated": f"{now:%d/%m/%Y %H:%M}",
        "period": f"{res['period'][0]} – {res['period'][1]}" if res.get("period") else None,
        "invoice_from": params.invoice_from,
        "invoice_total": funnel.get("Trạm có trong hóa đơn"),
        "tou_total": funnel.get("Trạm dùng điện 3 giá"),
        "infra_total": funnel.get("Qua lọc hạ tầng và thời gian xả"),
        "prices": prices or {"TD": None, "BT": None, "CD": None},
        "win_start": params.window_start, "win_end": params.end_time,
        "reserve": params.reserve_h, "threshold": params.min_discharge_h,
        "sample": bool(sample), "level": level, "rows": rows,
        "weather": has_wx, "rain_mm": params.rain_mm, "temp_c": params.temp_c,
        "wx_scope": "cả ngày" if params.weather_scope == "day" else "trong khung xả",
        "notices": _notices(res, params, wx_notices),
    }


def render_dashboard(payload: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    data = (json.dumps(payload, ensure_ascii=False)
            .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return html.replace("/*__DATA__*/null", data)


def empty_payload(params: e.Params) -> dict:
    now = datetime.now(timezone(timedelta(hours=7)))
    return {"date": f"{_DOW[now.weekday()]}, {now:%d/%m/%Y}", "updated": f"{now:%d/%m/%Y %H:%M}", "period": None,
            "invoice_from": "", "invoice_total": 0, "tou_total": 0, "infra_total": 0,
            "prices": {"TD": None, "BT": None, "CD": None}, "win_start": params.window_start,
            "win_end": params.end_time, "reserve": params.reserve_h, "threshold": params.min_discharge_h,
            "sample": False, "level": "device", "rows": [], "empty": True, "weather": False, "notices": []}
