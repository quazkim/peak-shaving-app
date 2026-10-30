"""Kiểm thử luật cố định bằng dữ liệu nhỏ tự tạo. Chạy: python -m pytest test_engine.py"""
import io

import pandas as pd

import engine as e


def inv(rows):
    return pd.DataFrame(rows, columns=["bts", "Đơn giá", "consumeCount"])


def test_tier_bands():
    s = pd.Series([1404.0, 2145.96, 3931.2, 2071.4, 3404.2, 5855.8, 3000.0])
    assert e.tag_tier(s, e.DEFAULT_PRICE_BANDS).tolist() == ["TD", "BT", "CD", "TD", "BT", "CD", "K"]


def test_three_tariff_and_only_one_excluded():
    d = inv([("A", 1404, 100), ("A", 2146, 300), ("A", 3931, 80),   # 3 giá
             ("B", 2146, 500),                                       # 1 giá
             ("C", 3931, 90), ("C", 3931.1, 10)])                    # chỉ cao điểm = 100%
    ok, rej = e.step_tou(d, e.DEFAULT_PRICE_BANDS)
    assert ok.bts.tolist() == ["A"]
    assert dict(zip(rej.bts, rej.ma_ly_do)) == {"B": "L11_NOT3G", "C": "L11_ONLY_ONE"}


def test_timing_formula():
    p = e.Params()  # dự phòng 1h, kết thúc 22:30, đầu cửa sổ 17:30
    t = e.compute_timing(pd.Series([3.35, 4.0, 9.0]), p)
    assert t["bat_dau_xa"].tolist() == ["20:09", "19:30", "17:30"]   # 9h bị chặn ở đầu cửa sổ
    assert t["thoi_gian_kha_dung_h"].tolist() == [2.35, 3.0, 5.0]
    assert set(t["ket_thuc_xa"]) == {"22:30"}


def test_infra_filters():
    tou = pd.DataFrame({"bts": list("ABCDEFG")})
    csdl = pd.DataFrame({
        "bts": list("ABCDEF"),
        "Phân lớp trạm": ["Lớp truy nhập", "Lớp truy nhập", "Lớp truy nhập", "Trạm trục", "Lớp truy nhập", "Lớp truy nhập"],
        "Trạm ưu tiên": ["UT3", "UT1", "UT2", "UT2", "UT3", "UT3"],
        "Thời gian xả Accu (h)": [3.5, 5.0, 3.0, 5.0, None, 99.0]})
    ok, rej = e.step_infra(tou, csdl, e.Params())   # ngưỡng > 3h
    assert ok.bts.tolist() == ["A"]
    assert dict(zip(rej.bts, rej.ma_ly_do)) == {
        "B": "L12_PRIORITY", "C": "L14_SHORT", "D": "L12_CLASS",
        "E": "L14_NO_DATA", "F": "L14_INVALID", "G": "L12_NO_CSDL"}


def test_devices_modes():
    st = pd.DataFrame({"bts": ["A", "B", "C"], "Trạm ưu tiên": "UT3", "Phân lớp trạm": "Lớp truy nhập",
                       "Thời gian xả Accu (h)": 4.0})
    dev = pd.DataFrame({"id": ["1", "2", "3", "4"], "m": "M", "k": ["320000841", "320002085", "999", "320000841"],
                        "tram": ["a", "A", "B", "B"]})
    cm = {"device": "id", "model": "m", "code": "k", "station": "tram"}
    p = e.Params(station_mode="all")
    res, rej = e.step_devices(st, dev, cm, p)
    assert sorted(res.ma_thiet_bi) == ["1", "2"]                       # chỉ trạm A (mọi thiết bị đủ năng lực)
    assert dict(zip(rej.bts, rej.ma_ly_do)) == {"B": "L15_DEVICE", "C": "L15_NO_DEVICE"}
    p.station_mode = "any"
    res, _ = e.step_devices(st, dev, cm, p)
    assert sorted(res.ma_thiet_bi) == ["1", "2", "4"]                  # trạm B chỉ lấy thiết bị đủ năng lực


