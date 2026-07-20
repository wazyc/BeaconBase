"""BeaconBase 共通例外定義。"""


class MonitoringError(Exception):
    """監視システムの基本例外クラス"""
    pass


class RetryableError(MonitoringError):
    """リトライ可能なエラーを示す例外クラス"""
    pass
