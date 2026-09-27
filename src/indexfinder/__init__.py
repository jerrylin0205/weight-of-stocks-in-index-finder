"""stock-index-finder: 股票 → 指數 / ETF / 權重 查詢。"""

__version__ = "0.1.0"


def _use_os_trust_store() -> None:
    """改用作業系統的憑證信任庫。

    部分發行商（例如元大投信）的憑證鏈缺少 Subject Key Identifier，
    OpenSSL 3.x 會拒絕，但 macOS / Windows 的驗證器可以接受。
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass


_use_os_trust_store()