def test_device_serial_fallback_and_own_discharge_time():
    st = pd.DataFrame({"bts": ["A"], "Trạm ưu tiên": "UT3", "Phân lớp trạm": "Lớp truy nhập",
                       "Thời gian xả Accu (h)": 4.0})
    dev = pd.DataFrame({"id": ["D1", None, "D3", "D4"], "sn": ["S1", "S2", "S3", "S4"],
                        "m": "M", "k": "320000841", "tram": "A",
                        "h": ["6.0", None, "2.0", "0.1"]})
    cm = {"device": "id", "serial": "sn", "model": "m", "code": "k", "station": "tram", "avail_h": "h"}
    res, rej = e.step_devices(st, dev, cm, e.Params())      # ngưỡng 3h, dự phòng 1h
    got = res.set_index("ma_thiet_bi")
    assert sorted(got.index) == ["D1", "S2"]                 # D3 (2h) và D4 (0,1h) bị loại
    assert got.loc["S2", "nguon_ma"].startswith("Serial")    # thiếu mã -> dùng serial
    assert got.loc["S2", "nguon_thoi_gian_xa"] == "Trạm (CSDL)" and got.loc["S2", "thoi_gian_xa_h"] == 4.0
    assert got.loc["D1", "nguon_thoi_gian_xa"] == "Thiết bị" and got.loc["D1", "bat_dau_xa"] == "17:30"  # 5h khả dụng = tối đa cửa sổ
    assert (rej.ma_ly_do == "L16_DEVICE_SHORT").any()


def test_no_window_when_threshold_below_reserve():
    st = pd.DataFrame({"bts": ["A"], "Trạm ưu tiên": "UT3", "Phân lớp trạm": "Lớp truy nhập",
                       "Thời gian xả Accu (h)": 4.0})
    dev = pd.DataFrame({"id": ["D1"], "m": "M", "k": "320000841", "tram": "A", "h": ["0.8"]})
    cm = {"device": "id", "model": "m", "code": "k", "station": "tram", "avail_h": "h"}
    res, rej = e.step_devices(st, dev, cm, e.Params(min_discharge_h=0.5))
    assert res.empty and rej.ma_ly_do.tolist() == ["L17_NO_WINDOW"]


def test_invoice_from_and_2024_bands():
    d = pd.DataFrame({"bts": ["A", "A", "A", "A", "A", "A"],
                      "Đơn giá": [1279, 1954, 3579, 1340, 2048, 3752],
                      "consumeCount": [10, 50, 20, 10, 50, 20],
                      "tu_ngay": pd.to_datetime(["2024-03-01"] * 3 + ["2024-11-01"] * 3)})
    assert e.tag_tier(d["Đơn giá"], e.DEFAULT_PRICE_BANDS).tolist() == ["TD", "BT", "CD"] * 2
    csdl = pd.DataFrame({"bts": ["A"], "Phân lớp trạm": "Lớp truy nhập", "Trạm ưu tiên": "UT3",
                         "Thời gian xả Accu (h)": 4.0})
    r = e.run_pipeline(d, csdl, None, None, e.Params(invoice_from="2024-11"))
    assert r["period"] == ("03/2024", "11/2024") and r["k_share"] == 0 and len(r["stations"]) == 1
    assert r["stations"].tong_kwh.iloc[0] == 80          # chỉ tính 3 dòng từ tháng 11


def test_ui_payload_and_render():
    import ui
    st = pd.DataFrame({"bts": ["A", "B"], "Trạm ưu tiên": "UT3", "Phân lớp trạm": "Lớp truy nhập",
                       "Tên khu vực": "Khu vực 1", "Tên tỉnh/thành phố": "Hà Nội", "Thời gian xả Accu (h)": 4.0})
    dev = pd.DataFrame({"id": ["D1", "D2"], "m": "M", "k": ["320000841", "999"], "tram": ["A", "B"]})
    cm = {"device": "id", "model": "m", "code": "k", "station": "tram"}
    p = e.Params()
    dv, rej = e.step_devices(st, dev, cm, p)
    res = {"stations": st, "devices": dv, "rejected": rej, "funnel": [("Trạm có trong hóa đơn", 2)], "period": None}
    pl = ui.build_payload(res, p, {"TD": 1404, "BT": 2146, "CD": 3931})
    ok = [r for r in pl["rows"] if not r["ex"]]
    ex = [r for r in pl["rows"] if r["ex"]]
    assert [r["id"] for r in ok] == ["D1"] and ok[0]["tinh"] == "Hà Nội" and ok[0]["st"] == "19:30"
    assert [r["bts"] for r in ex] == ["B"] and ex[0]["why"]            # trạm B bị loại hiện mờ kèm lý do
    html = ui.render_dashboard(pl)
    assert '"id": "D1"' in html and "/*__DATA__*/" not in html


