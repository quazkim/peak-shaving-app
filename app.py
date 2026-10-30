"""Mini app 'Tối ưu năng lượng' (Peak Shaving): nạp file Excel -> lọc -> danh sách thiết bị cần tác động.

Giao diện làm theo thiết kế app-toi-uu-nang-luong.html (xem dashboard.html, ui.py).
Chạy:  streamlit run app.py
Bản demo: chưa áp dụng thời tiết, chưa gửi lệnh xuống tủ nguồn.
"""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import engine as e
import ui
import weather

st.set_page_config(page_title="Tối ưu năng lượng", page_icon="⚡", layout="wide")

st.markdown("""
<style>
:root{--red:#E3102F;--red2:#C8102E;--line:#ECE7E8;--sub:#6B6B73}
header[data-testid="stHeader"],footer,#MainMenu{display:none!important}
.block-container{padding:1.2rem 1.6rem 2rem!important;max-width:100%!important}
section[data-testid="stSidebar"]{background:#FAF8F8;border-right:1px solid var(--line)}
.apphead{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.apphead .ico{width:36px;height:36px;border-radius:9px;background:#FEF3E2;color:#D97706;display:grid;place-items:center;font-size:20px}
.apphead h1{font-size:22px;font-weight:700;margin:0;padding:0;line-height:1.2;color:#1F1F23}
.apphead .sub{color:var(--sub);font-size:13px}
.apphead .badge{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:7px;font-size:12.5px;font-weight:600;background:#E8F7EE;color:#16A34A}
.apphead .badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
div[data-testid="stExpander"]{border:1px solid var(--line);border-radius:12px;background:#fff}
div[data-testid="stExpander"] summary{font-weight:600}
.stButton>button[kind="primary"],.stDownloadButton>button{border-radius:9px;font-weight:500}
.stButton>button[kind="primary"]{background:var(--red);border-color:var(--red)}
.stButton>button[kind="primary"]:hover{background:var(--red2);border-color:var(--red2)}
</style>
<div class="apphead"><span class="ico">⚡</span><div><h1>Tối ưu năng lượng <span class="badge">Active</span></h1>
<div class="sub">P. Cơ điện · Peak Shaving: chọn tủ nguồn DC cần chuyển sang acquy giờ cao điểm</div></div></div>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ đọc file (có cache)
@st.cache_data(show_spinner="Đang đọc hóa đơn SAP...")
def load_invoices(blobs: tuple[bytes, ...]) -> pd.DataFrame:
    return e.read_invoices([io.BytesIO(b) for b in blobs])


@st.cache_data(show_spinner="Đang đọc CSDL nhà trạm...")
def load_csdl(blob: bytes) -> pd.DataFrame:
    return e.read_csdl(io.BytesIO(blob))


@st.cache_data(show_spinner="Đang đọc file thiết bị...")
def load_devices(blobs: tuple[bytes, ...]) -> pd.DataFrame:
    frames = [e.read_devices(io.BytesIO(b)) for b in blobs]
    return pd.concat(frames, ignore_index=True).drop_duplicates() if len(frames) > 1 else frames[0]


@st.cache_data(ttl=86400, show_spinner="Đang lấy dự báo thời tiết (Open-Meteo)...")
def load_weather(pairs: tuple, h0: int, h1: int, day_key: str) -> pd.DataFrame:
    return weather.ward_weather(list(pairs), h0, h1)   # day_key: làm mới bộ nhớ đệm mỗi ngày


def guess(cols: list[str], keys: list[str]) -> int:
    low = [c.lower() for c in cols]
    for k in keys:
        for i, c in enumerate(low):
            if k in c:
                return i
    return 0


# ------------------------------------------------------------------ tham số (thanh bên)
with st.sidebar:
    st.header("Tham số")
    st.subheader("Trạm 3 giá")
    tou_m = st.radio("Cách nhận diện trạm 3 giá",
                     ["Theo số đơn giá khác nhau trong tháng", "Theo dải đơn giá cao/bình thường/thấp điểm"],
                     help="Trạm 1 giá: mọi khung giờ cùng một đơn giá. Trạm 3 giá: trong cùng một tháng có nhiều đơn giá. "
                          "Cách thứ hai là cách của skill BO (cần bảng dải đơn giá ở cuối thanh bên).")
    min_prices = st.number_input("Số đơn giá khác nhau tối thiểu trong một tháng", 2, 6, 2, 1,
                                 disabled=tou_m.startswith("Theo dải"),
                                 help="Hai đơn giá lệch nhau dưới 1% coi là một.")
    st.subheader("Hạ tầng")
    min_h = st.number_input("Thời gian xả ắc quy phải lớn hơn (giờ)", 0.0, 24.0, 3.0, 0.5)
    reserve = st.number_input("Dự phòng giữ lại sau khi xả (giờ)", 0.0, 12.0, 1.0, 0.5)
    end_time = st.text_input("Giờ kết thúc xả (HH:MM)", "22:30")
    win_start = st.text_input("Đầu cửa sổ xả cho phép (HH:MM)", "17:30")
    prios = st.multiselect("Trạm ưu tiên cho phép", ["UT1", "UT2", "UT3"], ["UT2", "UT3"])
    classes = st.multiselect("Phân lớp trạm cho phép",
                             ["Lớp truy nhập", "Tổng trạm huyện", "Tổng trạm tỉnh", "Trạm trục"],
                             ["Lớp truy nhập"])
    inv_from = st.text_input("Chỉ dùng hóa đơn từ tháng (YYYY-MM, để trống = toàn bộ file)", "",
                             help="Hóa đơn nhiều tháng/năm sẽ làm danh sách trạm 3 giá lệch so với hiện tại. "
                                  "Nên dùng 2-3 tháng gần nhất.")
    st.subheader("Thiết bị")
    codes_txt = st.text_area("Mã KTTS đủ năng lực Peak Shaving (mỗi dòng một mã)", "320000841\n320002085")
    mode = st.radio("Điều kiện thiết bị của trạm",
                    ["Có ít nhất một thiết bị đủ năng lực", "Mọi thiết bị của trạm đều đủ năng lực"],
                    help="Mặc định: trạm có ít nhất một thiết bị đủ năng lực thì giữ, và chỉ tác động các thiết bị "
                         "đủ năng lực của trạm đó. Chọn cách còn lại nếu cần loại trạm có thiết bị không đủ năng lực.")
    st.subheader("Thời tiết")
    use_wx = st.checkbox("Áp dụng thời tiết (Open-Meteo, cần mạng)", False,
                         help="Loại trạm có nguy cơ mất điện theo dự báo hôm nay. Tọa độ lấy theo phường/xã trong CSDL "
                              "nhà trạm (cột 'Tên phường/xã'). Lỗi mạng thì app bỏ qua bước này và báo rõ.")
    rain_mm = st.number_input("Loại trạm nếu mưa lớn hơn (mm/giờ)", 0.0, 200.0, 8.0, 1.0)
    temp_c = st.number_input("Loại trạm nếu nhiệt độ lớn hơn (°C)", 0.0, 60.0, 33.0, 1.0)
    wx_scope = st.radio("Khoảng thời gian xét dự báo", ["Trong khung xả", "Cả ngày"],
                        help="Trong khung xả: các giờ từ đầu cửa sổ đến giờ kết thúc xả. Cả ngày: 0h-24h hôm nay.")
    with st.expander("Dải đơn giá nhận diện khung giờ (đồng/kWh)"):
        bands_df = st.data_editor(
            pd.DataFrame(e.DEFAULT_PRICE_BANDS, columns=["Khung", "Từ", "Đến"]),
            num_rows="dynamic", use_container_width=True, hide_index=True)

params = e.Params(
    tou_method="bands" if tou_m.startswith("Theo dải") else "price_count", min_prices=int(min_prices),
    price_bands=[(r.Khung, float(r.Từ), float(r.Đến)) for r in bands_df.itertuples() if pd.notna(r.Từ)],
    classes=tuple(classes), priorities=tuple(prios), min_discharge_h=min_h,
    reserve_h=reserve, window_start=win_start, end_time=end_time, invoice_from=inv_from.strip(),
    rain_mm=rain_mm, temp_c=temp_c, weather_scope="day" if wx_scope == "Cả ngày" else "window",
    capable_codes=tuple(c.strip() for c in codes_txt.splitlines() if c.strip()),
    station_mode="all" if mode.startswith("Mọi") else "any")

# ------------------------------------------------------------------ nạp file
colmap, dev = None, None
with st.expander("Nguồn dữ liệu và chạy lọc", expanded="res" not in st.session_state):
    c1, c2, c3 = st.columns(3)
    with c1:
        f_inv = st.file_uploader("1. Hóa đơn điện SAP (.xlsx, có thể chọn nhiều tháng)", type=["xlsx"],
                                 accept_multiple_files=True)
    with c2:
        f_csdl = st.file_uploader("2. CSDL nhà trạm: CSDL phân loại hoặc báo cáo cảnh báo trạm (.xlsx)",
                                  type=["xlsx"])
    with c3:
        f_dev = st.file_uploader("3. Danh sách thiết bị (.xlsx, có thể chọn nhiều file khu vực)", type=["xlsx"],
                                 accept_multiple_files=True)
        use_sample = st.checkbox("Dùng dữ liệu thiết bị MẪU để thử (không phải dữ liệu thật)")

    if f_dev:
        dev = load_devices(tuple(f.getvalue() for f in f_dev))
        cols = list(dev.columns)
        st.markdown("**Chọn cột trong file thiết bị**")
        none = "(không dùng)"
        m1, m2, m3, m4 = st.columns(4)
        colmap = {
            "device": m1.selectbox("Mã thiết bị", cols, guess(cols, ["mã thiết bị cơ điện", "mã thiết bị", "ma thiet bi", "device"])),
            "model": m2.selectbox("Model", cols, guess(cols, ["model", "loại", "tên thiết bị"])),
            "code": m3.selectbox("Mã KTTS", cols, guess(cols, ["mã hàng hóa ktts", "ktts", "mã vật tư", "mã hàng"])),
            "station": m4.selectbox("Mã trạm", cols, guess(cols, ["mã trạm", "ma tram", "station", "bts"])),
        }
        n1, n2, n3, n4 = st.columns(4)
        opts = [none] + cols

        def pick(label, keys, container):
            i = guess(opts, keys) if any(k in " ".join(opts).lower() for k in keys) else 0
            return container.selectbox(label, opts, i)

        ser = pick("Serial (dùng khi thiếu mã thiết bị)", ["serial"], n1)
        avail = pick("Thời gian xả của thiết bị", ["thời gian xả"], n2)
        hang = pick("Hãng sản xuất (hiển thị)", ["hãng"], n3)
        ip = pick("IP (hiển thị)", ["ip"], n4)
        if ser != none:
            colmap["serial"] = ser
        if avail != none:
            colmap["avail_h"] = avail
        extra = {k: v for k, v in (("hang", hang), ("ip", ip)) if v != none}
        if extra:
            colmap["extra"] = extra
        with st.expander("Xem 5 dòng đầu file thiết bị"):
            st.dataframe(dev.head(), use_container_width=True)

    if not f_dev and use_sample:
        colmap = {"device": "Mã thiết bị", "model": "Model", "code": "Mã KTTS", "station": "Mã trạm"}

    ready = bool(f_inv) and f_csdl is not None
    go = st.button("Chạy lọc", type="primary", disabled=not ready)
    if not ready:
        st.info("Nạp hóa đơn SAP và CSDL nhà trạm để chạy. File thiết bị cần thiết để ra danh sách thiết bị; "
                "nếu chưa có, kết quả dừng ở danh sách trạm.")

if go:
    try:
        inv = load_invoices(tuple(f.getvalue() for f in f_inv))
        csdl = load_csdl(f_csdl.getvalue())
        sample = False
        if dev is None and use_sample:
            pre = e.run_pipeline(inv, csdl, None, None, params)
            dev = e.make_sample_devices(pre["stations"])
            sample = True
        wx_msg: list[str] = []

        def wx_fn(infra: pd.DataFrame):
            if "Tên phường/xã" not in infra.columns or "Tên tỉnh/thành phố" not in infra.columns:
                wx_msg.append("CSDL nhà trạm không có cột 'Tên phường/xã' nên chưa áp dụng được thời tiết. "
                              "Dùng báo cáo cảnh báo trạm làm CSDL nhà trạm.")
                return None
            pairs = tuple(sorted(set(zip(infra["Tên tỉnh/thành phố"].fillna(""), infra["Tên phường/xã"].fillna("")))))
            h0, h1 = weather.hours_scope(params.window_start, params.end_time, params.weather_scope)
            try:
                ward = load_weather(pairs, h0, h1, pd.Timestamp.today().strftime("%Y-%m-%d"))
            except Exception as ex:
                wx_msg.append(f"Không lấy được dự báo thời tiết ({ex}). Kết quả CHƯA áp dụng thời tiết.")
                return None
            miss = int(ward["lat"].isna().sum())
            if miss:
                wx_msg.append(f"{miss}/{len(ward)} phường/xã không có tọa độ nên các trạm đó không bị loại theo thời tiết.")
            return weather.attach(infra, ward)

        res = e.run_pipeline(inv, csdl, dev, colmap if dev is not None else None, params,
                             weather_fn=wx_fn if use_wx else None)
        st.session_state["wx_msg"] = wx_msg
        if res["devices"] is None:
            st_ = res["stations"]
            res["stations"] = pd.concat([st_, e.compute_timing(st_["Thời gian xả Accu (h)"], params)], axis=1)
        prices = ui.tier_prices(inv, params.price_bands)
        st.session_state["res"] = (res, params, sample, prices, bool(dev is not None))
        st.rerun()
    except Exception as ex:  # hiện lỗi dễ hiểu thay vì traceback
        st.session_state.pop("res", None)
        st.error(f"Không chạy được: {ex}")

# ------------------------------------------------------------------ giao diện mini app
if "res" in st.session_state:
    res, used, sample, prices, _ = st.session_state["res"]
    if res.get("k_share", 0) > 0.5:
        st.error(f"{res['k_share']:.0%} dòng hóa đơn có đơn giá không khớp dải nào (cao/bình thường/thấp điểm). "
                 "Kết quả 3 giá sẽ thiếu trạm: kiểm tra kỳ hóa đơn và bảng 'Dải đơn giá' ở thanh bên.")
    if res.get("period") and not used.invoice_from:
        st.warning(f"Đang dùng toàn bộ hóa đơn kỳ {res['period'][0]} - {res['period'][1]}. "
                   "Danh sách 3 giá nên lấy từ 2-3 tháng gần nhất: đặt 'Chỉ dùng hóa đơn từ tháng' ở thanh bên.")
    for m in st.session_state.get("wx_msg", []):
        st.warning(m)
    if res["devices"] is not None:
        nd = res["rejected"][res["rejected"].ma_ly_do == "L15_NO_DEVICE"].bts.nunique()
        total = nd + dict(res["funnel"]).get("Trạm có thiết bị đủ năng lực", 0)
        if total and nd / total > 0.3:
            reg = res["stations"][res["stations"].bts.isin(res["rejected"][res["rejected"].ma_ly_do == "L15_NO_DEVICE"].bts)]
            top = reg["Tên khu vực"].value_counts().to_dict() if "Tên khu vực" in reg else {}
            st.warning(f"File thiết bị không có {nd:,}/{total:,} trạm đã đạt điều kiện hóa đơn và hạ tầng"
                       + (f" (theo khu vực: {top})" if top else "")
                       + ". Kiểm tra file thiết bị có đủ các khu vực chưa.")
    html = ui.render_dashboard(ui.build_payload(res, used, prices, sample))
else:
    res, used, sample = None, params, False
    html = ui.render_dashboard(ui.empty_payload(params))

components.html(html, height=1250, scrolling=True)

if res is not None:
    with st.expander("Chi tiết kỹ thuật: phễu lọc, trạm bị loại, tham số, tải Excel"):
        fcols = st.columns(len(res["funnel"]))
        for col, (name, n) in zip(fcols, res["funnel"]):
            col.metric(name, f"{n:,}")
        tabs = st.tabs(["Trạm đạt điều kiện", "Trạm bị loại", "Tham số đã dùng"])
        with tabs[0]:
            s = res["stations"].drop(columns=["has_3g"], errors="ignore").rename(columns={"bts": "Mã trạm"})
            st.dataframe(s, use_container_width=True, hide_index=True)
        with tabs[1]:
            summ = res["rejected"].groupby(["ma_ly_do", "ly_do"]).size().reset_index(name="Số trạm") \
                .sort_values("Số trạm", ascending=False)
            st.dataframe(summ.rename(columns={"ma_ly_do": "Mã lý do", "ly_do": "Lý do"}),
                         use_container_width=True, hide_index=True)
            with st.expander("Chi tiết từng trạm bị loại"):
                st.dataframe(res["rejected"][["bts", "ma_ly_do", "ly_do"]].rename(columns={"bts": "Mã trạm"}),
                             use_container_width=True, hide_index=True)
        with tabs[2]:
            st.json({k: (list(v) if isinstance(v, tuple) else v) for k, v in used.__dict__.items()})
        buf = io.BytesIO()
        e.export_excel(res, used, buf, sample_devices=sample)
        st.download_button("Tải kết quả Excel", buf.getvalue(), "ket_qua_peak_shaving.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
