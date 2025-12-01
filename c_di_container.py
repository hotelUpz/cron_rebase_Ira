# c_di_container.py
from typing import *
from b_context import BotContext, BaseDataInitializer, PositionVarsSetup
from c_log import ErrorHandler
from c_utils import PositionUtils
from c_validators import TimeframeValidator, OrderValidator
from d_bapi import BinancePublicApi
from MANAGERS.online import WS_HotPrice_Stream
from MANAGERS.offline import WriteLogManager
from BUSINESS.signals import SIGNALS
from BUSINESS.risk_orders_control import RiskOrdersControl


class DIContainer:
    def __init__(self):
        self._factories = {}
        self._instances = {}

    def register(self, key: str, factory: callable, singleton: bool = False):
        self._factories[key] = {
            "factory": factory,
            "singleton": singleton,
        }

    def get(self, key: str):
        if key in self._instances:
            return self._instances[key]

        if key not in self._factories:
            raise KeyError(f"Dependency '{key}' is not registered.")

        factory_info = self._factories[key]
        instance = factory_info["factory"]()

        if factory_info["singleton"]:
            self._instances[key] = instance

        return instance    

def setup_dependencies_first(container: DIContainer, config: dict):
    info_handler: ErrorHandler = config.get("info_handler")
    context: BotContext = config.get("context")    
    container.register("pos_utils", lambda: PositionUtils(context, info_handler), singleton=True)
    pos_utils = container.get("pos_utils")
    container.register("base_initializer", lambda: BaseDataInitializer(
        context, 
        info_handler,
        pos_utils
        ),
        singleton=True
    )
    container.register("position_vars_setup", lambda: PositionVarsSetup(
        context, 
        info_handler,
        pos_utils
        ),
        singleton=True
    )

def setup_dependencies_second(container, config: dict):    
    info_handler: ErrorHandler = config.get("info_handler")
    context: BotContext = config.get("context")
    common_proxy_list: Optional[str] = config.get("common_proxy_list")
    container.register("write_log_manager", lambda: WriteLogManager(
        info_handler,
        config.get("max_log_lines")
        ), singleton=True
    )
    container.register("websocket_manager", lambda: WS_HotPrice_Stream(
        context=context,
        info_handler=info_handler,
        proxy_list=common_proxy_list
    ), singleton=True)
    container.register("time_frame_validator", lambda: TimeframeValidator(info_handler), singleton=True)
    container.register("order_validator", lambda: OrderValidator(info_handler), singleton=True)    
    container.register("binance_public", lambda: BinancePublicApi(info_handler), singleton=True)

def setup_dependencies_third(container, config: dict):
    info_handler: ErrorHandler = config.get("info_handler")
    context: BotContext = config.get("context")
    time_frame_validator: TimeframeValidator = config.get("time_frame_validator")
    pos_utils: PositionUtils = config.get("pos_utils")
    container.register("signals", lambda: SIGNALS(
        context,
        info_handler,
        time_frame_validator
        ),
        singleton=True
    )

    container.register("risk_order_control", lambda: RiskOrdersControl(
        context,
        info_handler,
        pos_utils,
        ),
        singleton=True
    )