def _fake_hourly(rain_at_19=0.0, temp_at_19=28.0, code_at_19=3):
    times = [f"2026-10-30T{h:02d}:00" for h in range(24)]
    rain = [0.0] * 24; temp = [25.0] * 24; code = [1] * 24
    rain[19], temp[19], code[19] = rain_at_19, temp_at_19, code_at_19
    rain[3] = 50.0; temp[13] = 38.0                      # ngoài khung xả: chỉ tính khi xét cả ngày
    return {"time": times, "precipitation": rain, "temperature_2m": temp, "weather_code": code}


def test_ward_weather_scope_and_parsing(monkeypatch):
    import weather as w
    monkeypatch.setattr(w, "ward_coords", lambda pairs: pd.DataFrame(
        [{"tinh": t, "phuong": p, "lat": 21.0 + i, "lon": 105.0, } for i, (t, p) in enumerate(pairs)]))
    calls = []

    def get(url):
        calls.append(url)
        return [{"hourly": _fake_hourly(12.0, 30.0, 95)}, {"hourly": _fake_hourly(0.0, 29.0, 3)}]   # Open-Meteo: nhiều vị trí -> danh sách

    pairs = [("Hà Nội", "Phường A"), ("Hà Nội", "Phường B")]
    h0, h1 = w.hours_scope("17:30", "22:30", "window")
    assert (h0, h1) == (17, 23)
    r = w.ward_weather(pairs, h0, h1, get=get).set_index("phuong")
    assert r.loc["Phường A", "rain_max"] == 12.0 and r.loc["Phường A", "wx_text"] == "Dông"
    assert r.loc["Phường B", "rain_max"] == 0.0 and r.loc["Phường B", "temp_max"] == 29.0   # 50 mm lúc 3h bị bỏ qua
    assert len(calls) == 1 and "latitude=21.0000,22.0000" in calls[0]
    day = w.ward_weather(pairs, *w.hours_scope("17:30", "22:30", "day"), get=get).set_index("phuong")
    assert day.loc["Phường B", "rain_max"] == 50.0 and day.loc["Phường B", "temp_max"] == 38.0


