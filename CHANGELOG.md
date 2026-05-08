# Nhật Ký Thay Đổi

Ghi chép thay đổi dành cho người chơi. Mục mới ở trên cùng. Định dạng ngày `YYYY-MM-DD`.

## [Chưa Phát Hành] — 2026-05-08

### Sửa Lỗi & Cải Tiến (Bản Vá Buổi Tối)

#### Luyện Đan

- **Sửa lỗi không luyện được Triều Nguyên Đan**: trước đây chọn đan phương
  này có lúc bot đứng yên không phản hồi do dữ liệu thảo dược cũ còn sót
  trong hệ thống. Đã dọn sạch — Triều Nguyên Đan và mọi đan phương khác
  giờ luyện được bình thường.
- **Đan Phương hệ Quang**: tên các đan phương / đan dược / nguyên liệu
  hệ Quang trước đây bị lẫn lộn giữa "Quang" (hiển thị) và "Dương" (mã
  nội bộ), một số nơi hiển thị nhầm thành chuỗi mã (ví dụ
  *DanPhuongDuongBiCao*). Đã thống nhất hết về **Quang** trên toàn bộ
  đan phương, đan dược, nguyên liệu yêu thú, và **Ngọc Quang** (Hoàng /
  Huyền / Địa / Thiên Phẩm). Đồ trong túi từ phiên bản cũ vẫn dùng
  được — hệ thống tự nhận diện.

#### Nguyên Liệu & Túi Đồ

- **Gộp Nguyên Liệu Đột Phá Cơ Thể vào "Nguyên Liệu Thể Chất"**:
  Huyết Tinh / Bì Phách / Cân Nguyên Thạch / Cốt Hoa / Ngũ Tạng Tinh /
  Pháp Tướng Kinh / Kim Thân Đan / Siêu Phàm Tinh / Đại Thừa Kinh /
  Thái Dương Tinh / Thái Âm Tinh giờ nằm chung danh mục **Nguyên Liệu
  Thể Chất** trong túi đồ. Đột phá Luyện Thể vẫn tiêu các vật phẩm này
  bình thường — chỉ là vị trí trong túi gọn hơn, không còn danh mục riêng.
- **Dọn vật phẩm rớt vô nghĩa**: 9 nguyên liệu cũ không còn dùng vào việc
  gì (Chân Linh Ngọc, Khí Tụ Đan, Trúc Cơ Đan dạng nguyên liệu, Kim Đan
  Nguyên, Nguyên Anh Thạch, Hóa Thần Đan dạng nguyên liệu, Hư Không Tinh,
  Hợp Đạo Ngọc, Đăng Tiên Phù) đã được gỡ khỏi mọi bảng rớt rương / boss
  / vùng săn. Hộp loot không còn nhả những "rác" này nữa.

#### Sửa Lỗi "Không Đủ Nguyên Liệu" Khi Tiêu Hao

Khi vật phẩm trong túi đồ bị tách thành nhiều dòng ở các phẩm cấp khác
nhau (ví dụ vừa có ở Hoàng Phẩm vừa có ở Địa Phẩm do cơ chế cũ), nhiều
chức năng tiêu vật phẩm trước đây báo lỗi "không đủ" mặc dù tổng số
trong túi rõ ràng đủ. Đã sửa cho:

- **Rèn trang bị** — hết lỗi "Không thể tiêu hao nguyên liệu".
- **Kích hoạt Thể Chất** — không còn ăn Công Đức / Hỗn Nguyên Thạch
  trong khi nguyên liệu vẫn nằm im trong túi.
- **Khai mở / Nâng cấp Linh Căn** — kiểm tra nguyên liệu giờ cộng dồn
  mọi phẩm cấp.
- **Khảm ngọc trận pháp (đặc biệt là Âm Khí Ngọc)** — hết lỗi "Không đủ
  Âm Khí Ngọc trong túi đồ" khi rõ ràng có ngọc trong dropdown.

#### Mở Rộng Hệ Thống Rèn

- **Thêm 15 nguyên liệu rèn chuyên biệt mới** cho các thuộc tính trước
  đây không có nguyên liệu nào "đẩy" tỉ lệ ra:
  - **Sát thương kéo dài**: Huyết Tỷ Tinh (Chảy Máu), Liễm Hỏa Sa
    (Thiêu Đốt), Độc Tủy Tinh (Trúng Độc), Triền Khốn Tinh (DOT chung),
    Vạn Pháp Tinh (Toàn Hệ Nguyên Tố).
  - **Hồi máu / linh lực**: Sinh Khí Tinh (Hồi HP), Linh Tuyền Tinh
    (Hồi MP), Linh Hải Tinh (MP Tối Đa).
  - **Khiên năng lượng**: Hộ Thuẫn Tinh (Khiên Tối Đa), Hộ Thuẫn Tủy
    (Hồi Khiên), Phản Thuẫn Tinh (Chuyển Khiên thành Sát Thương).
  - **Khác**: Phong Tốc Tinh (Tốc Độ), Lực Tủy Tinh (Công Kích),
    Thích Lăng Tinh (Phản Đòn), Pháp Tủy Tinh (Pháp Công).
