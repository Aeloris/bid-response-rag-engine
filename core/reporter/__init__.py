"""Phase 7：报告与导出（Reporter）。

把引擎产物（应答/核对/风险，已落盘 data/jobs/{id}/）**纯派生**重排成一份 BidReport
（schemas.py 模型 / service.py build_report），再由三个渲染器导出（render.py）：
Markdown / HTML（浏览器报告页 + 目录页）/ Excel。不重跑任何引擎。
"""
