# Peak Shaving – danh sách thiết bị cần tác động (bản demo)

Đầu vào là file Excel, đầu ra là danh sách thiết bị cần tác động kèm model và giờ bắt đầu/kết thúc xả.
Toàn bộ bộ lọc là luật cố định (không dùng LLM). Chưa áp dụng thời tiết.

## Thời tiết (bước 1.3, tùy chọn)

Bật ở trang "Cài đặt": "Áp dụng thời tiết (Open-Meteo, cần mạng)". Mặc định tắt để buổi demo không phụ thuộc mạng.
- Trạm nối với phường/xã qua cột `Tên tỉnh/thành phố` và `Tên phường/xã` của CSDL nhà trạm (dùng báo cáo cảnh báo trạm).
- Tọa độ phường/xã lấy từ thư viện `vietnamadminunits` (34 tỉnh sau sáp nhập 2025, có sẵn trong gói, không cần mạng).
- Dự báo mưa (mm/giờ), nhiệt độ, mã thời tiết hôm nay lấy từ `api.open-meteo.com` theo lô 50 vị trí; bộ nhớ đệm 30 phút.
- Loại trạm có dự báo mưa lớn hơn 8 mm/giờ (L13_RAIN) hoặc nhiệt độ lớn hơn 33°C (L13_HEAT); đổi ngưỡng và chọn xét trong khung xả hoặc cả ngày ở trang "Cài đặt".
- Trạm không có dữ liệu thời tiết được giữ lại. Lỗi mạng thì app bỏ qua bước này và báo rõ trên màn hình.
- Cần máy chạy app gọi được `api.open-meteo.com`. Bản Open-Meteo miễn phí dành cho mục đích phi thương mại.

## Giao diện

Hai trang điều hướng ở thanh bên trái (logo + "Peak Shaving" trên cùng): **Dashboard** (nạp file, chạy lọc, xem kết
quả) và **Cài đặt** (toàn bộ tham số lọc). Trong Dashboard: thanh lọc (ngày, kỳ hóa đơn, đơn vị, lưu ý gộp chung
một chip), 4 thẻ KPI, biểu đồ 24 giờ, bảng thiết bị có chọn dòng/sắp xếp/tìm kiếm/lọc trạng thái/đổi số dòng mỗi
trang/xuất CSV, nút Duyệt.
- Mã giao diện: `dashboard.html` (HTML/CSS/JS thuần, không phụ thuộc thư viện ngoài gói sẵn) + `ui.py` (đổ dữ liệu
 từ kết quả lọc, kể cả danh sách lưu ý `notices`). Số liệu đều tính từ dữ liệu nạp vào; ô chưa có số liệu ghi "—"
 kèm lý do, không bịa số.
- Tab mặc định của bảng là "Đề xuất"; dòng loại trừ chỉ hiện khi chọn tab "Loại trừ" (tab "Tất cả" không gồm dòng
 loại trừ).
- Lưu ý (đơn giá lệch dải, kỳ hóa đơn chưa lọc theo tháng, file thiết bị thiếu khu vực, lỗi lấy dự báo thời tiết...)
 gộp vào một chip nhỏ cạnh ô ngày, bấm mở chi tiết; chuỗi lỗi kỹ thuật thô (nếu có) chỉ nằm trong mục "Chi tiết kỹ
 thuật" gập lại, không hiện thẳng ra màn hình.
- Nút "Duyệt (n)" chỉ đánh dấu đã duyệt trong phiên trình duyệt, chưa gửi lệnh xuống tủ nguồn — có nhãn "Demo" và
 chú thích cố định cạnh nút để không ai nhầm là đã tác động thật.
- Trạng thái "Đang chạy ắc quy / Hoàn tất" suy ra từ giờ trên máy đang mở và lịch xả của thiết bị đã duyệt.
- Khung nhúng (`streamlit.components.v1.html`) tự báo chiều cao qua `postMessage` để không có 2 thanh cuộn chồng
 nhau ở ngoài; riêng bảng thiết bị có khung cuộn và tiêu đề cố định (sticky) riêng khi danh sách dài.

