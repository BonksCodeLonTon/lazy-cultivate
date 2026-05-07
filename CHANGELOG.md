# Nhật Ký Thay Đổi

Ghi chép thay đổi của dự án. Mục mới ở trên cùng. Định dạng ngày `YYYY-MM-DD`.

## [Chưa Phát Hành] — 2026-05-07

### Thêm Mới
- **Thiên Kiếp toàn cảnh giới × 3 trục**: Tu sĩ giờ phải đối mặt
  Thiên Kiếp ở **mỗi lần đột phá đại cảnh giới** trên cả ba trục
  (Luyện Thể, Luyện Khí, Trận Đạo) — không còn giới hạn realm 5..8
  và không còn miễn cho Trận Đạo. `MAJOR_BREAKTHROUGHS` (trong
  `src/game/systems/tribulation.py`) mở cho `body / qi / formation`
  tại current realm idx `0..7` (8 đột phá đại cảnh giới × 3 trục
  = **24 lần đối mặt Thiên Kiếp** trong cả hành trình tu).
  - Cog `breakthrough` đã có sẵn nhánh gọi `TribulationManager` khi
    `check_needs_tribulation` trả True — chỉ mở rộng dữ liệu là đủ,
    không cần sửa flow đột phá.
  - Trận Đạo độ kiếp thất bại **không trừ Công Đức** (apply_breakthrough
    chỉ chạy khi trib thắng); chỉ HP về 1 và 30% xác suất rớt 1 bậc.
- **Stats Thiên Kiếp ngang Truyền Thuyết (Apex Đạo Cốt-tier)**:
  Bổ sung 24 entry trong `src/data/tribulations/trib_realm_01..08.json`
  (`trib_<axis>_<idx>` × 8 cảnh × 3 trục, cộng `default_heavenly_trib`
  fallback). Base stats neo theo `ApexDaoCot_R3/5/7/9` (rank `chi_ton`):
  - **HP** (base, trước realm_scale × 1.4 hp_scale):
    `4k → 8k → 14.5k → 22k → 30k → 42k → 56k → 80k`
    (R1..R8). Realm-level multiplier 1.0..1.72 áp lên atk/matk/def.
  - **Profile theo trục**:
    - `body` — tank: hp ×1.10, def ×1.20, matk ×0.85
    - `qi` — pháp: hp ×0.95, matk ×1.20, def/atk ×0.85
    - `formation` — cân bằng (×1.00) + crit rating cao hơn 5
  - **Skill pool** (Lôi nguyên tố thiên kiếp):
    - R1–R2: `EnemyTribLoi_T1` (Thiên Kiếp Lôi Sơ)
    - R3–R4: `EnemyTribLoi_T1 + T2` (thêm Thiên Kiếp Lôi Trung)
    - R5–R6: `EnemyTribLoi_T2 + T3` (thêm Thiên Kiếp Lôi Hậu, có
      stun 40% / liệt 50%)
    - R7–R8: `EnemyTribLoi_T3 + EnemyLoi_T3` (Lôi Cửu Thiên elite)
  - **Sát thương / phòng thủ**: `final_dmg_bonus` 0.10 → 0.45 theo
    cấp; base_crit_rating 40 → 185; base_crit_dmg_rating 60 → 255.
  - **Kháng nguyên tố**: `loi` 0.20 → 0.50; các nguyên tố khác
    0.10 → 0.32 (thang thấp ở R1, full 9-elem ở R7+).
  - **`immune_hard_cc=true`** từ R5 trở lên — giống world boss,
    chặn freeze/stun/paralyze/disable nên không thể "lock down"
    Thiên Kiếp bằng CC để né hết damage.
  - Naming: `vi` của entry mang tên realm **đang rời** (vd
    `trib_qi_5` = "Hóa Thần Thiên Kiếp" — kẻ rời Hóa Thần để bước
    vào Luyện Hư).
- **Phân Giải Trang Bị (recycle / bulk recycle)**: Lệnh `/recycle` và
  nút "♻️ Phân Giải" trong Luyện Công Phường mở UI multi-select cho
  trang bị trong túi (lọc theo vị trí, phân trang khi vượt 25 món).
  Mỗi món phân giải trả về **1 vật liệu rèn ngẫu nhiên** theo cấp
  (Cấp 1-2 → Phẩm 1, …, Cấp 9 → Phẩm 6). Mapping bám đúng tùy chọn
  rẻ nhất của `forge_recipes` — phân giải không bao giờ trả phẩm cao
  hơn công thức tại cấp đó, nên không thể "farm" vật liệu xịn bằng
  recycle. Module `src/game/systems/recycle.py`, cog
  `src/bot/cogs/recycle.py`.
- **Đan Lô ghi nhớ lựa chọn**: Thêm cột `preferred_furnace_key` vào
  bảng `players` (migration `0013`). Lựa chọn Đan Lô của người chơi
  được lưu DB và áp dụng cho các lần luyện sau. Tự fallback về auto-pick
  nếu Đan Lô đã bán hoặc không đủ tier.
