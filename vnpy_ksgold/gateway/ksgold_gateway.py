from pathlib import Path
import pytz
import sys
from datetime import datetime
from time import sleep
from typing import Dict, List, Set, Tuple, Any

from vnpy.event import EventEngine
from ..api import (
    MdApi,
    TdApi,
    KS_Entrust_Sending,
    KS_Entrust_Waiting,
    KS_Entrust_Error,
    KS_Entrust_In,
    KS_Entrust_Part_Done,
    KS_Entrust_All_Done,
    KS_Entrust_All_Cancel,
    KS_Entrust_Part_Done_Cancel,
    KS_Entrust_Wait_Cancel,
    KS_BUY,
    KS_SELL,
    KS_P_OPEN,
    KS_P_OFFSET,
)
from vnpy.trader.constant import (
    Direction,
    Offset,
    Exchange,
    Product,
    Status,
)
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    PositionData,
    AccountData,
    ContractData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
)
from vnpy.trader.utility import get_folder_path
from vnpy.trader.event import EVENT_TIMER


# 委托状态映射
STATUS_KSGOLD2VT: Dict[str, Status] = {
    KS_Entrust_Sending: Status.SUBMITTING,
    KS_Entrust_Waiting: Status.NOTTRADED,
    KS_Entrust_Error: Status.REJECTED,
    KS_Entrust_In: Status.NOTTRADED,
    KS_Entrust_Part_Done: Status.PARTTRADED,
    KS_Entrust_All_Done: Status.ALLTRADED,
    KS_Entrust_All_Cancel: Status.CANCELLED,
    KS_Entrust_Part_Done_Cancel: Status.CANCELLED,
    KS_Entrust_Wait_Cancel: Status.SUBMITTING,
}

# 多空方向映射
DIRECTION_VT2KSGOLD: Dict[Direction, str] = {
    Direction.LONG: KS_BUY,
    Direction.SHORT: KS_SELL
}
DIRECTION_KSGOLD2VT: Dict[str, Direction] = {v: k for k, v in DIRECTION_VT2KSGOLD.items()}

# 开平方向映射
OFFSET_VT2KSGOLD: Dict[Offset, str] = {
    Offset.OPEN: KS_P_OPEN,
    Offset.CLOSE: KS_P_OFFSET,
}
OFFSET_KSGOLD2VT: Dict[str, Offset] = {v: k for k, v in OFFSET_VT2KSGOLD.items()}
OFFSET_KSGOLD2VT[48] = Offset.OPEN

# 其他常量
MAX_FLOAT = sys.float_info.max                  # 浮点数极限值
CHINA_TZ = pytz.timezone("Asia/Shanghai")       # 中国时区

# 全局缓存字典
symbol_exchange_map = {}
symbol_name_map = {}
symbol_size_map = {}
symbol_market_map = {}
localid_orderid_map = {}
orderid_localid_map = {}


