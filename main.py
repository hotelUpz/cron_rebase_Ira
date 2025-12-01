import asyncio
import aiohttp
from datetime import datetime
import time
# from pprint import pprint
from typing import *
from a_settings import *
from b_context import BotContext, BaseDataInitializer, PositionVarsSetup
from c_di_container import DIContainer, setup_dependencies_first, setup_dependencies_second, setup_dependencies_third
from c_log import ErrorHandler, log_time
from c_utils import PositionUtils
from c_validators import TimeframeValidator, OrderValidator
from d_bapi import BinancePublicApi
from MANAGERS.online import WS_HotPrice_Stream, NetworkManager
from MANAGERS.offline import WriteLogManager
from BUSINESS.position_control import Sync
from BUSINESS.order_patterns import RiskSet, HandleOrders
from BUSINESS.risk_orders_control import RiskOrdersControl
from BUSINESS.signals import SIGNALS
from d_bapi import BinancePrivateApi
from TG.tg_notifier import TelegramNotifier
# from pprint import pprint
import traceback


RUTINE_CYCLE_FREQUENCY: float = 1.0          # seconds. частота работы главного цикла

# import json

# def save_to_json(data: Optional[dict], filename="cache_data.json"):
#     """
#     Сохраняет словарь/список в JSON-файл с отступами.

#     :param data: dict или list – данные для сохранения
#     :param filename: str – путь до файла (например, '/home/user/data.json')
#     """
#     try:
#         # Убедимся, что директория существует
#         # os.makedirs(os.path.dirname(filename), exist_ok=False)

#         with open(filename, 'w', encoding='utf-8') as f:
#             json.dump(data, f, ensure_ascii=False, indent=4)
#         print(f"Файл сохранён: {filename}")
#     except Exception as e:
#         print(f"Ошибка при сохранении: {e}")


def generate_bible_quote():
    random_bible_list = [
        "<<Благодать Господа нашего Иисуса Христа, и любовь Бога Отца, и общение Святаго Духа со всеми вами. Аминь.>>\n___(2-е Коринфянам 13:13)___",
        "<<Притом знаем, что любящим Бога, призванным по Его изволению, все содействует ко благу.>>\n___(Римлянам 8:28)___",
        "<<Спокойно ложусь я и сплю, ибо Ты, Господи, един даешь мне жить в безопасности.>>\n___(Пс 4:9)___"
    ]

    current_hour = datetime.now().hour
    if 6 <= current_hour < 12:
        return random_bible_list[0]
    elif 12 <= current_hour < 23:
        return random_bible_list[1]
    return random_bible_list[2]


async def get_cur_price(
        session,
        ws_price_data: dict,
        symbol: str,
        get_hot_price: callable,
    ):
    """
    Получение текущей цены символа.
    """
    cur_price = ws_price_data.get(symbol, {}).get("close")
    if not cur_price:
        return await get_hot_price(session, symbol)
    return cur_price


