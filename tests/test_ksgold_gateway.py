from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("vnpy_ksgold.api", reason="缺少 Ksgold 原生扩展")

from vnpy.event import Event, EventEngine  # noqa: E402
from vnpy.trader.constant import (  # noqa: E402
    Direction,
    Exchange,
    Offset,
    OrderType,
    Product,
    Status,
)
from vnpy.trader.event import EVENT_TIMER  # noqa: E402
from vnpy.trader.object import (  # noqa: E402
    AccountData,
    CancelRequest,
    ContractData,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)
from vnpy_ksgold.api import (  # noqa: E402
    KS_BUY,
    KS_Entrust_All_Cancel,
    KS_Entrust_All_Done,
    KS_Entrust_Error,
    KS_Entrust_In,
    KS_Entrust_Part_Done,
    KS_Entrust_Sending,
    KS_Entrust_Wait_Cancel,
    KS_P_OFFSET,
    KS_P_OPEN,
    KS_SELL,
)
from vnpy_ksgold.gateway import ksgold_gateway  # noqa: E402
from vnpy_ksgold.gateway.ksgold_gateway import (  # noqa: E402
    CHINA_TZ,
    MAX_FLOAT,
    KsgoldGateway,
    KsgoldMdApi,
    KsgoldTdApi,
    adjust_price,
)


class Sink:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.ticks: list[TickData] = []
        self.contracts: list[ContractData] = []
        self.orders: list[OrderData] = []
        self.trades: list[TradeData] = []
        self.positions: list[PositionData] = []
        self.accounts: list[AccountData] = []

    def attach(self, gateway: KsgoldGateway) -> None:
        gateway.write_log = self.logs.append  # type: ignore[method-assign]
        gateway.on_tick = self.ticks.append  # type: ignore[method-assign]
        gateway.on_contract = self.contracts.append  # type: ignore[method-assign]
        gateway.on_order = self.orders.append  # type: ignore[method-assign]
        gateway.on_trade = self.trades.append  # type: ignore[method-assign]
        gateway.on_position = self.positions.append  # type: ignore[method-assign]
        gateway.on_account = self.accounts.append  # type: ignore[method-assign]


class CallRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def patch(self, monkeypatch: pytest.MonkeyPatch, api: object, names: list[str]) -> None:
        for name in names:
            monkeypatch.setattr(api, name, self.make_stub(name))

    def make_stub(self, name: str) -> Callable[..., int]:
        def stub(*args: Any) -> int:
            self.calls.append((name, args[0] if args else None))
            return 0
        return stub

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


TD_METHODS: list[str] = [
    "createGoldTraderApi",
    "subscribePrivateTopic",
    "subscribePublicTopic",
    "registerFront",
    "init",
    "exit",
    "reqUserLogin",
    "reqQryInstrument",
    "reqOrderInsert",
    "reqOrderAction",
    "reqQryTradingAccount",
    "reqQryInvestorPosition",
]

MD_METHODS: list[str] = [
    "createGoldQutoApi",
    "registerFront",
    "init",
    "exit",
    "reqUserLogin",
    "subscribeMarketData",
]


@pytest.fixture(autouse=True)
def clear_contracts() -> Iterator[None]:
    ksgold_gateway.symbol_contract_map.clear()
    ksgold_gateway.symbol_market_map.clear()
    yield
    ksgold_gateway.symbol_contract_map.clear()
    ksgold_gateway.symbol_market_map.clear()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ksgold_gateway, "sleep", lambda _seconds: None)


@pytest.fixture
def sink() -> Sink:
    return Sink()


@pytest.fixture
def recorder() -> CallRecorder:
    return CallRecorder()


@pytest.fixture
def gateway(sink: Sink, recorder: CallRecorder, monkeypatch: pytest.MonkeyPatch) -> KsgoldGateway:
    engine: EventEngine = EventEngine()
    gateway: KsgoldGateway = KsgoldGateway(engine, "KSGOLD")
    sink.attach(gateway)
    recorder.patch(monkeypatch, gateway.td_api, TD_METHODS)
    recorder.patch(monkeypatch, gateway.md_api, MD_METHODS)
    return gateway


@pytest.fixture
def td_api(gateway: KsgoldGateway) -> KsgoldTdApi:
    return gateway.td_api


@pytest.fixture
def md_api(gateway: KsgoldGateway) -> KsgoldMdApi:
    return gateway.md_api


