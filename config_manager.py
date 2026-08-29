"""WEB から監視設定ファイルを安全に読み書きする。

メイン YAML と includes_dir 直下の断片だけを対象にする。
パスはメイン設定ファイルのディレクトリ配下に限定する。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

from config_loader import (
    CONFIG_INCLUDES_DIR_KEY,
    _is_path_under_root,
    _list_yaml_files_in_dir,
    _load_yaml_mapping,
    _resolve_path_from_base,
    load_merged_yaml_config,
)
from exceptions import MonitoringError

ENTRY_FILE_ID = "entry"


def _entry_abs(config_path: str) -> str:
    path = os.path.abspath(config_path)
    if not os.path.isfile(path):
        raise MonitoringError(f"設定ファイルが見つかりません: {config_path}")
    return path


def _root_dir(config_path: str) -> str:
    return os.path.dirname(_entry_abs(config_path))


def _includes_dir(config_path: str) -> Optional[str]:
    """メイン YAML の includes_dir を解決する。無ければ None。"""
    entry = _entry_abs(config_path)
    raw = _load_yaml_mapping(entry)
    spec = raw.get(CONFIG_INCLUDES_DIR_KEY)
    if spec is None:
        return None
    if not isinstance(spec, str) or not spec.strip():
        raise MonitoringError(
            f"'{CONFIG_INCLUDES_DIR_KEY}' は空でない文字列で指定してください"
        )
    resolved = _resolve_path_from_base(_root_dir(config_path), spec.strip())
    if not _is_path_under_root(resolved, _root_dir(config_path)):
        raise MonitoringError(
            f"include が許可されたディレクトリ外を指しています: {spec!r}"
        )
    return resolved


def _fragment_id(filename: str) -> str:
    return f"fragment:{filename}"


def resolve_file_path(config_path: str, file_id: str) -> str:
    """file_id から実ファイルパスを解決する。"""
    entry = _entry_abs(config_path)
    root = _root_dir(config_path)
    if file_id == ENTRY_FILE_ID:
        return entry
    if file_id.startswith("fragment:"):
        name = file_id.split(":", 1)[1]
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise MonitoringError(f"不正な設定ファイル ID です: {file_id}")
        includes = _includes_dir(config_path)
        if not includes:
            raise MonitoringError("includes_dir が無いため断片を開けません")
        path = os.path.join(includes, name)
        if not _is_path_under_root(path, root):
            raise MonitoringError(f"許可されていないパスです: {file_id}")
        if not os.path.isfile(path):
            raise MonitoringError(f"設定ファイルが見つかりません: {name}")
        return path
    raise MonitoringError(f"不明な設定ファイル ID です: {file_id}")


def list_config_files(config_path: str) -> List[Dict[str, str]]:
    """編集可能な設定ファイル一覧を返す。"""
    entry = _entry_abs(config_path)
    files = [
        {
            "id": ENTRY_FILE_ID,
            "label": os.path.basename(entry),
            "kind": "entry",
            "path": entry,
        }
    ]
    includes = _includes_dir(config_path)
    if includes and os.path.isdir(includes):
        for frag in _list_yaml_files_in_dir(includes):
            name = os.path.basename(frag)
            files.append(
                {
                    "id": _fragment_id(name),
                    "label": f"{os.path.basename(includes)}/{name}",
                    "kind": "fragment",
                    "path": frag,
                }
            )
    return files


def read_config_text(config_path: str, file_id: str) -> str:
    path = resolve_file_path(config_path, file_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise MonitoringError(f"設定の読み込みに失敗しました: {e}") from e


def write_config_text(config_path: str, file_id: str, content: str) -> None:
    """YAML として解析できることを確認してから保存する。"""
    path = resolve_file_path(config_path, file_id)
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise MonitoringError(f"YAML の解析に失敗しました: {e}") from e

    if parsed is not None and not isinstance(parsed, dict):
        raise MonitoringError("設定ファイルのルートはマッピング（辞書）である必要があります")

    if file_id != ENTRY_FILE_ID and isinstance(parsed, dict) and CONFIG_INCLUDES_DIR_KEY in parsed:
        raise MonitoringError(
            f"分割設定ファイルに '{CONFIG_INCLUDES_DIR_KEY}' は指定できません"
        )

    # エントリ保存後にマージ＋検証できるよう、一時的に書き込まず先に内容検査する
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
            if content and not content.endswith("\n"):
                f.write("\n")
    except OSError as e:
        raise MonitoringError(f"設定の保存に失敗しました: {e}") from e

    # 保存後にマージ可能か確認する。失敗してもファイルは残す（ユーザーが直せるようにする）
    try:
        load_merged_yaml_config(config_path)
    except MonitoringError as e:
        raise MonitoringError(
            f"保存はしましたが、マージ後の設定に問題があります: {e}"
        ) from e


def create_fragment(config_path: str, filename: str, content: str = "") -> str:
    """includes_dir 直下に断片 YAML を新規作成し、file_id を返す。"""
    includes = _includes_dir(config_path)
    if not includes:
        raise MonitoringError("includes_dir が設定されていないため断片を作成できません")
    name = os.path.basename(filename.strip())
    if not name.lower().endswith((".yaml", ".yml")):
        raise MonitoringError("ファイル名は .yaml または .yml で終わる必要があります")
    if name != filename.strip() or "/" in filename or "\\" in filename:
        raise MonitoringError("ファイル名にパス区切りは使えません")
    os.makedirs(includes, exist_ok=True)
    path = os.path.join(includes, name)
    if not _is_path_under_root(path, _root_dir(config_path)):
        raise MonitoringError("許可されていないパスです")
    if os.path.exists(path):
        raise MonitoringError(f"すでに存在します: {name}")

    body = content if content.strip() else "# BeaconBase 分割設定\n"
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise MonitoringError(f"YAML の解析に失敗しました: {e}") from e
    if parsed is not None and not isinstance(parsed, dict):
        raise MonitoringError("設定ファイルのルートはマッピング（辞書）である必要があります")
    if isinstance(parsed, dict) and CONFIG_INCLUDES_DIR_KEY in parsed:
        raise MonitoringError(
            f"分割設定ファイルに '{CONFIG_INCLUDES_DIR_KEY}' は指定できません"
        )

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
            if not body.endswith("\n"):
                f.write("\n")
    except OSError as e:
        raise MonitoringError(f"設定の保存に失敗しました: {e}") from e

    try:
        load_merged_yaml_config(config_path)
    except MonitoringError as e:
        raise MonitoringError(
            f"保存はしましたが、マージ後の設定に問題があります: {e}"
        ) from e
    return _fragment_id(name)


def delete_fragment(config_path: str, file_id: str) -> None:
    """断片ファイルを削除する。エントリは削除できない。"""
    if file_id == ENTRY_FILE_ID:
        raise MonitoringError("メイン設定ファイルは削除できません")
    path = resolve_file_path(config_path, file_id)
    try:
        os.remove(path)
    except OSError as e:
        raise MonitoringError(f"削除に失敗しました: {e}") from e


def load_merged_for_display(config_path: str) -> Dict[str, Any]:
    """画面表示用にマージ済み設定を返す。"""
    return load_merged_yaml_config(config_path)


def dump_merged_yaml(config_path: str) -> str:
    """マージ済み設定を YAML 文字列にする（読み取り専用プレビュー）。"""
    merged = load_merged_for_display(config_path)
    return yaml.safe_dump(
        merged,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