### Bản quyền phông chữ và icon

- Phông: **Inter** và **JetBrains Mono**, giấy phép SIL Open Font License 1.1 (OFL), tải qua Google Fonts.
- Icon: vẽ tay dạng SVG theo phong cách [Lucide](https://lucide.dev) (giấy phép ISC), không dùng icon font hay
 emoji đứng thay icon.

## Chạy

```
pip install -r requirements.txt
streamlit run app.py
```

## Đầu vào

1. Hóa đơn điện SAP (.xlsx): cột `btsCode`, `Đơn giá`, `consumeCount`. Có thể chọn nhiều file (nhiều tháng).
2. CSDL phân loại nhóm trạm (.xlsx): tự tìm dòng tiêu đề; cần cột `Mã trạm`, `Phân lớp trạm`, `Trạm ưu tiên`, `Thời gian xả Accu (h)`.
3. Danh sách thiết bị (.xlsx), ví dụ `Report_Detail_Station*.xlsx` (tiêu đề 2 dòng được nhận diện tự động, ô chỉ có khoảng trắng coi là trống). Cột được chọn sẵn: Mã thiết bị cơ điện, Model, Mã hàng hóa Ktts, Mã trạm; tùy chọn: Serial (dùng khi thiếu mã thiết bị), Thời gian xả của thiết bị, Hãng, IP.

## Các bước lọc (mỗi trạm bị loại đều có mã lý do)

| Mã | Điều kiện loại |
|---|---|
| L11_FEW_PRICES | Mặc định: trạm chỉ có 1 đơn giá trong cùng một tháng (điện 1 giá). Trạm 3 giá là trạm có từ 2 đơn giá khác nhau trở lên trong cùng một tháng (đơn giá lệch dưới 1% coi là một; số tối thiểu đổi được ở trang "Cài đặt") |
| L11_NOT3G / L11_ONLY_ONE | Chỉ khi chọn cách nhận diện theo dải đơn giá (skill BO): không có dòng cao/thấp điểm; hoặc chỉ có dòng cao điểm hoặc chỉ thấp điểm (100%) |
| L12_NO_CSDL / L12_CLASS / L12_PRIORITY | Không có trong CSDL / không phải Lớp truy nhập / không phải UT2, UT3 |
| L14_NO_DATA / L14_INVALID / L14_SHORT | Thiếu thời gian xả / lớn hơn 24h (dữ liệu sai) / không vượt ngưỡng (mặc định 3h) |
| L15_NO_DEVICE / L15_DEVICE | Trạm không có trong file thiết bị / thiết bị không đủ năng lực (mã KTTS 320000841, 320002085) |
| L16_DEVICE_SHORT | Thiết bị có thời gian xả riêng không vượt ngưỡng |
| L17_NO_WINDOW | Hết thời gian khả dụng sau khi trừ dự phòng |

## Thời điểm tác động

- Thời gian khả dụng = thời gian xả − dự phòng giữ lại (mặc định 1h), tối đa bằng độ dài cửa sổ xả (17:30–22:30).
- Kết thúc xả = 22:30; bắt đầu xả = 22:30 − thời gian khả dụng.
- Thời gian xả: lấy của thiết bị nếu file thiết bị có giá trị, không thì lấy của trạm trong CSDL (cột "Nguồn thời gian xả" cho biết dùng nguồn nào).
- Mã thiết bị trống thì dùng Serial (cột "Nguồn mã").

## Lưu ý

- Điều kiện thiết bị của trạm: mặc định là có ít nhất một thiết bị đủ năng lực (đã xác nhận); chỉ các thiết bị đủ năng lực của trạm được tác động. Có thể chuyển sang "mọi thiết bị đều đủ năng lực" ở trang "Cài đặt".
- Dải đơn giá nhận diện khung giờ chỉnh được trên giao diện; cần cập nhật khi EVN đổi biểu giá.
- Kiểm thử: `python -m pytest test_engine.py`.

- File thiết bị phải phủ đủ khu vực; app cảnh báo khi hơn 30% trạm đạt điều kiện không có trong file thiết bị.