- **Kỹ năng mới cho Yêu Thú Bí Cảnh**: Thêm 136 kỹ năng
  `EnemyDungeon_<Element>_<Rank>_R<Realm>` cho mọi tổ hợp
  (cảnh giới × ngũ hành × phẩm cấp). Script `scripts/add_dungeon_enemy_skills.py`
  có thể chạy lại.

### Cân Bằng
- **Drop nguyên liệu Hoang Cổ Thánh Thể theo cảnh giới Apex**: Trước
  đây 9 nguyên liệu Phá Khóa (`HoangCoLongLan` → `HoangCoThanhCot`)
  định nghĩa trong `constitution_materials.json` nhưng **không có ở
  bất kỳ loot table nào** — chuỗi 9 mắt xích coi như chỉ admin grant.
  Thêm drop độc lập (no `pool_id`, không cạnh tranh nhau hay với
  `MatDaoCotTinh`) vào 4 bảng `LootTheChatApex_R3/R5/R7/R9`. Phân
  bổ chuỗi thấp ↔ apex thấp, chuỗi cao ↔ apex cao:
  - **R3** (Đạo Cốt Huyết Giáp) → Chain 1 LongLan (4%), Chain 2 PhuongVu (3.5%)
  - **R5** (Đạo Cốt Thiên Giáp Thần) → Chain 3 QuyVan (4.5% qty 1-2), Chain 4 LanTuy (4% qty 1-2)
  - **R7** (Đạo Cốt Chiến Thần) → Chain 5 ThienTinh (4.5% qty 1-2), Chain 6 DiaPhach (4% qty 1-2)
  - **R9** (Đạo Cốt Vô Thượng Tôn) → Chain 7 DaoVan (5.5% qty 1-2), Chain 8 ThanTam (4.5% qty 1-2), Chain 9 ThanhCot (3.5% qty 1-2)
  - Số kill kỳ vọng để Phá Khóa từng chain (qty_need ÷ activation_chance ÷ items_per_kill, mô phỏng 100k kills/bảng): Chain 1 ~42, Chain 4 ~74, Chain 6 ~145, Chain 9 ~466. Phù hợp với độ tedious "endgame ultimate" của 9 mắt xích.
- **Trận Đạo EXP / Công Đức ÷10 (heavy nerf)**: Bảng
  `FORMATION_EXP_PER_MERIT_BY_REALM` chuyển từ int (10..2 EXP/Công Đức)
  sang float (1.0..0.2). Lý do: trước đây player tích Công Đức từ
  kills / dungeon / world boss rồi đổ một cục để bỏ qua bậc, lệch
  pacing với Luyện Thể / Luyện Khí (gated theo lượt thật). Tổng
  Công Đức để max cả 9 cảnh giới Trận Đạo: **43M → 430M** (10×).
  - R0 Khai Huyền: `1.0 EXP/Công Đức` (giữ rẻ cho người mới Trận Tu).
  - R4 Tâm Trận: `0.4` (~2.5 Công Đức = 1 EXP).
  - R8 Đế Trận: `0.2` (5 Công Đức = 1 EXP, riêng R8 cần ~213M Công Đức).
  - Thêm guard `exp_gained <= 0` trong `study_formation_with_merit` —
    sub-1.0 rate khiến spend quá ít làm tròn về 0 EXP, nay refuse
    spend kèm gợi ý số Công Đức tối thiểu (`ceil(1 / rate)`).
  - UI cog `📘 Học Trận` và Sổ Tay tự đảo hiển thị: rate ≥ 1 vẫn là
    "1 Công Đức = X EXP", rate < 1 đổi thành "X Công Đức = 1 EXP"
    cho dễ đọc.
- **Drop rate `MatDaoCotTinh` ÷10**: Hạ trọng số drop tại cả bốn bảng
  `LootTheChatApex_R3/R5/R7/R9` xuống 1/10 (400k→40k, 500k→50k,
  550k→55k, 600k→60k). Tỉ lệ drop mới (mô phỏng 200k kills/bảng):
  R3 ~3.9%, R5 ~5.0%, R7 ~5.5%, R9 ~6.0% — qty range giữ nguyên,
  trung bình ~17 / ~13 / ~7 / ~6 kill cho 1 viên Đạo Cốt Tinh tại
  mỗi tier. Mục tiêu: kéo dài đường tu Thể Chất Truyền Thuyết
  (Đạo Cốt Tinh là nguyên liệu chính chuyển hóa Đạo Cốt-tier).

