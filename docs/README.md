# Hướng dẫn đọc tài liệu

Thư mục này chỉ giữ tài liệu phục vụ bản hiện hành F → G1 → G2. Review nội bộ, changelog và tài liệu đã thay thế không nằm trong bộ tài liệu đưa lên repo.

## Bài toán và trình bày

- [Problem Statement — Word](<Problem Statement.docx>) và [PDF](<Problem Statement.pdf>): bản chỉnh sửa và bản đọc/xuất, cần giữ cả hai.
- [PDMT map](pdmt_map.png): sơ đồ hiện hành F → G1 → G2. Ảnh nhúng trong Problem Statement vẫn cần thay trước khi nộp.
- [Báo cáo F1–F3](Bao_cao_F1-F3_VI.md) và [bản Word](Bao_cao_F1-F3_VI.docx): báo cáo kết quả F. Run đang dùng lấy từ `artifacts/modeling/manifest.json`; báo cáo có thể ghi run trước với cùng kết quả model.

## Dữ liệu và phương pháp

- [Kiến trúc dữ liệu](data_architecture.md), [từ điển dữ liệu](data_dictionary.csv), [thời điểm có feature](feature_availability.csv).
- [Thiết kế đánh giá F](F1_validation_design.md).
- [Bảng quyết định](decision_table_draft.md), [KPI register](kpi_register.csv), [kế hoạch pilot](Pilot_evaluation_plan.md).

## Tích hợp và vận hành demo

- **Người mới tải repo:** [hướng dẫn cài và chạy Streamlit + n8n](../RUN_DEMO.md).
- [Hợp đồng dữ liệu G1](G1_data_contract.md).
- [Kiến trúc G2](G2_architecture.md), [hợp đồng dữ liệu G2](G2_data_contract.md), [lựa chọn công cụ](G2_tool_choices.md).
- Hướng dẫn chạy: [dashboard](../dashboard/README.md), [n8n](../n8n/README_VI.md).
- **AI email drafts (n8n Cloud + Streamlit Cloud):** [đặc tả](SPEC_AI_N8N_VI.md), [triển khai cloud](README_DEPLOY_CLOUD.md), [trạng thái kiểm chứng](AI_EMAIL_VERIFICATION_VI.md).

`figures_F/` chứa hình của báo cáo F. Word là bản để chỉnh sửa, PDF là bản để đọc/nộp; Markdown giúp đọc báo cáo ngay trên repo.
