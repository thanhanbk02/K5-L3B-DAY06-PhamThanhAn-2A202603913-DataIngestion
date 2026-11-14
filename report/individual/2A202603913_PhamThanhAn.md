# Báo cáo cá nhân — Day 6

Học viên: Phạm Thanh An · MSSV 2A202603913 · Lớp L3B

## Dữ liệu và pipeline

- Chủ đề, nguồn/quyền, số trang và quy tắc tính trang: Chủ đề là supervised learning trong sách *Machine Learning: mì, súp và công thức nấu* của Nguyễn Xuân Khánh, lời tựa ghi ngày 20/7/2016. File local: `starter/data/Machine_learning_co_ban.pdf`. Bản đọc công khai: https://khanh-personal.gitbook.io/ml-book-vn. Repo nguồn: https://github.com/khanhptnk/ml-book-vn-v2. PDF, GitBook và repo đều không ghi giấy phép; bài không gán Creative Commons. Tên file không phải blog *Machine Learning cơ bản* của Vũ Hữu Tiệp. PDF có 41 trang vật lý. Trang 1 chỉ là mục lục nên loại. Trang 2–41 có thân bài, kể cả trang ngắn vì câu bị ngắt sang trang sau. Kết quả: **40 trang nội dung**, 68.234 ký tự pypdf trích được. Chi tiết ở `data/manifest.json`.
- Chuẩn hóa, lỗi và giới hạn: Unicode NFC, gộp khoảng trắng, giữ xuống dòng để chia đoạn. Không có trang rỗng hay trang lỗi (`results/ingestion_log.json`). Công thức toán bị vỡ khi trích. Một số trang mở giữa từ vì ngắt trang của PDF (`Test .`, `chép.`, `đoán`).
- Embedding model/provider/version/số chiều, vector store/version, metric, lệnh chạy và chi phí: Ollama local, model `embeddinggemma:latest`, 768 chiều, chi phí 0 USD. Embed tài liệu mất 13,49 giây. PostgreSQL 16.16 + pgvector 0.8.7, cổng `127.0.0.1:55433`, database `day6_lab`. Tìm kiếm cosine chính xác (`1 - cosine distance`), không dùng ANN. Lệnh chạy từ thư mục `starter/`: `python -m src.run`. Prompt tài liệu: `title: none | text: ...`. Prompt câu hỏi: `task: search result | query: ...`.
- Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn: Mỗi chunk có `document_id` (`ml-mi-sup-2016`), `source`, `page`. Trường lọc thêm là `section`, lấy từ mục lục trong manifest, không tự bịa. Mục lục có các mục tách nhau nên dùng được để lọc. Cấu hình C chỉ lọc khi câu hỏi mang sẵn filter.

## Cấu hình và benchmark

- A / B / C: Cùng corpus, cùng model, cùng 20 câu. A cắt cửa sổ 500 ký tự, chồng 50, không lọc, 163 chunk. B cắt theo đoạn, tối đa 800 ký tự, không lọc, 115 chunk. C dùng đúng chunk và vector của B; khác B đúng một điểm là lọc `section` khi câu có filter. Nạp lại không tăng số dòng: A 163, B 115, C 115, mỗi `chunk_id` một dòng.
- ≥20 câu: `benchmark/questions.json` có 20 câu, viết và tách trước khi embed. Dev là q01–q15. Test là q16–q20. Ba câu có filter: q15 (dev, `regularized_loss_minimization`), q18 và q20 (test, `tinh_chinh_hyperparameter` và `loss_function`). Evidence là chuỗi có thật trong text trang PDF sau khi chuẩn hóa khoảng trắng.
- Tiêu chí chọn chiến lược đã chốt trước khi xem test: Evidence Hit@3 trên dev. Hòa thì Hit@1 dev cao hơn, rồi số chunk ít hơn. Test không được dùng để đổi chunk, filter hay câu hỏi.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 12/15 | 15/15 | 163 | 13,49 giây cho cả A và B | 0 USD |
| A | test | 3/5 | 4/5 | 163 | cùng lần chạy | 0 USD |
| B | dev | 11/15 | 14/15 | 115 | nằm trong 13,49 giây | 0 USD |
| B | test | 5/5 | 5/5 | 115 | cùng lần chạy | 0 USD |
| C | dev | 11/15 | 14/15 | 115 | không embed thêm | 0 USD |
| C | test | 5/5 | 5/5 | 115 | không embed thêm | 0 USD |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số chunk) và trung bình /2 |
|---|---|---|---|---|
| A | 1,0 (10 vị trí) | 15/15 | 0/2 câu test có filter | 0: 4, 1: 7, 2: 4; trung bình 1,00 |
| B | 1,5 (10 vị trí) | 15/15 | 0/2 | 0: 6, 1: 4, 2: 5; trung bình 0,93 |
| C | 1,5 (cùng chunk với B) | 15/15 | 2/2 | 0: 7, 1: 3, 2: 5; trung bình 0,87 |