class Core:
    def __init__(self):
        self.context = BotContext()
        self.info_handler = ErrorHandler()
        self.container = DIContainer()       
        self.loaded_cache: dict = {}
        self.public_session: Optional[aiohttp.ClientSession] = None

    def _get_first_proxy(self) -> Optional[List]:
        """Берём proxy_url у первого пользователя, где он не None."""
        total_proxy_list = []
        # pprint(self.context.total_settings)
        for user, details in self.context.total_settings.items():
            proxy_list = details.get("proxies")
            if proxy_list:
                total_proxy_list += proxy_list

        return list(filter(None, total_proxy_list)) + [None] if total_proxy_list else None

    async def _start_context(self):
        setup_dependencies_first(self.container, {
            "info_handler": self.info_handler,
            "context": self.context,
        })
        base_initializer: BaseDataInitializer = self.container.get("base_initializer")
        base_initializer.init_base_structure()
        self.pos_utils: PositionUtils = self.container.get("pos_utils")
        # //
        self.all_users = list(self.context.total_settings.keys())
        self.common_proxy_list = self._get_first_proxy() or [None]

        setup_dependencies_second(self.container, {
            "info_handler": self.info_handler,
            "context": self.context,
            "max_log_lines": MAX_LOG_LINES,
            "common_proxy_list": self.common_proxy_list
        })
        self.write_log: WriteLogManager = self.container.get("write_log_manager")
        # print(self.common_proxy_list)

        self.public_connector = NetworkManager(
            info_handler=self.info_handler,
            proxy_list=self.common_proxy_list,
            user_label="PUBLIC_SESSION",
            stop_bot=self.context.stop_bot
        )

        # print(self.public_connector)

        await self.public_connector.start_ping_loop()

        # ждём, пока NetworkManager в фоне создаст сессию
        while self.public_connector.session is None:
            await asyncio.sleep(0.1)

        self.public_session = self.public_connector.session
        # print("PUBLIC_SESSION READY:", self.public_session)

        self.binance_public: BinancePublicApi = self.container.get("binance_public")
        for _ in range(10):  # попытки получить инструменты
            try:
                symbol_info = await self.binance_public.get_exchange_info(self.public_session)
                if symbol_info:
                    self.context.symbol_info = symbol_info
                    break
            except Exception as e:
                self.info_handler.debug_error_notes(f"[ERROR] Failed to fetch instruments: {e}", is_print=True)
            await asyncio.sleep(1)

        # print(self.context.symbol_info)
        position_vars_setup: PositionVarsSetup = self.container.get("position_vars_setup")
        position_vars_setup.setup_pos_vars()
        # //
        
        await asyncio.gather(*[self._init_all_users_sessions(user_name) for user_name in self.all_users])
        # //
        self.websocket_manager: WS_HotPrice_Stream = self.container.get("websocket_manager")
        self.time_frame_validator: TimeframeValidator = self.container.get("time_frame_validator")

        setup_dependencies_third(self.container, {
            "info_handler": self.info_handler,
            "context": self.context,
            "time_frame_validator": self.time_frame_validator,
            "pos_utils": self.pos_utils
        })
        self.signals: SIGNALS = self.container.get("signals")        
        self.order_validator: OrderValidator = self.container.get("order_validator")
        self.risk_order_control: RiskOrdersControl = self.container.get("risk_order_control")
        # # ///

        self.risk_order_patterns = RiskSet(
            context=self.context,
            info_handler=self.info_handler,
            validate=self.order_validator
        )

        self.handle_odrers = HandleOrders(
            context=self.context,
            info_handler=self.info_handler,
            pos_utils=self.pos_utils,
            risk_set=self.risk_order_patterns,
            get_hot_price=self.binance_public.get_hot_price,
            get_cur_price=get_cur_price
        )

        self.notifier = TelegramNotifier(             
            token=TG_BOT_TOKEN,
            chat_ids=[TG_BOT_ID,],
            context=self.context,
            info_handler=self.info_handler 
        )

        self.sync = Sync(
            context=self.context,
            info_handler=self.info_handler,
            set_pos_defaults=position_vars_setup.set_pos_defaults,
            preform_message=self.notifier.preform_message
        )

        self.info_handler.wrap_foreign_methods(self)

    async def _init_all_users_sessions(self, user_name: str) -> None:
        user_details: dict = self.context.total_settings[user_name]
        proxy_list = user_details.get("proxies", None)

        connector = NetworkManager(info_handler=self.info_handler, proxy_list=proxy_list)
        await connector.start_ping_loop()

        keys = user_details["keys"]

        binance_client = BinancePrivateApi(
            info_handler=self.info_handler,
            api_key=keys["BINANCE_API_PUBLIC_KEY"],
            api_secret=keys["BINANCE_API_PRIVATE_KEY"],
            user_label=user_name,
        )

        self.context.user_contexts[user_name] = {
            "connector": connector,
            "binance_client": binance_client,
        }

    async def _quit_all_users_sessions(self, user_name: str) -> None:
        connector: NetworkManager = self.context.user_contexts[user_name]["connector"]
        await connector.shutdown_session()

    # ------------------------------------------------------------------
    async def set_hedge_mode_for_all_users(self, all_users: List[str], enable_hedge: bool = True):
        """
        Оставляю этот метод, раз ты им пользуешься / любишь иметь под рукой.
        Даже если ты уже делаешь set_hedge_mode в Core, дублирование не мешает.
        """
        tasks = []
        for user_name in all_users:
            try:
                user_context = self.context.user_contexts[user_name]
                session = user_context["connector"].session
                binance_client: BinancePrivateApi = user_context["binance_client"]

                task = binance_client.set_hedge_mode(
                    session=session,
                    true_hedg=enable_hedge
                )
                tasks.append(task)
            except Exception as e:
                self.info_handler.debug_error_notes(
                    f"[HEDGE_MODE ERROR][{user_name}] → {e}", is_print=True
                )

        if tasks:
            await asyncio.gather(*tasks)

    async def _run(self):
        print(f"\n{generate_bible_quote()}")
        print(f"Время старта: {log_time()}")

        await self._start_context()

        if not self.context.fetch_symbols:
            print("Нет ни одного символа для торговли.")
            self.context.stop_bot = True
            return

        # # # set hedg mode for all users:
        # await self.set_hedge_mode_for_all_users(
        #     all_users=self.all_users,
        #     enable_hedge=True
        # )

        # print(self.context.fetch_symbols)
        
        # # // web socket start
        await self.websocket_manager.sync_ws_streams(list(self.context.fetch_symbols))
        # await asyncio.sleep(10)
        while not self.context.stop_bot:
            # ждём пока у каждого символа будет "close" != None
            if all(
                self.context.ws_price_data.get(s, {}).get("close") is not None
                for s in self.context.fetch_symbols
            ):
                break
            await asyncio.sleep(0.1)

        # save_to_json(self.context.ws_price_data, "ws_price_data.json")
        print(self.all_users)

        asyncio.create_task(self.sync.run_positions_sync_loop())
        while not self.context.stop_bot and not all(self.context.first_update_done.get(user_name, False) for user_name in self.all_users):
            await asyncio.sleep(0.1)

        print("Начало основного цикла...")
        # print(self.context.position_vars)
        # save_to_json(self.context.position_vars)        
        # pprint(self.context.total_settings)
        # return

        # ---- Обновляем инструменты каждые 300 секунд ---
        instrume_update_interval = 1800.0
        # --- пишем логи каждые 5 секунд ---
        write_logs_interval = 5.0

        last_instrume_time = time.monotonic()
        last_write_logs_time = time.monotonic()      

        def is_symbol_limit(core_settings, active_symbols, symbol):
            # лимиты (как было)
            long_limit = core_settings.get("long_positions_limit", float("inf"))
            short_limit = core_settings.get("short_positions_limit", float("inf"))
            if active_symbols and len(active_symbols) >= max(long_limit, short_limit):
                if symbol not in active_symbols:
                    return True
            return False

        # RUTINE CUCLE
        while not self.context.stop_bot:
            try:
                risk_tasks = []
                signal_tasks = []

                long_count, short_count, active_symbols = self.pos_utils.count_active_symbols(
                    self.context.position_vars
                )

                for user_name in self.all_users:
                    core_settings = self.context.total_settings[user_name]["core"]
                    connector: NetworkManager = self.context.user_contexts[user_name]["connector"]
                    binance_client = self.context.user_contexts[user_name]["binance_client"]
                    strategies = self.context.position_vars[user_name]

                    for strategy_name, strategy_data in strategies.items():
                        for symbol, symbol_pos_data in strategy_data.items():

                            for position_side in ("LONG", "SHORT"):
                                risk_action = self.risk_order_control.risk_symbol_monitoring(
                                    user_name=user_name,
                                    strategy_name=strategy_name,
                                    symbol=symbol,
                                    position_side=position_side,
                                    compose_signals=self.signals.compose_signals,
                                    client_session=connector.session,
                                    binance_client=binance_client
                                )

                                if risk_action:
                                    risk_tasks.append(risk_action)

                                if not is_symbol_limit(core_settings, active_symbols, symbol):

                                    # ---- GET OPEN SIGNAL ----
                                    if self.signals.get_signal(
                                        user_name,
                                        strategy_name,
                                        symbol,
                                        position_side,
                                        long_count,
                                        short_count
                                    ):

                                        # ------ логирование открытия -------
                                        debug_label = f"{user_name}_{symbol}_{position_side}"
                                        self.info_handler.trades_info_notes(
                                            f"[{debug_label}]. 🚀 Открываем позицию по сигналу! ",
                                            True
                                        )

                                        # устанавливаем process_volume
                                        strategy_settings = self.context.strategy_notes[strategy_name][position_side]
                                        volume_rate = strategy_settings["entry_conditions"]["grid_orders"][0]["volume"]
                                        symbol_pos_data[position_side]["process_volume"] = volume_rate

                                        signal_tasks.append(self.signals.compose_signals(
                                            user_name=user_name,
                                            strategy_name=strategy_name,
                                            symbol=symbol,
                                            position_side=position_side,
                                            status="is_opening",
                                            client_session=connector.session,
                                            binance_client=binance_client
                                        ))

                # Всегда первым — риск!
                if risk_tasks:
                    await self.handle_odrers.compose_trade_instruction(task_list=risk_tasks)
                await asyncio.sleep(0)

                # Открытия после рисков
                if signal_tasks:
                    await self.handle_odrers.compose_trade_instruction(task_list=signal_tasks)

            finally:
                try:
                    if self.context.report_list:
                        asyncio.create_task(self.notifier.send_report_batches(batch_size=1))
                except Exception as e:
                    err_msg = f"[ERROR] main finally block: {e}\n" + traceback.format_exc()
                    self.info_handler.debug_error_notes(err_msg, is_print=True)      

                now = time.monotonic()
                if now - last_instrume_time >= instrume_update_interval:
                    try:
                        symbol_info = await self.binance_public.get_exchange_info(self.public_session)
                        if not symbol_info:
                            self.info_handler.debug_error_notes(f"[ERROR] Failed to fetch instruments: {e}", is_print=True)
                        else:
                            self.context.symbol_info = symbol_info
                    except Exception as e:
                        self.info_handler.debug_error_notes(f"[ERROR] Failed to fetch instruments: {e}", is_print=True)
                    last_instrume_time = now

                now = time.monotonic() 
                if WRITE_TO_LOG and now - last_write_logs_time >= write_logs_interval:
                    try:
                        await self.write_log.write_logs()
                    except Exception as e:
                        self.info_handler.debug_error_notes(f"Ошибка при записи логов: {e}")
                    last_write_logs_time = now

                self.context.first_iter = False
                await asyncio.sleep(RUTINE_CYCLE_FREQUENCY)
                # print("Tik")


async def main():
    instance = Core()
    try:
        await instance._run()
    except asyncio.CancelledError:
        print("🚩 Асинхронная задача была отменена.")
    except KeyboardInterrupt:
        print("\n⛔ Остановка по Ctrl+C")
    # except Exception as e:
    #     print(f"\n❌ Ошибка: {type(e).__name__} — {e}")
    finally:
        if WRITE_TO_LOG and hasattr(instance, "write_log"):
            try:
                await instance.write_log.write_logs()
            except Exception as e:
                print("[SYNC][ERROR] write_cache:", e)

        instance.context.stop_bot = True
        await asyncio.gather(*[instance._quit_all_users_sessions(user_name) for user_name in instance.all_users])
        await instance.public_connector.shutdown_session()  # ← добавь это
        print("Сессии закрываются...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


# # убедились, что права уже установлены (вы это сделали)
# chmod 600 ssh_key

# # запустить агент (если он не запущен) и добавить ключ из текущей директории
# eval "$(ssh-agent -s)" && ssh-add ./ssh_key

# ssh-add -l        # выведет список добавленных ключей или "The agent has no identities"

# ssh -T git@github.com