def add_contract(symbol: str = "Au99.99") -> ContractData:
    contract: ContractData = ContractData(
        symbol=symbol,
        exchange=Exchange.SGE,
        name=symbol,
        product=Product.SPOT,
        size=1000,
        pricetick=0.01,
        gateway_name="KSGOLD",
    )
    ksgold_gateway.symbol_contract_map[symbol] = contract
    ksgold_gateway.symbol_market_map[symbol] = "00"
    return contract


def order_request(offset: Offset = Offset.OPEN) -> OrderRequest:
    return OrderRequest(
        symbol="Au99.99",
        exchange=Exchange.SGE,
        direction=Direction.LONG,
        type=OrderType.LIMIT,
        volume=2,
        price=500,
        offset=offset,
    )


def instrument_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "InstID": "Au99.99",
        "Name": "黄金",
        "Unit": 1000,
        "Tick": 0.01,
        "MarketID": "00",
    }
    data.update(overrides)
    return data


def rtn_order(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "InstID": "Au99.99",
        "FrontID": 1,
        "SessionID": 2,
        "OrderRef": "7",
        "LocalOrderNo": "L7",
        "OrderNo": "SYS1",
        "EntrustTime": "09:30:00",
        "BuyOrSell": KS_BUY,
        "OffsetFlag": KS_P_OPEN,
        "Price": 500,
        "Amount": 2,
        "MatchQty": 0,
        "Status": KS_Entrust_In,
    }
    data.update(overrides)
    return data


def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2025, 10, 10, 9, 30, 0)

    monkeypatch.setattr(ksgold_gateway, "datetime", FrozenDateTime)


def connect_setting(login_kind: str, td_address: str, md_address: str) -> dict[str, str]:
    return {
        "用户名": "u1",
        "密码": "p1",
        "交易服务器": td_address,
        "行情服务器": md_address,
        "账号类型": login_kind,
    }


def test_adjust_price_replaces_max_float() -> None:
    assert adjust_price(MAX_FLOAT) == 0
    assert adjust_price(500) == 500


def test_connect_bank_account_prefixes_tcp(gateway: KsgoldGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, tuple[Any, ...]] = {}
    monkeypatch.setattr(gateway.td_api, "connect", lambda *args: seen.__setitem__("td", args))
    monkeypatch.setattr(gateway.md_api, "connect", lambda *args: seen.__setitem__("md", args))

    gateway.connect(connect_setting("银行账号", "127.0.0.1:1234", "ssl://127.0.0.1:5678"))

    assert seen["td"][0] == "tcp://127.0.0.1:1234"
    assert seen["td"][3] == 1
    assert seen["md"][0] == "ssl://127.0.0.1:5678"
    assert seen["md"][3] == 1


def test_connect_gold_account_keeps_tcp_prefix(gateway: KsgoldGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, tuple[Any, ...]] = {}
    monkeypatch.setattr(gateway.td_api, "connect", lambda *args: seen.__setitem__("td", args))
    monkeypatch.setattr(gateway.md_api, "connect", lambda *args: seen.__setitem__("md", args))

    gateway.connect(connect_setting("黄金账号", "tcp://127.0.0.1:1234", "10.0.0.2:9"))

    assert seen["td"][0] == "tcp://127.0.0.1:1234"
    assert seen["td"][3] == 2
    assert seen["md"][0] == "tcp://10.0.0.2:9"
    assert seen["md"][3] == 2


def test_timer_skips_first_tick_then_rotates_queries(gateway: KsgoldGateway) -> None:
    calls: list[str] = []

    def query_account() -> None:
        calls.append("account")

    def query_position() -> None:
        calls.append("position")

    gateway.query_account = query_account  # type: ignore[method-assign]
    gateway.query_position = query_position  # type: ignore[method-assign]
    gateway.init_query()
    event: Event = Event(EVENT_TIMER)

    gateway.process_timer_event(event)
    assert calls == []

    gateway.process_timer_event(event)
    gateway.process_timer_event(event)
    gateway.process_timer_event(event)

    assert calls == ["account", "position"]