class KsgoldGateway(BaseGateway):
    """
    vn.py用于对接金仕达黄金TD交易的接口。
    """

    default_setting: Dict[str, Any] = {
        "用户名": "",
        "密码": "",
        "交易服务器": "",
        "行情服务器": "",
        "账号类型": ["银行账号", "黄金账号"]
    }

    exchanges: Exchange = [Exchange.SGE]

    def __init__(self, event_engine: EventEngine, gateway_name: str = "KSGOLD") -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.td_api: "KsgoldTdApi" = KsgoldTdApi(self)
        self.md_api: "KsgoldMdApi" = KsgoldMdApi(self)

    def connect(self, setting: dict) -> None:
        """连接交易接口"""
        userid: str = setting["用户名"]
        password: str = setting["密码"]
        accout_type: str = setting["账号类型"]
        td_address: str = setting["交易服务器"]
        md_address: str = setting["行情服务器"]

        if accout_type == "银行账号":
            login_type = 1
        else:
            login_type = 2

        if (
            (not td_address.startswith("tcp://"))
            and (not td_address.startswith("ssl://"))
        ):
            td_address = "tcp://" + td_address

        if (
            (not md_address.startswith("tcp://"))
            and (not md_address.startswith("ssl://"))
        ):
            md_address = "tcp://" + md_address

        self.td_api.connect(td_address, userid, password, login_type)
        self.md_api.connect(md_address, userid, password, login_type)

        self.init_query()

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        self.md_api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        return self.td_api.send_order(req)

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        self.td_api.cancel_order(req)

    def query_account(self) -> None:
        """查询资金"""
        self.td_api.query_account()

    def query_position(self) -> None:
        """查询持仓"""
        self.td_api.query_position()

    def close(self) -> None:
        """关闭接口"""
        self.td_api.close()
        self.md_api.close()

    def write_error(self, msg: str, error: dict) -> None:
        """输出错误信息日志"""
        error_id: int = error["ErrorID"]
        error_msg: str = error["ErrorMsg"]
        msg: str = f"{msg}，代码：{error_id}，信息：{error_msg}"
        self.write_log(msg)

    def process_timer_event(self, event) -> None:
        """定时事件处理"""
        self.count += 1
        if self.count < 2:
            return
        self.count = 0

        func = self.query_functions.pop(0)
        func()
        self.query_functions.append(func)

    def init_query(self) -> None:
        """初始化查询任务"""
        self.count: int = 0
        self.query_functions: list = [self.query_account, self.query_position]
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)