def test_fetch_forecast_backs_off_on_429_and_paces_batches(monkeypatch):
    """Lỗi thực tế gặp khi gọi ~2200 phường/xã liên tiếp không nghỉ: Open-Meteo trả 429 (giới hạn
    600 vị trí/phút). fetch_forecast phải lùi lâu hơn khi gặp 429 và giãn cách giữa các lô."""
    import weather as w
    sleeps = []
    monkeypatch.setattr(w.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        if calls["n"] == 1:
            ex = Exception("HTTP 429")
            ex.code = 429
            raise ex
        n = len(url.split("latitude=")[1].split("&")[0].split(","))
        return [{"hourly": {"time": [], "precipitation": [], "temperature_2m": [], "weather_code": []}}
                for _ in range(n)]

    coords = [(10.0, 100.0)] * (w.BATCH + 10)   # 2 lô: 50 + 10
    out = w.fetch_forecast(coords, get=get)
    assert len(out) == len(coords)
    assert any(s >= 20.0 for s in sleeps)          # lùi lâu khi gặp 429 (thử lại thành công ở lần sau)
    assert w.BATCH_DELAY_S in sleeps               # có giãn cách trước lô thứ 2


def test_ward_coords_rejects_cross_province_match_and_applies_alias():
    """Lỗi thực tế gặp trong CSDL: tỉnh ghi 'Đắc Lắc' (lệch chính tả, đúng là 'Đắk Lắk') hoặc tên tỉnh
    trước sáp nhập 2025 ('Phú Yên', đã nhập vào Đắk Lắk) không được thư viện nhận diện. Khi đó thư viện
    KHÔNG báo lỗi mà âm thầm khớp theo tên phường/xã ở một tỉnh bất kỳ trên toàn quốc (ví dụ nhảy sang
    Hà Tĩnh) -> phải chặn lại, không lấy tọa độ sai tỉnh."""
    import weather as w
    co = w.ward_coords([
        ("Đắc Lắc", "Phường Xuân Đài"),   # lệch chính tả -> phải khớp đúng Đắk Lắk qua _PROVINCE_ALIASES
        ("Phú Yên", "Phường Xuân Đài"),   # tên tỉnh cũ không còn tồn tại -> phải bỏ, không lấy nhầm tỉnh khác
    ]).set_index("tinh")
    assert 13.0 < co.loc["Đắc Lắc", "lat"] < 14.0      # đúng vùng duyên hải Đắk Lắk (Phú Yên cũ), không phải Hà Tĩnh (~18)
    assert pd.isna(co.loc["Phú Yên", "lat"])


def test_ward_weather_single_location_is_dict(monkeypatch):
    import weather as w
    monkeypatch.setattr(w, "ward_coords", lambda pairs: pd.DataFrame(
        [{"tinh": "X", "phuong": "Y", "lat": 10.0, "lon": 106.0}]))
    r = w.ward_weather([("X", "Y")], 17, 23, get=lambda url: {"hourly": _fake_hourly(2.0)})   # 1 vị trí -> đối tượng, không phải danh sách
    assert r.rain_max.iloc[0] == 2.0


def test_step_weather_and_pipeline():
    import weather as w
    st = pd.DataFrame({"bts": ["A", "B", "C", "D"], "Tên tỉnh/thành phố": "T", "Tên phường/xã": list("abcd")})
    wx = pd.DataFrame({"bts": ["A", "B", "C"], "rain_max": [9.0, 1.0, 0.0], "temp_max": [30.0, 34.0, 30.0],
                       "wx_code": [95, 1, 1], "wx_text": ["Dông", "Ít mây", "Ít mây"]})
    ok, rej, ex = e.step_weather(st, wx, e.Params())
    assert ok.bts.tolist() == ["C", "D"]                              # D không có dữ liệu thời tiết nên được giữ
    assert dict(zip(rej.bts, rej.ma_ly_do)) == {"A": "L13_RAIN", "B": "L13_HEAT"}
    assert sorted(ex.bts) == ["A", "B"]
    d = pd.DataFrame({"bts": ["A", "B"], "Tên tỉnh/thành phố": "T", "Tên phường/xã": ["a", "b"]})
    ward = pd.DataFrame({"tinh": ["T"], "phuong": ["a"], "rain_max": [1.0], "temp_max": [30.0], "wx_code": [1], "wx_text": ["Ít mây"]})
    assert w.attach(d, ward).set_index("bts").loc["A", "wx_text"] == "Ít mây" and pd.isna(w.attach(d, ward).set_index("bts").loc["B", "rain_max"])


def test_export_excel_writes_all_sheets():
    """Lỗi thực tế gặp khi deploy: pandas mới (3.x/4.x) không còn nhận sheet_name theo vị trí trong
    to_excel(), to_excel(xw, "TenSheet") raise TypeError. export_excel từng gọi theo kiểu này."""
    d = inv([("A", 1404, 100), ("A", 2146, 300), ("A", 3931, 80)])
    csdl = pd.DataFrame({"bts": ["A"], "Phân lớp trạm": "Lớp truy nhập", "Trạm ưu tiên": "UT3",
                        "Thời gian xả Accu (h)": 4.0})
    dev = pd.DataFrame({"id": ["D1"], "m": "M", "k": "320000841", "tram": "A"})
    cm = {"device": "id", "model": "m", "code": "k", "station": "tram"}
    res = e.run_pipeline(d, csdl, dev, cm, e.Params())
    buf = io.BytesIO()
    e.export_excel(res, e.Params(), buf, sample_devices=True)
    sheets = pd.ExcelFile(buf).sheet_names
    assert set(sheets) == {"Thiet_bi_tac_dong", "Tram_dat_dieu_kien", "Tram_bi_loai", "Phễu lọc", "Tham_so"}


def test_price_count_rule():
    """Luật anh Thảo: 1 đơn giá trong tháng = điện 1 giá; từ 2 đơn giá khác nhau trong cùng tháng = nhiều giá."""
    d = pd.DataFrame({
        "bts": ["A", "A", "B", "B", "C", "C", "D", "D", "E"],
        "Đơn giá": [1404.0, 3931.0, 2146.0, 2146.4, 1809.0, 3314.0, 1809.0, 1896.0, 0.0],
        "consumeCount": [100, 80, 500, 400, 300, 90, 300, 90, 0],
        "tu_ngay": pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-01", "2025-01-01",
                                   "2025-01-01", "2025-01-01", "2025-01-01", "2025-02-01", "2025-01-01"])})
    ok, rej = e.step_tou_count(d, e.DEFAULT_PRICE_BANDS, 2, 0.01)
    # A: 2 giá cùng tháng -> giữ; B: 2146 và 2146,4 lệch <1% = 1 giá -> loại;
    # C: 2 giá -> giữ; D: đổi biểu giá giữa 2 tháng (mỗi tháng 1 giá) -> loại; E: không có sản lượng -> loại
    assert sorted(ok.bts) == ["A", "C"]
    assert set(rej.ma_ly_do) == {"L11_FEW_PRICES"} and sorted(rej.bts) == ["B", "D", "E"]
    ok3, _ = e.step_tou_count(d, e.DEFAULT_PRICE_BANDS, 3, 0.01)
    assert len(ok3) == 0
