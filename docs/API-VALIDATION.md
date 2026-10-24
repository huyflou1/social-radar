# Kiểm chứng API — 24/10/2026 (Asia/Ho_Chi_Minh)

## Kết luận và trạng thái

Chọn **X Recent Search** làm nguồn pilot. Đã kiểm tra tài liệu chính thức, chưa kiểm chứng quyền của tài khoản vì môi trường chưa có token nền tảng. Không có yêu cầu API trả phí nào được gửi trong quá trình phát triển này. Bộ kiểm thử dùng HTTP mock và cơ sở dữ liệu tạm.

| Nguồn | Phạm vi adapter | Trạng thái |
|---|---|---|
| X | `GET /2/tweets/search/recent`, Bearer token, phân trang và `since_id` | Đối chiếu tài liệu; chưa live-test |
| Threads | `GET /v1.0/keyword_search`, token và quyền tìm kiếm | Adapter dự kiến; chưa xác nhận tài liệu hiện hành hoặc live-test |
| Facebook | `GET /{graph-version}/{page-id}/posts` | Chỉ Page cụ thể; adapter chưa live-test; không tìm toàn Facebook |

Meta documentation trả HTTP 429/không truy cập được qua công cụ tra cứu trong phiên này. Không suy diễn rằng token có quyền public search, không cam kết hạn mức hoặc chi phí Meta. Phải kiểm tra trong Meta App Dashboard trước khi bật hai nguồn này. Thông số version Facebook bắt buộc cấu hình, không đoán version API đang được hỗ trợ.

## X: quyền và chi phí đã đối chiếu

Nguồn chính thức:
- https://docs.x.com/x-api/getting-started/pricing
- https://docs.x.com/x-api/posts/search/introduction
- https://docs.x.com/x-api/posts/search-recent-posts

Recent Search tìm trong 7 ngày gần nhất, tối đa 100 bài/trang, truy vấn thường tối đa 512 ký tự. Cần developer account được duyệt, Project/App và token có quyền tương ứng. Chuỗi token tồn tại không chứng minh endpoint được cấp quyền.

Tại ngày kiểm tra, bảng giá liệt kê Post read **$0.005/resource**, User read **$0.010/resource**. Pilot không yêu cầu user expansion để tránh thêm tài nguyên user. Giá và thuế/phí cần đối chiếu lại ở Developer Console trước khi sử dụng.

| Khối lượng Post reads | Ước tính giá niêm yết |
|---|---:|
| 10 | $0.05 |
| 100 | $0.50 |
| 1.000 | $5.00 |
| 3.000 | $15.00 |

Mặc định mỗi trang 10 bài, app chặn sau 100 lượt đọc được dự trù/ngày UTC. Trường `usage.reserved` tính cả request thất bại theo số bài tối đa để giới hạn thận trọng; không hoàn trả dự trù và không phải hóa đơn thực tế. Phạm vi là tất cả job cùng một DB, không bao gồm app khác dùng chung tài khoản X. Cần đặt **spending limit tại X Developer Console**; app không thể bảo đảm trần hóa đơn của nhà cung cấp. Không tự mua credits hay bật auto-recharge.

## Quy trình cấp quyền

### X — làm trước
1. Vào https://console.x.com/ bằng tài khoản của bạn, hoàn tất quyền developer và tạo Project/App.
2. Kiểm tra endpoint Recent Search được phép và giá áp dụng cho tài khoản.
3. Đặt trần chi tiêu nhỏ ở console. Tự mua credits nếu cần; dự án không thực hiện việc này.
4. Cấu hình `X_BEARER_TOKEN` ở server bằng secret manager hoặc biến môi trường; không gửi token vào chat/repo.
5. Chạy `python -m backend.live_check --query '"AI marketing"'` để kiểm tra cấu hình, chưa gửi request.
6. Chạy thêm `--execute` khi sẵn sàng thực hiện **một request có thể tính phí**.
7. Xem report kiểm chứng, đối chiếu bài gốc và usage console. Sau đó mới bật lịch.

### Threads — sau khi xác minh

Kiểm tra https://developers.facebook.com/docs/threads/keyword-search/ và App Dashboard. Xác nhận ứng dụng có sản phẩm Threads, token còn hạn, quyền `threads_basic` và quyền keyword search/advanced access phù hợp với phạm vi thực tế (cần đối chiếu lại). Adapter không tự xin review hay refresh token. Khi phát hiện token hết hạn/quyền bị từ chối, job được tắt để người vận hành xử lý.

### Facebook — Page cụ thể

Đối chiếu https://developers.facebook.com/docs/pages-api/posts/ và https://developers.facebook.com/docs/features-reference/page-public-content-access/. Chọn Page có quyền đọc tương ứng và Page token. Đặt `META_GRAPH_VERSION` theo version được hỗ trợ trong App Dashboard. Dữ liệu ngoài Page được cấp quyền, nhóm kín, profile riêng tư và tìm kiếm toàn mạng không thuộc adapter này. Query của job Facebook chỉ lọc cục bộ để liên kết bài với job; dữ liệu Page đã đọc vẫn được lưu vào kho chung.

## Tiêu chí pilot trên nguồn thật (chưa đạt vì thiếu token)

- Request live thành công; ghi nhận UTC, số bài trả về, số bài mới và lỗi.
- Kiểm tra thủ công tối đa 10 bài: đúng truy vấn gốc, thời gian, link và nội dung.
- Chạy lại sau chu kỳ: cùng platform+external_id không tăng số bản ghi trùng.
- Kiểm tra tiếp trang, restart worker, backoff khi hết quota (dùng mock nếu không thể tái tạo an toàn).
- Tìm kiếm nội bộ, bookmark và CSV phản ánh cùng tập dữ liệu.
- Ghi rõ tỷ lệ thiếu tác giả/thời gian/link/chỉ số; không biến trường thiếu thành số 0.
- Đối chiếu credits trước/sau request; xác nhận trần ở provider console.

HTTP 200 với 0 bài xác nhận request được chấp nhận nhưng không chứng minh độ bao phủ dữ liệu hoặc quyền tìm toàn nền tảng. Không đánh giá precision/recall chỉ bằng một mẫu nhỏ. Khi không đủ quyền, sửa cấp quyền trước; không chuyển sang cookie scraping.