Đường dẫn trace, nhãn thủ công và cách chọn mẫu: Trace đầy đủ ở `results/traces.json`. Cấu hình ở `results/strategies.json`, chỉ số tự động ở `results/summary.json`, nhật ký ở `artifacts/experiment_log.csv`. Nhãn tay ở `results/manual_audit.json`. Coherence lấy 10 trang nội dung đầu đã sắp xếp, trang 2–11, trước khi dùng điểm retrieval của các vị trí đó. Metadata và grounding chấm 5 câu test × top-3 của cả ba cấu hình. Người chấm là trợ lý đọc chunk theo rubric, cùng người đã viết pipeline, nên có nguy cơ thiên lệch; học viên cần đọc lại trước khi nộp. Nhãn không suy ra từ biến `hit` tự động.

## Phân tích và kết luận

- So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn: A–B đổi cách cắt. A đưa evidence vào top-3 dev thường xuyên hơn (15/15 so với 14/15) nhờ cửa sổ chồng nhau, nhưng chunk bị cắt giữa câu nên coherence chỉ 1,0/2. B giữ trọn ý hơn (1,5/2) và hạng 1 của cả 5 câu test đều đủ bằng chứng, đổi lại dev mất một câu vì evidence không nằm trọn trong một đoạn. B–C chỉ đổi filter. Điểm Hit giống nhau, nhưng top-3 của q15, q18 và q20 thay đổi. C tuân thủ filter 2/2 câu test có filter; A và B là 0/2. Ví dụ sai thứ nhất: **q16**, cấu hình A, nguồn `starter/data/Machine_learning_co_ban.pdf` trang 33. Câu cần cả lý do cấm dùng test set và tên development set. Hai đoạn nằm ở hai cửa sổ 500 ký tự, nên Evidence Hit@3 bằng 0 dù cả ba kết quả đều đúng trang. Ví dụ sai thứ hai: **q18**, cấu hình A, cùng file PDF. Gold là trang 34, tỉ lệ 8:1:1. Hạng 1 lại là trang 13, chia khoảng 80/20 và không có tập phát triển. Đáp án đúng chỉ đứng hạng 2.
- Chiến lược chọn, đánh đổi, điều kiện áp dụng: Chọn **A** vì quy tắc đã khóa trên dev, không phải vì test. A phù hợp khi mục tiêu là không bỏ sót evidence trong top-3 và chấp nhận chunk cụt. Nếu cần hạng 1 tự đọc được, B tốt hơn trên 5 câu test này, nhưng mẫu quá nhỏ để đảo lựa chọn sau khi đã xem test. C đáng dùng khi người dùng đã biết mục cần tìm; filter không sửa chunk vỡ.
- Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí: Chỉ 5 câu test. Quyền tái phân phối PDF chưa được tác giả ghi. Công thức toán trích kém. Nhãn tay chưa được người thứ hai chấm. Thời gian 13,49 giây là một lần embed trên máy local, không tách riêng từng cấu hình. Chi phí API bằng 0.
- Nếu làm lại hoặc mở rộng, cải tiến gì? Tăng overlap hoặc gói theo câu để q16 không bị tách evidence. Không để một chunk chỉ còn heading, như trang 10 của B. Nhờ một người khác chấm lại coherence và grounding. Xin rõ quyền dùng sách trước khi public repo.