### Sửa Lỗi
- **Chống abuse Bí Cảnh đa-phiên (Auto Repeat)**: Trước đây người chơi
  có thể mở `/dungeon` trên 2+ tin nhắn ephemeral cùng lúc và bấm
  "⚔️ Vào Bí Cảnh" / "🔁 Tự Động Lặp Lại" trên cả hai — hai async task
  cùng thao tác trên 1 Player row, nhân đôi Công Đức / drop / Hỗn Nguyên
  Thạch. Thêm in-memory lock `_ACTIVE_DUNGEON_USERS: set[int]` ở
  `src/bot/cogs/dungeon.py`. Nút Vào / Auto Repeat phải gọi
  `_try_acquire_dungeon_session` trước khi defer; phiên thứ hai bị từ
  chối với thông báo ephemeral. `try/finally` quanh `_execute_dungeon`
  và `_run_dungeon_with_repeat` đảm bảo lock luôn được release dù có
  ngoại lệ. Restart bot xóa set (chấp nhận được cho hiếm trường hợp
  crash giữa run).
- **Auto-chọn Đan Lô cấp thấp**: Trước đây sở hữu G1..G4 thường thì luôn
  chọn G1. Đã thêm `furnace_tier` vào điểm so sánh — Đan Lô cấp cao
  thắng khi điểm hòa.
- **Hoang Cổ Thánh Thể: chỉ Chain 1 được rút từ pre-roll Truyền Thuyết**:
  Đảo ngược exclusion "block toàn bộ HoangCo" trước đây — Chain 1
  (Sơ Tỉnh, không có `progresses_from`) là **đầu chuỗi hợp lệ** và
  cần xuất hiện trong cổng pre-roll 0.5% như mọi Truyền Thuyết khác.
  Bỏ filter `not c["key"].startswith("ConstitutionHoangCo")` trong
  `_roll_starter_constitution`; filter `not c.get("progresses_from")`
  vẫn còn nên Chain 2–9 (có `progresses_from`) tiếp tục bị chặn —
  chỉ có thể đạt qua flow Phá Khóa (kích hoạt + nguyên liệu hoang cổ).
  `registry.rollable_constitutions()` cũng nhận thêm filter
  `progresses_from` cho nhất quán với pool Truyền Thuyết. Mô phỏng
  200k roll forced-legendary: Chain 1 ~5.33%, Chain 2–9 = 0 hits.

### Thay Đổi
- **Đường cong EXP cảnh giới: gap bậc tăng theo n²**: Đổi
  `_even(step)` → `_upward(step)` với gap_n = step × n² (sum-of-squares).
  bậc 8→9 nay tốn **81×** XP của bậc 0→1 (trước là 1× cố định) — bậc
  cuối thành "tường" thật trước Độ Kiếp. bậc 9 = 285 × step (vs 9 × step
  cũ). `TRIBULATION_EXP_COST`: 270,000 → **42,750,000** (R8 step 150,000
  × 285). Tổng EXP/cảnh giới tăng ~30× so với phân phối phẳng cũ. Test
  `test_advance_cultivation_levels_up_via_realm_table` đã được làm
  adaptive (đọc threshold từ realm table thay vì hard-code 270 lượt).
- **Pill EXP/giảm độc theo phẩm đan dược, có gate cảnh giới**:
  `exp_luyen_the` / `exp_qi` / `reduce_toxicity` nay quan tâm đến
  `grade` đan. EXP/độ giảm đan độc co dãn theo phẩm đan (Grade-N pill
  → realm idx N-1 trên `_TARGET_PILLS_PER_REALM`), không còn phụ
  thuộc cảnh giới người dùng. Khi cảnh giới trục tương ứng vượt phẩm
  đan, consume bị từ chối (`applied=False`, đan ở lại trong túi) —
  không lãng phí pill quá yếu. Detox tỉ lệ thuận với phẩm đan
  (Grade 2 −40 → Grade 9 −180 mỗi Hoàn). `reduce_toxicity` xét
  `max(body_realm, qi_realm)` vì Đan Độc là stat toàn cục.
- **Refactor `compute_combat_stats`**: Tách 4 helper riêng cho
  MP reservation, HP→Shield, pill buff, và toxicity penalty.
  Không đổi logic — 447/447 test pass.

---

## [2026-05-06]

### Thêm Mới
- **Hệ thống Đan Độc**: Tích lũy độc từ pill làm giảm sát thương đầu ra
  và hồi máu (cap −0.25). Module `toxicity.py`.
- **Buff vĩnh viễn từ Đan Dược**: Cột `pill_buff_counts` (migration
  `0012`). Mỗi pill tăng counter (cap 20/loại): +0.5 SPD, +10 DEF,
  +1% sát thương ngũ hành.
- **Lấp kỹ năng theo cấp**: Script đảm bảo mỗi ô (ngũ hành, cảnh giới,
  cấp) có ≥2 kỹ năng cho người chơi.

### Sửa Lỗi
- **World Boss giới hạn cảnh giới**: Xét cả 3 trục (Tu Thể, Tu Khí,
  Trận Đạo) thay vì chỉ Tu Khí.
- **Bí Cảnh giới hạn cảnh giới**: Áp dụng cho cả Tu Thể và Trận Đạo.
- **Công Đức không còn âm**: Thêm guard ở economy + test bảo vệ.
- **Đan Độc max → 0% EXP từ pill**.

### Thay Đổi
- **Loại bỏ elixir cũ** khỏi rương loot.
- **Pill buff cap 20 stack/loại**.
