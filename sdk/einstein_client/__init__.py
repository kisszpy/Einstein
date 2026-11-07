"""Einstein Python SDK —— 零第三方依赖（urllib）。

    from einstein_client import EinsteinClient

    with EinsteinClient("http://localhost:8000", password="admin123",
                        username="admin") as client:
        print(client.chat("死锁的四个必要条件是什么"))
        hits = client.search([kb_id], "死锁")
        client.upload_document(kb_id, "报价.xlsx")   # 异步摄取，返回 job_id
"""

from .client import EinsteinClient, EinsteinError, JobResult

__all__ = ["EinsteinClient", "EinsteinError", "JobResult"]
__version__ = "0.2.0"
