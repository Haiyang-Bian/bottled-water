"""统一启动入口，从环境配置读取 host 与 port。"""

import uvicorn
from pathlib import Path

from app.core.config import get_settings


def main() -> None:
    """启动 uvicorn 服务器。"""
    settings = get_settings()
    uvicorn.run(
        "app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        reload_dirs=[str(Path(__file__).parent / "src"), str(Path(__file__).parents[1] / "src")],
    )


if __name__ == "__main__":
    main()
