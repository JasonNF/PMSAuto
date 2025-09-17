import os
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from log import logger

# 兼容性读取设置：优先 settings.py，其次环境变量，最后默认值
try:
    import settings as _cfg
except Exception:  # pragma: no cover
    _cfg = object()

UID = getattr(_cfg, "UID", os.environ.get("UID"))
GID = getattr(_cfg, "GID", os.environ.get("GID"))
STRM_FILE_PATH = getattr(
    _cfg,
    "STRM_FILE_PATH",
    getattr(_cfg, "EMBY_STRM_ASSISTANT_MEDIAINFO", os.environ.get("STRM_FILE_PATH", "/tmp/strm")),
)
STRM_MEDIA_SOURCE = getattr(
    _cfg,
    "STRM_MEDIA_SOURCE",
    os.environ.get("STRM_MEDIA_SOURCE", "http://127.0.0.1:8000"),
)

# 将字符串 UID/GID 转为 int；失败则使用 None（跳过 chown）
def _to_int(x):
    try:
        return int(x)
    except Exception:
        return None

_UID = _to_int(UID)
_GID = _to_int(GID)


def create_strm_file(
    file_path: Path,
    strm_path: Path = Path(STRM_FILE_PATH),
    strm_file_path: Optional[Path] = None,
    media_source: str = STRM_MEDIA_SOURCE,
):
    """
    创建 .strm 文件

    Args:
        file_path: 媒体文件的完整路径
        strm_path: .strm 文件存放目录
        strm_file_path: 指定 .strm 文件的完整路径（包含文件名），优先级高于 strm_path
        media_source: 媒体源 URL 前缀
    """
    try:
        # 优先使用 strm_file_path 参数
        if strm_file_path:
            strm_path = strm_file_path.parent
        # 确保 strm 目录存在
        strm_path.mkdir(parents=True, exist_ok=True)

        # 构造 .strm 文件的内容
        strm_content = f"{media_source.rstrip('/')}/{quote(str(file_path).lstrip('/'))}"

        # 构造 .strm 文件的完整路径
        strm_file_name = file_path.name + ".strm"
        strm_file_full_path = (
            (strm_path / strm_file_name) if not strm_file_path else strm_file_path
        )

        # 写入 .strm 文件
        strm_file_full_path.write_text(strm_content, encoding="utf-8")

        # 设置权限（仅当提供了有效的 UID/GID 时）
        if _UID is not None and _GID is not None:
            try:
                for item in strm_path.rglob("*"):
                    shutil.chown(str(item), user=_UID, group=_GID)
                shutil.chown(str(strm_path), user=_UID, group=_GID)
            except Exception as e:
                logger.info(f"chown 跳过: {e}")

        logger.info(f"为 {file_path} 创建 strm 文件成功: {strm_file_full_path}")
        return True
    except Exception as e:
        logger.error(f"为 {file_path} 创建 strm 文件失败: {e}")
        return False


if __name__ == "__main__":
    create_strm_file(
        Path(
            "/Media/TVShows/Aired_2002/M06/[火线] The Wire (2002) {tmdb-1438}/Season 04/[火线] The Wire (2002) {tmdb-1438} - S04E06 - The.Wire.S04E06.2006.1080p.Blu-ray.x265.10bit.AC3￡cXcY@FRDS.mkv"
        ),
        Path("./data"),
    )
