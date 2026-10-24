# Social Radar

Bản trải nghiệm dashboard tiếng Việt để tìm kiếm và chọn lọc nội dung mạng xã hội.

## Chạy tại máy

Không cần cài thư viện hoặc build. Mở `dist/index.html` trong trình duyệt, hoặc chạy:

```sh
python3 -m http.server 8080 --directory dist
```

Sau đó mở http://localhost:8080.

## Đã có

- Giao diện responsive cho desktop và điện thoại.
- Tìm từ khóa, cụm từ chính xác trong dấu ngoặc kép; các từ khóa được kết hợp bằng AND.
- Lọc nền tảng, thời gian tương đối trong dữ liệu mẫu, loại nội dung, ngôn ngữ, tương tác tối thiểu; loại trừ từ khóa phân cách bằng dấu phẩy.
- Sắp xếp theo thời gian, tương tác, mức liên quan.
- Lưu bài và truy vấn bằng localStorage, xuất CSV với nhãn dữ liệu minh họa.
- Tổng hợp số liệu và hashtag trên kết quả đã lọc.

## Phạm vi hiện tại

14 bài minh họa được khai báo trong `dist/app.js`. Chưa kết nối API Facebook, X hoặc Threads; chưa có crawler, đăng nhập, backend, tác vụ định kỳ hoặc phân tích AI. Cảm xúc là nhãn mẫu. Khả năng tìm kiếm thực tế phụ thuộc quyền và hạn mức của từng nền tảng.

Dữ liệu đã lưu chỉ tồn tại trên trình duyệt đang sử dụng. Không nhập khóa API vào giao diện này.

## Kiểm tra

`node --check dist/app.js` đã chạy thành công. Kiểm thử trình duyệt tự động chưa hoàn tất do môi trường giới hạn chạy Chromium.

## Lộ trình

1. Kiểm chứng quyền API và chi phí một nguồn dữ liệu thực tế.
2. Triển khai backend và bộ kết nối riêng cho từng nền tảng.
3. Bổ sung lưu trữ dùng chung, lịch thu thập, xử lý giới hạn và nhật ký lỗi.
4. Thử nghiệm tìm kiếm, chất lượng dữ liệu và xuất báo cáo trên nguồn thật.

Không cần đưa `.openai/hosting.json` vào GitHub; đây là cấu hình của bản Sites riêng biệt.
