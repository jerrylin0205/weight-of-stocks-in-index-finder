from indexfinder.normalize import looks_taiwanese, normalize_ticker, split_query


def test_us_basic():
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker("  MSFT ") == "MSFT"


def test_us_share_class_and_suffix():
    # iShares 用 BRKB，SPDR 用 BRK.B —— 都要 normalize 成同一個
    assert normalize_ticker("BRK.B", "US") == "BRKB"
    assert normalize_ticker("BRK-B", "US") == "BRKB"
    assert normalize_ticker("BRKB") == "BRKB"
    assert normalize_ticker("AAPL.O") == "AAPL"
    assert normalize_ticker("AAPL US") == "AAPL"


def test_taiwan():
    assert normalize_ticker("2330") == "2330"
    assert normalize_ticker("2330.TW") == "2330"
    assert normalize_ticker("2330 TT", "TW") == "2330"
    assert normalize_ticker("00878.TWO") == "00878"
    assert normalize_ticker("00713", "TW") == "00713"


def test_looks_taiwanese():
    assert looks_taiwanese("2330")
    assert looks_taiwanese("2330.TW")
    assert not looks_taiwanese("AAPL")
    assert not looks_taiwanese("BRK.B")


def test_chinese_name_not_a_ticker():
    assert normalize_ticker("台積電", "TW") == ""


def test_split_query():
    assert split_query("AAPL MSFT") == ["AAPL", "MSFT"]
    assert split_query("2330,2317; 2454") == ["2330", "2317", "2454"]
    assert split_query("台積電、鴻海") == ["台積電", "鴻海"]
    assert split_query("   ") == []
