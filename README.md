# Social Radar

Dashboard tiếng Việt và backend pilot để thu thập nội dung mạng xã hội qua API được cấp quyền.

**Trạng thái:** backend + adapter + kiểm thử bằng phản hồi giả lập. Chưa kiểm chứng trên nguồn thật vì chưa có token tài khoản. X là nguồn pilot đầu tiên; Threads/Facebook là adapter chưa live-test. Không có crawler toàn Facebook, nhóm kín hoặc profile riêng tư. Xem [quyền API, chi phí và kế hoạch kiểm chứng](docs/API-VALIDATION.md).

## Chạy backend và giao diện dữ liệu thật

Python 3.12+, một máy chủ với ổ đĩa bền vững:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền `.env` bằng công cụ local/secret manager. Tạo `RADAR_APP_TOKEN` ngẫu nhiên ít nhất 24 ký tự; cấu hình `X_BEARER_TOKEN` của bạn. Không commit `.env`. Nạp biến môi trường bằng service manager hoặc, với tệp `.env` tin cậy đã đặt quyền chỉ chủ sở hữu đọc:

```sh
set -a
. ./.env
set +a
uvicorn backend.app:app --host 127.0.0.1 --port 8080
```

Mở **http://localhost:8080/live.html**. Nhập `RADAR_APP_TOKEN` để truy cập workspace. Token nền tảng chỉ lưu ở server; mã workspace chỉ giữ trong bộ nhớ trang và mất khi reload. Không dùng máy chủ static thuần cho màn hình live.

Ở terminal khác, nạp cùng môi trường và dùng cùng đường dẫn DB để chạy lịch:

```sh
python -m backend.worker
```

Tạo job mặc định **lịch tắt**. Nhấn “Chạy 1 trang” để chạy thủ công; bật lịch khi đã kiểm chứng chi phí/quyền. Mỗi lượt lấy một trang, trang tiếp theo chờ chu kỳ. Nhấn tắt lịch không hủy request đang chạy. Tiến trình worker cần được systemd/supervisor quản lý nếu muốn chạy liên tục.

## Chế độ minh họa

Mở `dist/index.html` trực tiếp hoặc phục vụ `dist/` bằng HTTP. Dữ liệu mẫu và localStorage của chế độ này tách khỏi kho dữ liệu thật. Bấm “Dữ liệu thật” chỉ hoạt động khi có backend chạy cùng origin.

## Cấu trúc

- `backend/connectors.py`: X Recent Search, Threads Keyword Search, Facebook Page posts. Không gửi token trong URL, không theo `paging.next` tùy ý; chỉ dùng cursor với host API cố định.
- `backend/db.py`: SQLite WAL, bài viết, bookmark chung, job, checkpoint, lịch sử chạy, dự trù đọc và cooldown.
- `backend/worker.py`: claim có lease, chống chạy chồng, upsert theo platform+external_id, backoff, tắt job khi lỗi quyền/credits hoặc 5 lần lỗi liên tiếp.
- `backend/app.py`: FastAPI, API được bảo vệ bằng workspace bearer token, static UI.
- `backend/live_check.py`: kiểm tra live một trang có chủ đích, báo cáo chất lượng; mặc định không gửi request.
- `dist/live.html`: quản lý nguồn, job/lịch, tìm và loại trừ từ khóa, bookmark, xuất CSV, nhật ký.

## Cấu hình

| Biến | Ý nghĩa |
|---|---|
| `RADAR_APP_TOKEN` | Mã truy cập chung bắt buộc, ít nhất 24 ký tự |
| `RADAR_DB` | Mặc định `data/radar.sqlite3`; API và worker phải cùng DB |
| `X_BEARER_TOKEN` | Token đọc của X app |
| `X_DAILY_READ_CAP` | Mặc định 100 bài dự trù/ngày UTC, dùng chung tất cả job X |
| `COLLECT_PAGE_SIZE` | 10–100; mặc định 10 |
| `THREADS_ACCESS_TOKEN` | Token Threads; cần xác minh quyền trước |
| `FACEBOOK_PAGE_TOKEN` | Token cho Page có quyền đọc |
| `META_GRAPH_VERSION` | Bắt buộc với Facebook; version xác minh ở Meta console |

Giá X được kiểm tra ngày 24/10/2026: $0.005/Post read; 10 bài tối đa khoảng $0.05 tiền Post reads. Không mở rộng user profile. Dự trù cục bộ tính cả request thất bại và không đại diện hóa đơn; **đặt trần chi tiêu trong X Developer Console**. Không có mua credits tự động.

