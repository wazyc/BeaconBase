"""設定YAMLの読込と includes_dir による分割マージ。

メイン設定ファイルのルートに includes_dir（文字列）を書くと、
そのディレクトリ直下の *.yaml / *.yml をファイル名ソート順でマージし、
続けてメイン本体をマージする。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

from exceptions import MonitoringError

CONFIG_INCLUDES_DIR_KEY = "includes_dir"
_YAML_SUFFIXES = {".yaml", ".yml"}


def deep_merge_config(left: Any, right: Any) -> Any:
    """設定フラグメントをマージする。

    dict同士はキー単位で再帰マージ、list同士は連結し、
    それ以外の組み合わせでは右側（後から読んだ値）を採用する。

    Args:
        left: マージ先に既にある値
        right: 上書き・拡張に使う値

    Returns:
        マージ結果（listの場合は新しいリストインスタンス）
    """
    if right is None:
        return left
    if left is None:
        return right
    if isinstance(left, dict) and isinstance(right, dict):
        out: Dict[str, Any] = dict(left)
        for key, rval in right.items():
            if key in out:
                out[key] = deep_merge_config(out[key], rval)
            else:
                out[key] = rval
        return out
    if isinstance(left, list) and isinstance(right, list):
        return list(left) + list(right)
    return right


def _is_path_under_root(resolved_path: str, root_dir: str) -> bool:
    """解決済みパスが root_dir 配下（同一も含む）にあるか検証する。"""
    try:
        p = Path(resolved_path).resolve()
        r = Path(root_dir).resolve()
        p.relative_to(r)
        return True
    except ValueError:
        return False


def _resolve_path_from_base(base_dir: str, path_spec: str) -> str:
    """相対または絶対パスを base_dir 基準で絶対パスに解決する。"""
    if os.path.isabs(path_spec):
        return os.path.abspath(os.path.normpath(path_spec))
    return os.path.abspath(os.path.normpath(os.path.join(base_dir, path_spec)))


def _load_yaml_mapping(file_path: str) -> Dict[str, Any]:
    """単一YAMLを辞書として読み込む。"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_doc = yaml.safe_load(f)
    except OSError as e:
        raise MonitoringError(
            f"設定ファイルの読み込みに失敗しました: {file_path}: {e}"
        ) from e
    except yaml.YAMLError as e:
        raise MonitoringError(
            f"YAML の解析に失敗しました: {file_path}: {e}"
        ) from e

    if raw_doc is None:
        return {}
    if not isinstance(raw_doc, dict):
        raise MonitoringError(
            f"設定ファイルのルートはマッピング（辞書）である必要があります: {file_path}"
        )
    return raw_doc


def _list_yaml_files_in_dir(dir_path: str) -> List[str]:
    """ディレクトリ直下の *.yaml / *.yml をファイル名ソートで返す。

    Raises:
        MonitoringError: ディレクトリが存在しない、または YAML 以外のファイルのみがある場合
    """
    if not os.path.isdir(dir_path):
        raise MonitoringError(f"includes_dir が見つかりません: {dir_path}")

    entries = []
    non_yaml: List[str] = []
    try:
        names = os.listdir(dir_path)
    except OSError as e:
        raise MonitoringError(
            f"includes_dir の読み取りに失敗しました: {dir_path}: {e}"
        ) from e

    for name in names:
        full = os.path.join(dir_path, name)
        if not os.path.isfile(full):
            continue
        suffix = Path(name).suffix.lower()
        if suffix in _YAML_SUFFIXES:
            entries.append(full)
        else:
            non_yaml.append(name)

    if not entries and non_yaml:
        raise MonitoringError(
            f"includes_dir に YAML ファイルがありません"
            f"（非YAMLのみ）: {dir_path}"
        )

    entries.sort(key=lambda p: os.path.basename(p).lower())
    return entries


def _load_fragment_file(file_path: str) -> Dict[str, Any]:
    """分割設定ファイルを読み込む。includes_dir があればエラー。"""
    doc = _load_yaml_mapping(file_path)
    if CONFIG_INCLUDES_DIR_KEY in doc:
        raise MonitoringError(
            f"分割設定ファイルに '{CONFIG_INCLUDES_DIR_KEY}' は指定できません: {file_path}"
        )
    return doc


def load_merged_yaml_config(config_path: str) -> Dict[str, Any]:
    """メイン設定ファイルと includes_dir から最終設定辞書を構築する。

    エントリとなる設定ファイルと同じディレクトリをルートとし、
    includes_dir もそのディレクトリ配下に制限する。

    Args:
        config_path: メインの設定YAMLパス（相対可）

    Returns:
        includes_dir を除いたマージ済み設定

    Raises:
        MonitoringError: ファイル不存在、YAML エラー、検証エラー
    """
    entry_abs = os.path.abspath(config_path)
    if not os.path.isfile(entry_abs):
        raise MonitoringError(f"設定ファイルが見つかりません: {config_path}")

    root_dir = os.path.dirname(entry_abs)
    raw_doc = _load_yaml_mapping(entry_abs)
    includes_dir_spec = raw_doc.pop(CONFIG_INCLUDES_DIR_KEY, None)

    merged: Dict[str, Any] = {}

    if includes_dir_spec is not None:
        if not isinstance(includes_dir_spec, str) or not includes_dir_spec.strip():
            raise MonitoringError(
                f"'{CONFIG_INCLUDES_DIR_KEY}' は空でない文字列で指定してください: {entry_abs}"
            )
        resolved_dir = _resolve_path_from_base(root_dir, includes_dir_spec.strip())
        if not _is_path_under_root(resolved_dir, root_dir):
            raise MonitoringError(
                f"include が許可されたディレクトリ外を指しています: "
                f"{includes_dir_spec!r} -> {resolved_dir}"
            )
        for frag_path in _list_yaml_files_in_dir(resolved_dir):
            if not _is_path_under_root(frag_path, root_dir):
                raise MonitoringError(
                    f"include が許可されたディレクトリ外を指しています: {frag_path}"
                )
            fragment = _load_fragment_file(frag_path)
            merged = deep_merge_config(merged, fragment)

    merged = deep_merge_config(merged, raw_doc)
    return merged