- Mỗi nguyên liệu mới rớt ở **Vùng Săn 4 / 5 / 6** với tỉ trọng giống
  hệt các nguyên liệu chuyên biệt cũ. Trước đây một số dòng affix (Chảy
  Máu, Thiêu Đốt, Trúng Độc, Hồi HP / MP, Khiên, Phản Đòn…) không có
  cách nào "đẩy" — giờ rèn theo định hướng được toàn bộ.

#### Bí Cảnh

- **Bỏ giới hạn cảnh giới khi vào bí cảnh**: trước đây bí cảnh đòi hỏi
  cảnh giới tối thiểu trên một trong ba hướng tu (Luyện Thể / Luyện Khí
  / Trận Đạo). Giờ **bất kỳ cảnh giới nào cũng vào được mọi bí cảnh** —
  chữ "Yêu cầu" đổi thành "Khuyến nghị". Thấp hơn mức khuyến nghị thì
  trận đấu sẽ rất khó (yêu thú vẫn lên cấp theo cảnh giới gốc của bí
  cảnh), nhưng không còn bị chặn ở cửa.

#### Hiển Thị Trang Bị

- **Sửa "+0 …" trên một loạt thuộc tính phụ rớt từ boss / rèn được**:
  trước đây nhiều affix hiển thị giá trị "+0" và tên thuộc tính bằng
  chuỗi mã thô (ví dụ *+0 burn_dmg_bonus*) thay vì phần trăm thực tế.
  Đã sửa hiển thị cho:
  - **ST Thiêu Đốt**, **ST Chảy Máu**, **ST Trúng Độc**, **ST DOT**.
  - **Phản Đòn**, **ST từ Khiên**.
  - **Tốc Độ** trên giày / phụ kiện.

  Các thuộc tính này thực ra **vẫn đang hoạt động trong combat** từ
  trước — chỉ phần hiển thị bị hỏng nên người chơi không thấy mình
  được lợi gì. Giờ bảng trang bị hiện đúng "+6.0% ST Thiêu Đốt" thay
  vì "+0 burn_dmg_bonus".
- **Sửa thuộc tính "Thép Thân" (Giảm ST) bị mất tác dụng**: prefix
  "Thép Thân" rớt trên giáp / khiên / mũ trước đây ghi nhầm tên thuộc
  tính, khiến giá trị rolled bị **mất hoàn toàn** khi mặc — giờ đã
  cộng dồn đúng vào tổng "Giảm ST" với mức cap thông thường. Trang bị
  cũ trong kho cũng được tự động chuyển đổi sang tên thuộc tính chuẩn
  khi tính chỉ số.

---

### Cân Bằng Chiến Đấu

- **Giới hạn 30 lượt = thua**: Nếu tu sĩ không hạ được đối thủ trong
  vòng **30 lượt**, trận đấu sẽ tự động tính là **thất bại**. Trước đây
  hết lượt chỉ tính là "hòa" và không trừ gì — giờ buộc người chơi phải
  có đủ sát thương để kết liễu mục tiêu, không thể câu giờ chờ DoT
  hoặc né tránh xuyên suốt trận đấu.
  - Ngoại lệ: **Boss Thế Giới** vẫn giữ cơ chế cũ (giới hạn lượt là
    bình thường, đánh bao nhiêu sát thương ăn bấy nhiêu, không bị
    coi là chết).

- **Tăng mạnh sát thương kỹ năng**:
  - **Sát thương cơ bản (base) của kỹ năng** giờ được nhân theo cảnh
    giới của kỹ năng — kỹ năng cảnh giới **R9** đánh ra base **gấp 9
    lần** cũ; R5 gấp 5 lần, R1 không đổi. Các kỹ năng cảnh giới cao
    sẽ tỏa sáng đúng chất "đỉnh phong".
  - **Hệ số ATK/MATK của kỹ năng** được nhân **×4**: với cùng một
    cây kỹ, mỗi điểm Công/Pháp giờ đóng góp gấp bốn lần sát thương.
    Áp dụng đồng đều cho cả người chơi và quái — đôi bên cùng đánh
    mạnh hơn rất nhiều, nhịp combat ngắn lại đáng kể.

### Cân Bằng Vật Phẩm Rớt

- **Dược Viên**
  - **Buff các tỉ trọng thấp**: sau khi cân bằng, các tỉ trọng đáy
    (15.000 / 16.000 / 20.000 / 24.000 / 30.000 / 32.000 / 40.000)
    được nâng lên đúng trên ngưỡng **50.000** (lần lượt thành
    51.000 / 52.000 / 53.000 / 54.000 / 60.000 / 64.000 / 80.000).
    Dược liệu cấp thấp giờ rớt thường xuyên hơn rõ rệt, không còn bị
    "lọt sàng" như trước.
  - **Tổng quan**: toàn bộ 498 item trong bảng Dược Viên hiện nằm
    trong khoảng **51.000 – 80.000** — bảng rớt trở nên đồng đều,
    không có entry nào quá hiếm hoặc quá phổ biến.

---

> Các giá trị cụ thể có thể được điều chỉnh tiếp dựa trên phản hồi
> thử nghiệm. Nếu thấy nhịp chiến đấu quá nhanh/quá chậm hoặc tỉ
> lệ rớt vẫn lệch, vui lòng báo trong kênh phản hồi.