class KsgoldMdApi(MdApi):
    """"""

    def __init__(self, gateway: KsgoldGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: KsgoldGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.reqid: int = 0

        self.connect_status: bool = False
        self.login_status: bool = False
        self.subscribed: Set = set()

        self.userid: str = ""
        self.password: str = ""
        self.login_type: int = 0

    def onFrontConnected(self, result: int) -> None:
        """服务器连接成功回报"""
        self.gateway.write_log("行情服务器连接成功")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """服务器连接断开回报"""
        self.login_status = False
        self.gateway.write_log(f"行情服务器连接断开，原因{reason}")

    def onRspUserLogin(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """用户登录请求回报"""
        if not error["ErrorID"]:
            self.login_status = True
            self.gateway.write_log("行情服务器登录成功")

            for symbol in self.subscribed:
                self.subscribeMarketData(symbol)
        else:
            self.gateway.write_error("行情服务器登录失败", error)

    def onRspError(self, error: dict, reqid: int, last: bool) -> None:
        """请求报错回报"""
        self.gateway.write_error("行情接口报错", error)

    def onRspSubMarketData(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """订阅行情回报"""
        if not error or not error["ErrorID"]:
            return

        self.gateway.write_error("行情订阅失败", error)

    def onRtnDepthMarketData(self, data: dict) -> None:
        """行情数据推送"""
        symbol: str = data["InstID"]
        exchange: Exchange = symbol_exchange_map.get(symbol, "")
        if not exchange:
            return

        timestamp: str = f"{data['QuoteDate']} {data['QuoteTime']}.{int(data['UpdateMillisec']/100)}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S.%f")
        dt: datetime = CHINA_TZ.localize(dt)

        tick: TickData = TickData(
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            name=symbol_name_map[symbol],
            volume=data["Volume"],
            open_interest=data["OpenInt"],
            last_price=data["Last"],
            limit_up=data["highLimit"],
            limit_down=data["lowLimit"],
            open_price=adjust_price(data["Open"]),
            high_price=adjust_price(data["Highest"]),
            low_price=adjust_price(data["Low"]),
            pre_close=adjust_price(data["PreClose"]),
            bid_price_1=adjust_price(data["Bid1"]),
            ask_price_1=adjust_price(data["Ask1"]),
            bid_volume_1=data["BidLot1"],
            ask_volume_1=data["AskLot1"],
            gateway_name=self.gateway_name
        )

        if data["BidLot2"] or data["AskLot2"]:
            tick.bid_price_2 = adjust_price(data["Bid2"])
            tick.bid_price_3 = adjust_price(data["Bid3"])
            tick.bid_price_4 = adjust_price(data["Bid4"])
            tick.bid_price_5 = adjust_price(data["Bid5"])

            tick.ask_price_2 = adjust_price(data["Ask2"])
            tick.ask_price_3 = adjust_price(data["Ask3"])
            tick.ask_price_4 = adjust_price(data["Ask4"])
            tick.ask_price_5 = adjust_price(data["Ask5"])

            tick.bid_volume_2 = adjust_price(data["BidLot2"])
            tick.bid_volume_3 = adjust_price(data["BidLot3"])
            tick.bid_volume_4 = adjust_price(data["BidLot4"])
            tick.bid_volume_5 = adjust_price(data["BidLot5"])

            tick.ask_volume_2 = adjust_price(data["AskLot2"])
            tick.ask_volume_3 = adjust_price(data["AskLot3"])
            tick.ask_volume_4 = adjust_price(data["AskLot4"])
            tick.ask_volume_5 = adjust_price(data["AskLot5"])

        self.gateway.on_tick(tick)

    def connect(
        self,
        address: str,
        userid: str,
        password: str,
        login_type: int
    ) -> None:
        """连接服务器"""
        self.userid = userid
        self.password = password
        self.login_type = login_type

        # 禁止重复发起连接，会导致异常崩溃
        if not self.connect_status:
            path: Path = get_folder_path(self.gateway_name.lower())
            self.createGoldQutoApi(str(path) + "\\Md")

            self.registerFront(address)
            self.init()

            self.connect_status = True

        elif not self.login_status:
            self.login()

    def login(self) -> None:
        """用户登录"""
        req: dict = {
            "AccountID": self.userid,
            "Password": self.password,
            "LoginType": self.login_type
        }

        self.reqid += 1
        self.reqUserLogin(req, self.reqid)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if self.login_status:
            self.reqid += 1
            self.subscribeMarketData(req.symbol, self.reqid)

        self.subscribed.add(req.symbol)

    def close(self) -> None:
        """关闭连接"""
        if self.connect_status:
            self.exit()


class KsgoldTdApi(TdApi):
    """"""

    def __init__(self, gateway: KsgoldGateway) -> None:
        """构造函数"""
        super().__init__()

        self.gateway: KsgoldGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.reqid: int = 0
        self.order_ref: int = 0

        self.connect_status: bool = False
        self.login_status: bool = False
        self.login_failed: bool = False

        self.userid: str = ""
        self.password: str = ""
        self.trade_code: str = ""
        self.login_type: int = 0
        self.seat_no: int = 0

        self.frontid: int = 0
        self.sessionid: int = 0

        self.order_data: List[dict] = []
        self.trade_data: List[dict] = []
        self.positions: Dict[str, PositionData] = {}
        self.sysid_orderid_map: Dict[str, str] = {}

    def onFrontConnected(self, result: int) -> None:
        """服务器连接成功回报"""
        self.gateway.write_log("交易服务器连接成功")
        self.login()

    def onFrontDisconnected(self, reason: int) -> None:
        """服务器连接断开回报"""
        self.login_status = False
        self.gateway.write_log(f"交易服务器连接断开，原因{reason}")

    def onRspUserLogin(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """用户登录请求回报"""
        if not error["ErrorID"]:
            self.frontid = data["FrontID"]
            self.sessionid = data["SessionID"]
            self.seat_no = data["SeatNo"]
            self.trade_code = data["TradeCode"]
            self.login_status = True
            self.gateway.write_log("交易服务器登录成功")

            while True:
                self.reqid += 1
                n: int = self.reqQryInstrument({}, self.reqid)

                if not n:
                    break
                else:
                    sleep(1)
        else:
            self.login_failed = True

            self.gateway.write_error("交易服务器登录失败", error)

    def onRspOrderInsert(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """委托下单失败回报"""
        print("on order: data, ", data, "\n\nerror:", error)
        print("offset=", data["OffsetFlag"])

        order_ref: str = data["OrderRef"]
        orderid: str = f"{self.frontid}_{self.sessionid}_{order_ref}"

        symbol: str = data["InstID"]
        exchange: Exchange = symbol_exchange_map[symbol]

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=exchange,
            orderid=orderid,
            direction=DIRECTION_KSGOLD2VT[data["BuyOrSell"]],
            offset=OFFSET_KSGOLD2VT.get(data["OffsetFlag"], Offset.NONE),
            price=data["Price"],
            volume=data["Amount"],
            status=Status.REJECTED,
            datetime=datetime.now(),
            gateway_name=self.gateway_name
        )
        self.gateway.on_order(order)

        self.gateway.write_error("交易委托失败", error)

    def onRspOrderAction(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """委托撤单失败回报"""
        self.gateway.write_error("交易撤单失败", error)

    def onRspQueryMaxOrderVolume(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """"""
        pass

    def onRspQryInvestorPosition(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """持仓查询回报"""
        error_id: int = error["ErrorID"]
        error_msg: str = error["ErrorMsg"]

        if error_id != 0:
            if error_id == 10001:
                return
            else:
                self.gateway.write_log(f"查询持仓失败，信息{error_msg}")
                return

        # 多仓
        long_position: PositionData = PositionData(
            symbol=data["InstID"],
            exchange=Exchange.SGE,
            direction=Direction.LONG,
            price=data["LongOpenAvgPrice"],
            yd_volume=data["LastLong"],
            volume=data["LongPosiVol"],
            frozen=data["LongPosiFrozen"],
            pnl=0,
            gateway_name=self.gateway_name,
        )
        self.gateway.on_position(long_position)

        # 空仓
        short_position: PositionData = PositionData(
            symbol=data["InstID"],
            exchange=Exchange.SGE,
            direction=Direction.SHORT,
            price=data["ShortOpenAvgPrice"],
            volume=data["ShortPosiVol"],
            yd_volume=data["LastShort"],
            frozen=data["ShortPosiFrozen"],
            pnl=0,
            gateway_name=self.gateway_name,
        )
        self.gateway.on_position(short_position)

    def onRspQryTradingAccount(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """资金查询回报"""
        if "ClientID" not in data:
            return

        account: AccountData = AccountData(
            accountid=data["ClientID"],
            balance=data["TotalFrozen"] + data["AvailCap"],
            frozen=data["TotalFrozen"],
            gateway_name=self.gateway_name
        )

        self.gateway.on_account(account)

    def onRspQryInstrument(
        self,
        data: dict,
        error: dict,
        reqid: int,
        last: bool
    ) -> None:
        """合约查询回报"""
        contract: ContractData = ContractData(
            symbol=data["InstID"],
            exchange=Exchange.SGE,
            name=data["Name"],
            product=Product.SPOT,
            size=data["Unit"],
            pricetick=data["Tick"],
            gateway_name=self.gateway_name
        )

        self.gateway.on_contract(contract)

        symbol_exchange_map[contract.symbol] = contract.exchange
        symbol_name_map[contract.symbol] = contract.name
        symbol_size_map[contract.symbol] = contract.size
        symbol_market_map[contract.symbol] = data["MarketID"]

        if last:
            self.gateway.write_log("合约信息查询成功")

            for data in self.order_data:
                self.onRtnOrder(data)
            self.order_data.clear()

            for data in self.trade_data:
                self.onRtnTrade(data)
            self.trade_data.clear()

    def onRtnOrder(self, data: dict) -> None:
        """委托更新推送"""
        symbol: str = data["InstID"]
        exchange: Exchange = symbol_exchange_map.get(symbol, "")
        if not exchange:
            self.order_data.append(data)
            return

        frontid: int = data["FrontID"]
        sessionid: int = data["SessionID"]
        order_ref: str = data["OrderRef"]
        localid: int = data["LocalOrderNo"]
        orderid: str = f"{frontid}_{sessionid}_{order_ref}"

        orderid_localid_map[orderid] = localid
        localid_orderid_map[localid] = orderid

        today: str = datetime.now().strftime("%Y%m%d")
        timestamp: str = f"{today} {data['EntrustTime']}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S")
        dt: datetime = CHINA_TZ.localize(dt)

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=exchange,
            orderid=orderid,
            direction=DIRECTION_KSGOLD2VT[data["BuyOrSell"]],
            offset=OFFSET_KSGOLD2VT[data["OffsetFlag"]],
            price=data["Price"],
            volume=data["Amount"],
            traded=data["MatchQty"],
            status=STATUS_KSGOLD2VT[data["Status"]],
            datetime=dt,
            gateway_name=self.gateway_name
        )
        self.gateway.on_order(order)

        self.sysid_orderid_map[data["OrderNo"]] = orderid

    def onRtnTrade(self, data: dict) -> None:
        """成交数据推送"""
        symbol: str = data["InstID"]
        exchange: Exchange = symbol_exchange_map.get(symbol, "")
        if not exchange:
            self.trade_data.append(data)
            return

        orderid: str = self.sysid_orderid_map[data["OrderNo"]]

        today: str = datetime.now().strftime("%Y%m%d")
        timestamp: str = f"{today} {data['MatchTime']}"
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d %H:%M:%S")
        dt: datetime = CHINA_TZ.localize(dt)

        trade: TradeData = TradeData(
            symbol=symbol,
            exchange=exchange,
            orderid=orderid,
            tradeid=data["MatchNo"],
            direction=DIRECTION_KSGOLD2VT[data["BuyOrSell"]],
            offset=OFFSET_KSGOLD2VT[data["OffSetFlag"]],
            price=data["Price"],
            volume=data["Volume"],
            datetime=dt,
            gateway_name=self.gateway_name
        )
        self.gateway.on_trade(trade)

    def connect(
        self,
        address: str,
        userid: str,
        password: str,
        login_type: int,
    ) -> None:
        """连接服务器"""
        self.userid = userid
        self.password = password
        self.login_type = login_type

        if not self.connect_status:
            path = get_folder_path(self.gateway_name.lower())
            self.createGoldTraderApi(str(path) + "\\Td")

            self.subscribePrivateTopic(0)
            self.subscribePublicTopic(0)

            self.registerFront(address)
            self.init()

            self.connect_status = True

    def login(self) -> None:
        """用户登录"""
        if self.login_failed:
            return

        req: dict = {
            "AccountID": self.userid,
            "Password": self.password,
            "LoginType": self.login_type,
        }

        self.reqid += 1
        self.reqUserLogin(req, self.reqid)

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        if req.offset not in OFFSET_VT2KSGOLD:
            self.gateway.write_log("请选择开平方向")
            return ""

        self.order_ref += 1

        ksgold_req: dict = {
            "SeatID": self.seat_no,
            "ClientID": self.userid,
            "TradeCode": self.trade_code,
            "InstID": req.symbol,
            "BuyOrSell": DIRECTION_VT2KSGOLD.get(req.direction, ""),
            "OffsetFlag": OFFSET_VT2KSGOLD.get(req.offset, ""),
            "Amount": int(req.volume),
            "Price": req.price,
            "MarketID": symbol_market_map[req.symbol],
            "OrderRef": str(self.order_ref),
            "SessionID": self.sessionid,
        }

        self.reqid += 1
        self.reqOrderInsert(ksgold_req, self.reqid)

        orderid: str = f"{self.frontid}_{self.sessionid}_{self.order_ref}"
        order: OrderData = req.create_order_data(orderid, self.gateway_name)
        self.gateway.on_order(order)

        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        localid: str = orderid_localid_map[req.orderid]
        ksgold_req: dict = {"LocalOrderNo": localid}

        self.reqid += 1
        self.reqOrderAction(ksgold_req, self.reqid)

    def query_account(self) -> None:
        """查询资金"""
        self.reqid += 1
        self.reqQryTradingAccount({}, self.reqid)

    def query_position(self) -> None:
        """查询持仓"""
        if not symbol_exchange_map:
            return

        self.reqid += 1
        self.reqQryInvestorPosition({}, self.reqid)

    def close(self) -> None:
        """关闭连接"""
        if self.connect_status:
            self.exit()


def adjust_price(price: float) -> float:
    """将异常的浮点数最大值（MAX_FLOAT）数据调整为0"""
    if price == MAX_FLOAT:
        price = 0
    return price