## Kiểm chứng nguồn thật

```sh
python -m backend.live_check --query '"AI marketing"'
# Khi đã cấu hình quyền, credits và sẵn sàng cho request có thể tính phí:
python -m backend.live_check --query '"AI marketing"' --execute
```

Lệnh execute tạo job lịch tắt, chạy tối đa một trang, lưu bài và in JSON thống kê; không in token. Cần đối chiếu thủ công link bài gốc và usage console. Chưa có token thì không thể xác nhận quyền hoặc chất lượng dữ liệu thật. Không gửi token vào chat.

## API

Mọi `/api/*` ngoài health cần `Authorization: Bearer <RADAR_APP_TOKEN>`.

- `GET /api/health`, `GET /api/sources`: health và trạng thái cấu hình; cấu hình token không đồng nghĩa đã có quyền.
- `POST /api/jobs`: `{platform, query, page_id?, interval_seconds}`; job lịch tắt.
- `GET /api/jobs`, `PATCH /api/jobs/{id}` với `{enabled: true|false}`.
- `POST /api/jobs/{id}/run`: tối đa một trang, vẫn tuân thủ backoff, lease và trần đọc.
- `GET /api/posts`: `q`, `exclude`, `platform`, `days`, `min_engagement`, `language`, `content_type`, `saved`, `job_id`, `limit`, `offset`.
- `PUT /api/posts/{id}/bookmark`: `{saved: true|false}`; lưu chung workspace.
- `GET /api/reports.csv`: cùng bộ lọc, tối đa 10.000 dòng; header `X-Report-Truncated` báo bị cắt.
- `GET /api/runs`, `GET /api/usage`: lịch sử và dự trù, không lộ token/payload lỗi upstream.

Query thu thập X dùng cú pháp X (OR, lang:, from:...). Query tìm trong DB dùng từ khóa AND, cụm từ trong ngoặc kép và loại trừ phân cách dấu phẩy, không phải cú pháp X. Facebook đọc Page rồi gắn bài phù hợp query vào job, không lọc upstream theo từ khóa. Có thể lưu cả bài Page không khớp vào kho chung; dùng `job_id` để xem tập phù hợp job.

## Giới hạn pilot và vận hành

Một workspace tin cậy, một host, cùng SQLite trên ổ đĩa local bền vững. API và worker có thể là hai tiến trình. Chưa có tài khoản riêng, phân quyền theo người, OAuth/refresh token hay PostgreSQL đa máy. Mọi người có workspace token được xem dữ liệu và quản lý lịch, nên chỉ cấp cho người tin cậy. Không triển khai DB trên network filesystem hoặc nhiều host. Sao lưu bằng SQLite backup API hoặc dừng cả hai tiến trình trước khi copy; lưu cả WAL nếu copy lúc hoạt động.

Khi đưa ra Internet, dùng HTTPS qua reverse proxy, quản lý process, secret manager và backup; giữ uvicorn bind loopback. Các request API được xác thực; tài liệu OpenAPI mô tả cấu trúc, không chứa dữ liệu/secret. Chưa có rate-limit theo người tại app; reverse proxy cần giới hạn request nếu chia sẻ workspace rộng.

Không có sentiment AI. Chỉ số không có quyền đọc là `null`/“—”, không phải 0; khi lọc tương tác, trường thiếu đóng góp 0 nên bài thiếu chỉ số có thể bị loại. Chưa xử lý sự kiện xóa bài từ provider hoặc retention policy tự động: đây là việc cần hoàn tất theo điều khoản nền tảng trước production. Không cam kết thu đủ dữ liệu, đặc biệt nếu số trang lớn hơn tốc độ lịch thu thập hoặc X vượt cửa sổ 7 ngày. Chưa có checkpoint reset UI; khi query cũ lỗi pagination, tạo job mới với cùng query để đọc lại có giới hạn.

## Kiểm thử

```sh
python -m unittest discover -s tests -v
node --check dist/app.js
node --check dist/live.js
```

Test dùng HTTP mock, DB tạm và API in-process: quyền thiếu, collect/filter/export/bookmark, dedup, checkpoint restart, 429/backoff, lỗi quyền, atomic read cap và claim cạnh tranh. Đây không phải kiểm chứng quyền live. GitHub Actions chạy lại trên mỗi push/PR. Đã kiểm tra giao diện Chromium với API giả lập: đăng nhập workspace, tạo job, thu thập một trang, lưu bài, lọc loại trừ, màn hình 390px và đăng xuất; không có lỗi JavaScript. Kiểm tra này không gọi API nền tảng thật.
