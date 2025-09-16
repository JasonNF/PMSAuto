#! /usr/bin/env python3

import json
import os
import re
from pathlib import Path
from time import sleep
from typing import Dict, List, Optional, Sequence, Union

import requests
from log import logger
try:
    import settings as _cfg
except Exception:  # pragma: no cover
    _cfg = object()

# 读取配置，缺失时使用环境变量或默认值兜底
EMBY_API_TOKEN = getattr(_cfg, "EMBY_API_TOKEN", os.environ.get("EMBY_API_TOKEN", ""))
EMBY_BASE_URL = getattr(_cfg, "EMBY_BASE_URL", os.environ.get("EMBY_BASE_URL", ""))
# 兼容历史字段：优先 STRM_FILE_PATH；没有则退回 EMBY_STRM_ASSISTANT_MEDIAINFO；再退回临时目录
STRM_FILE_PATH = getattr(_cfg, "STRM_FILE_PATH", getattr(_cfg, "EMBY_STRM_ASSISTANT_MEDIAINFO", os.environ.get("STRM_FILE_PATH", "/tmp/strm")))
from strm import create_strm_file


class Emby:
    "Emby Class"

    def __init__(
        self, base_url: str = EMBY_BASE_URL, token: str = EMBY_API_TOKEN
    ) -> None:
        self.token = token
        self.base_url = base_url

    @property
    def libraries(self) -> List[Dict[str, str]]:
        res = requests.get(
            f"{self.base_url}/Library/SelectableMediaFolders?api_key={self.token}"
        )
        if res.status_code != requests.codes.ok:
            logger.error(f"Error: fail to get libraries: {res.text}")
            res.raise_for_status()
        _libraries = []
        for lib in res.json():
            name = lib.get("Name")
            subfolders = lib.get("SubFolders")
            for _ in subfolders:
                path = _.get("Path")
                _id = _.get("Id")
                _libraries.append({"library": name, "path": path, "id": _id})
        return _libraries

    def get_library_by_location(self, path: str) -> Optional[str]:
        """通过路径获取库"""
        for lib in self.libraries:
            if path.startswith(lib.get("path")):
                return lib.get("library")
        return None

    def get_items(
        self,
        parent_id=None,
        item_types="Movie,Episode,Series,Audio,Music,Game,Book,MusicVideo,BoxSet",
        recursive=True,
    ):
        """
        获取媒体项目

        Args:
            parent_id: 父级ID (媒体库ID)
            item_types: 项目类型
            recursive: 是否递归查询
        """
        try:
            url = f"{self.base_url}/Items"
            params = {
                "api_key": self.token,
                "Recursive": str(recursive).lower(),
                "IncludeItemTypes": item_types,
                "Fields": "Path,MediaSources",
                "EnableTotalRecordCount": "false",
            }

            if parent_id:
                params["ParentId"] = parent_id

            response = requests.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            return data.get("Items", [])

        except Exception as e:
            logger.info(f"获取媒体项目失败: {e}")
            return []

    def get_all_items(self, filter=None):
        """获取所有视频信息"""
        logger.info("获取媒体库...")
        if filter is None:
            filter = []
        libraries = self.libraries

        all_items = []

        for library in libraries:
            lib_name = library.get("library")
            lib_id = library.get("id")
            if filter and lib_name not in filter:
                logger.info(f"媒体库 {lib_name} 不在过滤列表 ({filter}) 中，跳过...")
                continue

            logger.info(f"处理媒体库: {lib_name}, Subfolder ID: {lib_id}")

            # 获取该库中的所有视频项目
            items = self.get_items(parent_id=lib_id)

            for item in items:
                if item.get("Path") and item.get("MediaSources"):
                    all_items.append(
                        {
                            "id": item["Id"],
                            "name": item.get("Name", ""),
                            "path": item["Path"],
                            "type": item.get("Type", ""),
                            "library": lib_name,
                            "media_sources": item.get("MediaSources", []),
                        }
                    )

        logger.info(f"找到 {len(all_items)} 个视频文件")
        return all_items

    def create_strm_file_for_existed_items(self, filter=None):
        items = self.get_all_items(filter=filter)
        for item in items:
            file_path: str = item.get("path")
            # 跳过 strm 文件
            if file_path.endswith(".strm"):
                continue
            file_path = file_path.replace("/Media2", "/Media")
            strm_path = Path(STRM_FILE_PATH) / (
                re.sub(r"/M\d{2}", "", file_path).removeprefix("/Media/") + ".strm"
            )
            create_strm_file(Path(file_path), strm_file_path=strm_path)

    def scan(self, path: Union[str, Sequence]) -> None:
        """发送扫描请求"""
        if isinstance(path, str):
            path = [path]
        _path = set(path)
        for p in set(path):
            lib = self.get_library_by_location(p)
            if not lib:
                logger.warning(f"Warning: library not found for {p}")
                _path.remove(p)
        if not _path:
            return

        payload = {"Updates": [{"Path": p} for p in _path]}

        headers = {"Content-Type": "application/json"}

        while True:
            try:
                res = requests.post(
                    url=f"{self.base_url}/Library/Media/Updated?api_key={self.token}",
                    data=json.dumps(payload),
                    headers=headers,
                )
                res.raise_for_status()
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.error(e)
                sleep(10)
                continue
            except requests.HTTPError:
                logger.error(f"Error: {res.status_code}, {res.text}")
                sleep(10)
                continue
            else:
                logger.info(f"Sent scan request successfully: {_path}")
                break


    # ---------------- Emby 用户管理（注册/密码/策略/删除）----------------
    @staticmethod
    def _create_policy(*, admin: bool = False, disabled: bool = False, block_folders: Optional[list] = None,
                       stream_limit: int = 2) -> dict:
        """
        参考 MiEmbybot 的策略：默认隐藏账号、限制转码下载、可远程访问；可按需屏蔽媒体库。
        """
        if block_folders is None:
            block_folders = ["播放列表"]
        return {
            "IsAdministrator": admin,
            "IsHidden": True,
            "IsHiddenRemotely": True,
            "IsDisabled": disabled,
            "EnableRemoteControlOfOtherUsers": False,
            "EnableSharedDeviceControl": False,
            "EnableRemoteAccess": True,
            "EnableLiveTvManagement": False,
            "EnableLiveTvAccess": True,
            "EnableMediaPlayback": True,
            "EnableAudioPlaybackTranscoding": False,
            "EnableVideoPlaybackTranscoding": False,
            "EnablePlaybackRemuxing": False,
            "EnableContentDeletion": False,
            "EnableContentDownloading": False,
            "EnableSubtitleDownloading": False,
            "EnableSubtitleManagement": False,
            "EnableSyncTranscoding": False,
            "EnableMediaConversion": False,
            "EnableAllDevices": True,
            "SimultaneousStreamLimit": stream_limit,
            "BlockedMediaFolders": block_folders,
            # Jellyfin/Emby 不同版本可能支持不同字段，这里保持与 MiEmbybot 一致的最小集合
        }

    def create_user_with_password(self, username: str, password: str, *, apply_policy: bool = True,
                                   policy: Optional[dict] = None) -> dict:
        """
        使用自定义用户名+密码创建 Emby 用户。
        返回：{"emby_user_id": str, "username": str}
        抛出异常时请捕获 requests.HTTPError。
        """
        # 1) 创建用户
        url_new = f"{self.base_url}/Users/New"
        headers = {"Content-Type": "application/json", "X-Emby-Token": self.token}
        resp = requests.post(url_new, headers=headers, params={"api_key": self.token}, json={"Name": username})
        if resp.status_code >= 300:
            logger.error(f"Create user failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()
        data = resp.json() or {}
        user_id = data.get("Id")
        if not user_id:
            raise requests.HTTPError("Emby response missing Id for created user")

        # 2) 设置密码（兼容不同服务器版本字段）
        self.reset_password(user_id, password)

        # 3) 可选：应用默认策略
        if apply_policy:
            pol = policy if policy is not None else self._create_policy()
            self.set_policy(user_id, pol)

        return {"emby_user_id": user_id, "username": username}

    def reset_password(self, user_id: str, new_password: str) -> None:
        """
        重置/设置密码。优先尝试 {ResetPassword, NewPw}，如失败再尝试 {ResetPassword, NewPassword}。
        失败将抛出 requests.HTTPError。
        """
        url = f"{self.base_url}/Users/{user_id}/Password"
        headers = {"Content-Type": "application/json", "X-Emby-Token": self.token}

        def _post(payload: dict):
            return requests.post(url, headers=headers, params={"api_key": self.token}, json=payload)

        # 先试 NewPw
        r1 = _post({"ResetPassword": True, "NewPw": new_password, "CurrentPw": ""})
        if r1.status_code >= 300:
            logger.warning(f"reset_password(NewPw) failed: {r1.status_code} {r1.text}")
            # 再试 NewPassword
            r2 = _post({"ResetPassword": True, "NewPassword": new_password, "CurrentPassword": ""})
            if r2.status_code >= 300:
                logger.error(f"reset_password(NewPassword) failed: {r2.status_code} {r2.text}")
                r2.raise_for_status()

    def set_policy(self, user_id: str, policy: dict) -> None:
        """
        设置用户策略。
        失败将抛出 requests.HTTPError。
        """
        url = f"{self.base_url}/Users/{user_id}/Policy"
        headers = {"Content-Type": "application/json", "X-Emby-Token": self.token}
        resp = requests.post(url, headers=headers, params={"api_key": self.token}, json=policy)
        if resp.status_code >= 300:
            logger.error(f"set_policy failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()

        # 同时确保允许本地密码
        try:
            url_cfg = f"{self.base_url}/Users/{user_id}/Configuration"
            cfg = {"EnableLocalPassword": True}
            resp2 = requests.post(url_cfg, headers=headers, params={"api_key": self.token}, json=cfg)
            if resp2.status_code >= 300:
                logger.info(f"set policy config warn: {resp2.status_code} {resp2.text}")
        except Exception as e:
            logger.info(f"skip set configuration: {e}")

    def delete_user(self, user_id: str) -> None:
        """
        删除 Emby 用户。失败将抛出 requests.HTTPError。
        """
        url = f"{self.base_url}/Users/{user_id}"
        headers = {"X-Emby-Token": self.token}
        resp = requests.delete(url, headers=headers, params={"api_key": self.token})
        if resp.status_code >= 300:
            logger.error(f"delete_user failed: {resp.status_code} {resp.text}")
            resp.raise_for_status()


if __name__ == "__main__":
    e = Emby()
    e.create_strm_file_for_existed_items(filter=["TV Shows"])