def test_front_connected_logs_in(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    td_api.userid = "u1"
    td_api.password = "p1"
    td_api.login_type = 1
    td_api.onFrontConnected(0)

    request: dict[str, Any] = recorder.calls[0][1]
    assert recorder.names() == ["reqUserLogin"]
    assert request["AccountID"] == "u1"
    assert request["LoginType"] == 1


def test_login_queries_instrument(td_api: KsgoldTdApi, sink: Sink, recorder: CallRecorder) -> None:
    td_api.onRspUserLogin(
        {"FrontID": 1, "SessionID": 2, "SeatNo": 9, "TradeCode": "TC"},
        {"ErrorID": 0, "ErrorMsg": ""},
        1,
        True,
    )

    assert td_api.frontid == 1
    assert td_api.sessionid == 2
    assert td_api.seat_no == 9
    assert td_api.trade_code == "TC"
    assert td_api.login_status is True
    assert recorder.names() == ["reqQryInstrument"]
    assert "交易服务器登录成功" in sink.logs


def test_login_failed_does_not_send_again(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    td_api.onRspUserLogin({}, {"ErrorID": 1, "ErrorMsg": "bad"}, 1, True)
    assert td_api.login_failed is True

    td_api.login()

    assert recorder.calls == []


def test_send_order_requires_offset(td_api: KsgoldTdApi, sink: Sink, recorder: CallRecorder) -> None:
    assert td_api.send_order(order_request(offset=Offset.NONE)) == ""
    assert recorder.names() == []
    assert sink.logs == ["请选择开平方向"]


def test_send_order_returns_local_id(td_api: KsgoldTdApi, sink: Sink, recorder: CallRecorder) -> None:
    add_contract()
    td_api.frontid = 1
    td_api.sessionid = 2
    td_api.seat_no = 9
    td_api.userid = "u1"
    td_api.trade_code = "TC"

    vt_orderid: str = td_api.send_order(order_request())

    assert vt_orderid == "KSGOLD.1_2_1"
    request: dict[str, Any] = recorder.calls[0][1]
    assert request["SeatID"] == 9
    assert request["ClientID"] == "u1"
    assert request["TradeCode"] == "TC"
    assert request["InstID"] == "Au99.99"
    assert request["BuyOrSell"] == KS_BUY
    assert request["OffsetFlag"] == KS_P_OPEN
    assert request["Amount"] == 2
    assert request["MarketID"] == "00"
    assert request["OrderRef"] == "1"
    assert request["SessionID"] == 2
    assert sink.orders[0].status == Status.SUBMITTING
    assert sink.orders[0].orderid == "1_2_1"


def test_cancel_order_uses_local_no(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    td_api.orderid_localid_map["1_2_7"] = "L7"
    td_api.cancel_order(CancelRequest(orderid="1_2_7", symbol="Au99.99", exchange=Exchange.SGE))

    assert recorder.calls == [("reqOrderAction", {"LocalOrderNo": "L7"})]


def test_query_account_sends_empty_request(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    td_api.query_account()

    assert recorder.calls == [("reqQryTradingAccount", {})]


def test_query_position_waits_for_contracts(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    td_api.query_position()

    assert recorder.calls == []


def test_query_position_sends_empty_request(td_api: KsgoldTdApi, recorder: CallRecorder) -> None:
    add_contract()
    td_api.query_position()

    assert recorder.calls == [("reqQryInvestorPosition", {})]


def test_instrument_spot_cached(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryInstrument(instrument_data(), {}, 1, True)

    contract: ContractData = sink.contracts[0]
    assert contract.symbol == "Au99.99"
    assert contract.exchange == Exchange.SGE
    assert contract.product == Product.SPOT
    assert contract.size == 1000
    assert td_api.contract_inited is True
    assert ksgold_gateway.symbol_market_map["Au99.99"] == "00"


def test_order_before_contract_is_replayed(
    td_api: KsgoldTdApi,
    sink: Sink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze_now(monkeypatch)
    td_api.onRtnOrder(rtn_order())
    assert sink.orders == []

    td_api.onRspQryInstrument(instrument_data(), {}, 1, True)

    order: OrderData = sink.orders[0]
    assert order.orderid == "1_2_7"
    assert order.status == Status.NOTTRADED
    assert order.datetime == datetime(2025, 10, 10, 9, 30, tzinfo=CHINA_TZ)
    assert td_api.order_data == []
    assert td_api.orderid_localid_map["1_2_7"] == "L7"
    assert td_api.sysid_orderid_map["SYS1"] == "1_2_7"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (KS_Entrust_Sending, Status.SUBMITTING),
        (KS_Entrust_In, Status.NOTTRADED),
        (KS_Entrust_Part_Done, Status.PARTTRADED),
        (KS_Entrust_All_Done, Status.ALLTRADED),
        (KS_Entrust_All_Cancel, Status.CANCELLED),
        (KS_Entrust_Error, Status.REJECTED),
        (KS_Entrust_Wait_Cancel, Status.SUBMITTING),
    ],
)
def test_order_maps_local_id_and_status(
    td_api: KsgoldTdApi,
    sink: Sink,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected: Status,
) -> None:
    freeze_now(monkeypatch)
    add_contract()
    td_api.contract_inited = True
    td_api.onRtnOrder(rtn_order(Status=status, MatchQty=1, OffsetFlag=48, BuyOrSell=KS_SELL))

    order: OrderData = sink.orders[0]
    assert order.orderid == "1_2_7"
    assert order.status == expected
    assert order.offset == Offset.OPEN
    assert order.direction == Direction.SHORT
    assert order.traded == 1


def test_order_string_offset_maps_close(
    td_api: KsgoldTdApi,
    sink: Sink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze_now(monkeypatch)
    add_contract()
    td_api.contract_inited = True
    td_api.onRtnOrder(rtn_order(OffsetFlag=KS_P_OFFSET))

    assert sink.orders[0].offset == Offset.CLOSE


def test_insert_failure_prints_and_rejects(
    td_api: KsgoldTdApi,
    sink: Sink,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_contract()
    td_api.frontid = 1
    td_api.sessionid = 2
    td_api.onRspOrderInsert(
        {
            "OrderRef": "7",
            "InstID": "Au99.99",
            "BuyOrSell": KS_BUY,
            "OffsetFlag": KS_P_OPEN,
            "Price": 500,
            "Amount": 2,
        },
        {"ErrorID": 1, "ErrorMsg": "bad"},
        1,
        True,
    )

    assert sink.orders[0].orderid == "1_2_7"
    assert sink.orders[0].status == Status.REJECTED
    assert "交易委托失败" in sink.logs[0]
    assert "on order" in capsys.readouterr().out


def test_trade_uses_sysid_map(td_api: KsgoldTdApi, sink: Sink, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_now(monkeypatch)
    add_contract()
    td_api.contract_inited = True
    td_api.sysid_orderid_map["SYS1"] = "1_2_7"
    td_api.onRtnTrade({
        "InstID": "Au99.99",
        "OrderNo": "SYS1",
        "MatchNo": "M1",
        "BuyOrSell": KS_BUY,
        "OffSetFlag": KS_P_OFFSET,
        "Price": 501,
        "Volume": 1,
        "MatchTime": "09:31:00",
    })

    trade: TradeData = sink.trades[0]
    assert trade.orderid == "1_2_7"
    assert trade.tradeid == "M1"
    assert trade.offset == Offset.CLOSE
    assert trade.datetime == datetime(2025, 10, 10, 9, 31, tzinfo=CHINA_TZ)


def test_trade_before_contract_is_replayed(td_api: KsgoldTdApi, sink: Sink, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_now(monkeypatch)
    td_api.sysid_orderid_map["SYS1"] = "1_2_7"
    td_api.onRtnTrade({
        "InstID": "Au99.99",
        "OrderNo": "SYS1",
        "MatchNo": "M1",
        "BuyOrSell": KS_SELL,
        "OffSetFlag": KS_P_OPEN,
        "Price": 501,
        "Volume": 1,
        "MatchTime": "09:31:00",
    })
    assert sink.trades == []

    td_api.onRspQryInstrument(instrument_data(), {}, 1, True)

    assert sink.trades[0].tradeid == "M1"
    assert sink.trades[0].direction == Direction.SHORT


def test_position_pushes_long_and_short(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryInvestorPosition(
        {
            "InstID": "Au99.99",
            "LongOpenAvgPrice": 500,
            "LastLong": 1,
            "LongPosiVol": 3,
            "LongPosiFrozen": 1,
            "ShortOpenAvgPrice": 510,
            "LastShort": 2,
            "ShortPosiVol": 4,
            "ShortPosiFrozen": 0,
        },
        {"ErrorID": 0, "ErrorMsg": ""},
        1,
        True,
    )

    long_position: PositionData = sink.positions[0]
    short_position: PositionData = sink.positions[1]
    assert long_position.direction == Direction.LONG
    assert long_position.volume == 3
    assert long_position.yd_volume == 1
    assert long_position.price == 500
    assert long_position.frozen == 1
    assert long_position.exchange == Exchange.SGE
    assert short_position.direction == Direction.SHORT
    assert short_position.volume == 4
    assert short_position.yd_volume == 2
    assert short_position.price == 510


def test_position_error_10001_is_silent(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryInvestorPosition({}, {"ErrorID": 10001, "ErrorMsg": "none"}, 1, True)

    assert sink.positions == []
    assert sink.logs == []


def test_position_error_is_logged(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryInvestorPosition({}, {"ErrorID": 2, "ErrorMsg": "bad"}, 1, True)

    assert sink.positions == []
    assert "查询持仓失败" in sink.logs[0]


def test_account_sums_available_and_frozen(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryTradingAccount({
        "ClientID": "u1",
        "TotalFrozen": 10,
        "AvailCap": 90,
    }, {}, 1, True)

    account: AccountData = sink.accounts[0]
    assert account.accountid == "u1"
    assert account.balance == 100
    assert account.frozen == 10
    assert account.available == 90


def test_account_without_client_id_is_ignored(td_api: KsgoldTdApi, sink: Sink) -> None:
    td_api.onRspQryTradingAccount({}, {}, 1, True)

    assert sink.accounts == []


def test_depth_uses_quote_time(md_api: KsgoldMdApi, sink: Sink) -> None:
    add_contract()
    md_api.onRtnDepthMarketData({
        "InstID": "Au99.99",
        "QuoteDate": "20251010",
        "QuoteTime": "09:30:00",
        "UpdateMillisec": 500,
        "Volume": 10,
        "OpenInt": 8,
        "Last": 500,
        "highLimit": 550,
        "lowLimit": 450,
        "Open": MAX_FLOAT,
        "Highest": 510,
        "Low": 490,
        "PreClose": 495,
        "Bid1": 499,
        "Ask1": 501,
        "BidLot1": 5,
        "AskLot1": 6,
        "BidLot2": 0,
        "AskLot2": 0,
    })

    tick: TickData = sink.ticks[0]
    assert tick.datetime == datetime(2025, 10, 10, 9, 30, 0, 500000, tzinfo=CHINA_TZ)
    assert tick.last_price == 500
    assert tick.open_price == 0
    assert tick.bid_price_1 == 499
    assert tick.ask_volume_1 == 6
    assert tick.open_interest == 8
    assert tick.bid_price_2 == 0


def test_depth_copies_five_levels_when_second_lot_present(md_api: KsgoldMdApi, sink: Sink) -> None:
    add_contract()
    md_api.onRtnDepthMarketData({
        "InstID": "Au99.99",
        "QuoteDate": "20251010",
        "QuoteTime": "09:30:00",
        "UpdateMillisec": 0,
        "Volume": 1,
        "OpenInt": 1,
        "Last": 500,
        "highLimit": 550,
        "lowLimit": 450,
        "Open": 500,
        "Highest": 510,
        "Low": 490,
        "PreClose": 495,
        "Bid1": 499,
        "Ask1": 501,
        "BidLot1": 1,
        "AskLot1": 1,
        "Bid2": 498,
        "Bid3": 497,
        "Bid4": 496,
        "Bid5": 495,
        "Ask2": 502,
        "Ask3": 503,
        "Ask4": 504,
        "Ask5": 505,
        "BidLot2": 3,
        "BidLot3": 0,
        "BidLot4": 0,
        "BidLot5": 0,
        "AskLot2": 4,
        "AskLot3": 0,
        "AskLot4": 0,
        "AskLot5": 0,
    })

    tick: TickData = sink.ticks[0]
    assert tick.datetime == datetime(2025, 10, 10, 9, 30, tzinfo=CHINA_TZ)
    assert tick.bid_price_5 == 495
    assert tick.ask_volume_2 == 4
    assert tick.bid_volume_2 == 3


def test_depth_without_contract_is_ignored(md_api: KsgoldMdApi, sink: Sink) -> None:
    md_api.onRtnDepthMarketData({"InstID": "Au99.99"})

    assert sink.ticks == []


def test_subscribe_before_login_replays_without_reqid(
    md_api: KsgoldMdApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Any, ...]] = []

    def record(*args: Any) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(md_api, "subscribeMarketData", record)
    md_api.subscribe(SubscribeRequest(symbol="Au99.99", exchange=Exchange.SGE))
    assert seen == []
    assert "Au99.99" in md_api.subscribed

    md_api.onRspUserLogin({}, {"ErrorID": 0, "ErrorMsg": ""}, 1, True)

    assert seen == [("Au99.99",)]


def test_subscribe_after_login_passes_reqid(md_api: KsgoldMdApi, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def record(*args: Any) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(md_api, "subscribeMarketData", record)
    md_api.login_status = True
    md_api.subscribe(SubscribeRequest(symbol="Au99.99", exchange=Exchange.SGE))

    assert seen == [("Au99.99", 1)]


def test_md_front_connected_logs_in(md_api: KsgoldMdApi, recorder: CallRecorder) -> None:
    md_api.userid = "u1"
    md_api.password = "p1"
    md_api.login_type = 2
    md_api.onFrontConnected(0)

    assert recorder.names() == ["reqUserLogin"]
    assert recorder.calls[0][1]["LoginType"] == 2


def test_close_without_connection_does_not_exit(
    td_api: KsgoldTdApi,
    md_api: KsgoldMdApi,
    recorder: CallRecorder,
) -> None:
    td_api.close()
    md_api.close()

    assert recorder.calls == []
