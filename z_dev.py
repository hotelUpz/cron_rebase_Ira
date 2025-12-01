# import asyncio
# import aiohttp
# from typing import Callable, List
# from b_context import BotContext
# from c_log import ErrorHandler
# from c_utils import PositionUtils
# from c_validators import OrderValidator 
# from d_bapi import BinancePrivateApi

# class RiskSet:
#     def __init__(
#         self,
#         context: BotContext,
#         error_handler: ErrorHandler,
#         validate: OrderValidator
#     ):
#         error_handler.wrap_foreign_methods(self)
#         self.error_handler = error_handler
#         self.context = context
#         self.validate = validate

#     async def _cancel_risk_order(
#         self,
#         session,
#         user_name: str,
#         strategy_name: str,
#         symbol: str,
#         position_side: str,
#         cancel_order_by_id: Callable,
#         suffix: str
#     ) -> bool:
#         debug_label = f"[{user_name}][{strategy_name}][{symbol}][{position_side}]"
#         pos_data = self.context.position_vars[user_name][strategy_name][symbol][position_side]
#         order_id = pos_data.get(f"{suffix}_order_id")

#         if not order_id:
#             self.error_handler.trades_info_notes(
#                 f"[INFO]{debug_label}[{suffix.upper()}]: отсутствует ID ордера.", False
#             )
#             return False

#         response = await cancel_order_by_id(
#             session=session,
#             strategy_name=strategy_name,
#             symbol=symbol,
#             order_id=order_id,
#             suffix=suffix
#         )

#         if self.validate.validate_cancel_risk_response(response, suffix, debug_label):
#             pos_data[f"{suffix}_order_id"] = None
#             return True
#         return False

#     async def _place_risk_order(
#         self,
#         session,
#         user_name: str,
#         strategy_name: str,
#         symbol: str,
#         position_side: str,
#         suffix: str,
#         place_risk_order: Callable,
#         offset: float = None,
#         activation_percent: float = None,
#         is_move_tp: bool = False
#     ):
#         # print(f"\n📌 START placing {suffix.upper()} order for [{user_name}][{strategy_name}][{symbol}][{position_side}]")
#         debug_label = f"[{user_name}][{strategy_name}][{symbol}][{position_side}]"

#         user_risk_cfg = self.context.total_settings[user_name]["symbols_risk"]
#         key = symbol if symbol in user_risk_cfg else "ANY_COINS"

#         dinamic_condition_pct = (
#             self.context.dinamik_risk_data
#                 .get(user_name, {})
#                 .get(symbol, {})
#                 .get(suffix)
#         )

#         condition_pct = (
#             dinamic_condition_pct
#             if dinamic_condition_pct is not None
#             else user_risk_cfg.get(key, {}).get(suffix.lower())
#         )

#         # print(f"{debug_label} → condition_pct ({suffix}): {condition_pct}")
#         if not condition_pct:
#             self.error_handler.debug_info_notes(f"{debug_label}: Не задан {suffix.upper()} процент.")
#             return

#         is_long = position_side == "LONG"
#         sign = 1 if is_long else -1

#         pos_data = self.context.position_vars[user_name][strategy_name][symbol][position_side]
#         avg_price = pos_data.get("avg_price")
#         qty = pos_data.get("comul_qty")
#         price_precision = self.context.position_vars[user_name][strategy_name][symbol].get("price_precision", 2)

#         order_type = user_risk_cfg.get(key, {}).get(f"tp_order_type")

#         # print(f"{debug_label} → avg_price: {avg_price}, qty: {qty}, precision: {price_precision}, sign: {sign}")

#         try:
#             if suffix.lower() == "sl" and offset:
#                 target_price = round(avg_price * (1 + sign * offset / 100), price_precision)
#                 # print(f"{debug_label} → SL offset: {offset}, target_price: {target_price}")

#             elif suffix.lower() == "tp" and is_move_tp:
#                 shift_pct = activation_percent + condition_pct
#                 target_price = round(avg_price * (1 + sign * shift_pct / 100), price_precision)
#                 # print(f"{debug_label} → TP shift (activation + condition): {shift_pct}, target_price: {target_price}")

#             else:
#                 # === Вычисления ===
#                 shift_pct = condition_pct if suffix == "tp" else -abs(condition_pct)
#                 target_price = round(avg_price * (1 + sign * shift_pct / 100), price_precision)

#         except Exception as e:
#             print(f"{debug_label} ❌ Error calculating target_price: {e}")
#             return

#         side = "SELL" if is_long else "BUY"
#         # print(f"{debug_label} → placing order: side={side}, qty={qty}, price={target_price}, suffix={suffix}")

#         try:
#             response = await place_risk_order(
#                 session=session,
#                 strategy_name=strategy_name,
#                 symbol=symbol,
#                 qty=qty,
#                 side=side,
#                 position_side=position_side,
#                 target_price=target_price,
#                 suffix=suffix,
#                 order_type=order_type
#             )
#         except Exception as e:
#             print(f"{debug_label} ❌ Error placing order: {e}")
#             return

#         validated = self.validate.validate_risk_response(response, suffix.upper(), debug_label)
#         # print(f"{debug_label} → validation result: {validated}")
#         if validated:
#             success, order_id = validated
#             if success:
#                 pos_data[f"{suffix.lower()}_order_id"] = order_id                
#                 print(f"{debug_label} ✅ Order placed: {suffix.lower()}_order_id = {order_id}")
#                 return True
#         return False

#     async def cancel_all_risk_orders(
#         self,
#         session,
#         user_name: str,
#         strategy_name: str,
#         symbol: str,
#         position_side: str,
#         risk_suffix_list: List, # ['tp', 'sl']
#         cancel_order_by_id: Callable,
#     ):
#         """
#         Отменяет оба ордера (SL и TP) параллельно.
#         """
#         return await asyncio.gather(*[
#             self._cancel_risk_order(
#                 session,
#                 user_name,
#                 strategy_name,
#                 symbol,
#                 position_side,
#                 cancel_order_by_id,
#                 suffix
#             )
#             for suffix in risk_suffix_list
#         ])

#     async def place_all_risk_orders(
#         self,
#         session,
#         user_name: str,
#         strategy_name: str,
#         symbol: str,
#         position_side: str,
#         risk_suffix_list: List, # ['tp', 'sl']
#         place_risk_order: Callable,
#         offset: float = None,
#         activation_percent: float = None,
#         is_move_tp: bool = False,
#     ):
#         """
#         Размещает оба ордера (SL и TP) параллельно.
#         """
#         return await asyncio.gather(*[
#             self._place_risk_order(
#                 session,
#                 user_name,
#                 strategy_name,
#                 symbol,
#                 position_side,
#                 suffix,
#                 place_risk_order,
#                 offset,
#                 activation_percent,
#                 is_move_tp
#             )
#             for suffix in risk_suffix_list
#         ])

#     # ////////
#     async def replace_sl(
#         self,
#         session: aiohttp.ClientSession,
#         user_name: str,
#         strategy_name: str,
#         symbol: str,
#         position_side: str,
#         is_move_tp: bool,
#         offset: float,
#         activation_percent: float,
#         cancel_order_by_id: Callable,
#         place_risk_order: Callable,
#         debug_label: str = ""
#     ) -> None:
#         try:
#             # 🚫 Отменяем TP и SL
#             await self.cancel_all_risk_orders(
#                     session,
#                     user_name,
#                     strategy_name,
#                     symbol,
#                     position_side,
#                     ["tp", "sl"],
#                     cancel_order_by_id
#                 )
#             self.error_handler.debug_info_notes(f"Cancelled SL/TP for {debug_label}")

#             risk_suffics_list = ['sl']
#             if is_move_tp:
#                 risk_suffics_list.append('tp')

#             await self.place_all_risk_orders(
#                 session,
#                 user_name,
#                 strategy_name,
#                 symbol,
#                 position_side,
#                 risk_suffics_list,
#                 place_risk_order,
#                 offset,
#                 activation_percent,
#                 is_move_tp
#             )

#         except aiohttp.ClientError as e:
#             self.error_handler.debug_error_notes(f"[HTTP Error] Failed to replace SL/TP for {debug_label}: {e}")
#             raise
#         except Exception as e:
#             self.error_handler.debug_error_notes(f"[Unexpected Error] Failed to replace SL/TP for {debug_label}: {e}")
#             raise


# class HandleOrders:
#     def __init__(
#         self,
#         context: BotContext,
#         error_handler: ErrorHandler,
#         pos_utils: PositionUtils,
#         risk_set: RiskSet,
#         get_hot_price: Callable,
#         get_cur_price: Callable
#     ):
#         error_handler.wrap_foreign_methods(self)
#         self.context = context
#         self.error_handler = error_handler
#         self.pos_utils = pos_utils
#         self.get_hot_price = get_hot_price
#         self.get_cur_price = get_cur_price
#         # self.sync_pos_all_users = sync_pos_all_users
#         self.risk_set = risk_set
#         self.last_debug_label = {}

#     async def set_hedge_mode_for_all_users(self, all_users: List, enable_hedge: bool = True):
#         tasks = []

#         for user_name in all_users:
#             try:
#                 user_context = self.context.user_contexts[user_name]
#                 session = user_context["connector"].session
#                 binance_client: BinancePrivateApi = user_context["binance_client"]

#                 task = binance_client.set_hedge_mode(
#                     session=session,
#                     true_hedg=enable_hedge
#                 )
#                 tasks.append(task)

#             except Exception as e:
#                 self.error_handler.debug_error_notes(
#                     f"[HEDGE_MODE ERROR][{user_name}] → {e}", is_print=True
#                 )

#         await asyncio.gather(*tasks)

#     async def compose_trade_instruction(self, task_list: list[dict]):
#         async def make_trailing_task(task):
#             strategy_settings = self.context.strategy_notes[task["strategy_name"]][task["position_side"]]
#             is_move_tp = strategy_settings.get("exit_conditions", {}).get("trailing_sl", {}).get("is_move_tp", False)
#             await self.risk_set.replace_sl(
#                 task["client_session"],
#                 task["user_name"],
#                 task["strategy_name"],
#                 task["symbol"],
#                 task["position_side"],
#                 is_move_tp,
#                 task["position_data"].get("offset"),
#                 task["position_data"].get("activation_percent"),
#                 task["binance_client"].cancel_order_by_id,
#                 task["binance_client"].place_risk_order,
#                 task["debug_label"]
#             )

#         async def make_trade_task(task, side, qty):
#             try:
#                 user_name = task["user_name"]
#                 symbol = task["symbol"]
#                 strategy_name = task["strategy_name"]
#                 position_side = task["position_side"]
#                 debug_label = task["debug_label"]
#                 client_session = task["client_session"]
#                 binance_client: BinancePrivateApi = task["binance_client"]
#                 symbols_risk = self.context.total_settings[user_name]["symbols_risk"]
#                 symbol_risk_key = symbol if symbol in symbols_risk else "ANY_COINS"
#                 action = task["status"]
#                 position_data = task["position_data"]

#                 # Проставим плечо и тип маржи, если debug_label новый
#                 leverage = symbols_risk.get(symbol_risk_key, {}).get("leverage", 1)
#                 margin_type = symbols_risk.get(symbol_risk_key, {}).get("margin_type", "CROSSED")

#                 last_known_label = self.last_debug_label \
#                     .setdefault(user_name, {}) \
#                     .setdefault(symbol, {}) \
#                     .setdefault(position_side, None)
                
#                 pos = self.context.position_vars.get(user_name, {}) \
#                     .get(strategy_name, {}) \
#                     .get(symbol, {}) \
#                     .get(position_side)
                
#                 in_position = pos and pos.get("in_position")                

#                 if action == "is_closing":                    
#                     if not in_position:
#                         return                 
                    
#                 elif action == "is_opening":             
#                     if in_position:
#                         return

#                 if debug_label != last_known_label:
#                     await binance_client.set_margin_type(client_session, strategy_name, symbol, margin_type)
#                     await binance_client.set_leverage(client_session, strategy_name, symbol, leverage)
#                     self.last_debug_label[user_name][symbol][position_side] = debug_label

#                 last_avg_price = pos_data.get("avg_price", None)

#                 # Основной запрос на маркет-ордер
#                 market_order_result = await binance_client.make_order(
#                     session=client_session,
#                     strategy_name=strategy_name,
#                     symbol=symbol,
#                     qty=qty,
#                     side=side,
#                     position_side=position_side,
#                     market_type="MARKET"
#                 )

#                 success, validated = self.risk_set.validate.validate_market_response(
#                     market_order_result[0], debug_label
#                 )
#                 if not success and action == "is_opening":
#                     self.error_handler.debug_info_notes(
#                         f"[INFO][{debug_label}] не удалось нормально открыть позицию.",
#                         is_print=True
#                     )
#                     return

#                 if action in {"is_avg", "is_closing"}:
#                     position_data["trailing_sl_progress_counter"] = 0

#                     for attempt in range(3):  # максимум 3 попытки
#                         if await self.risk_set.cancel_all_risk_orders(
#                             session=client_session,
#                             user_name=user_name,
#                             strategy_name=strategy_name,
#                             symbol=symbol,
#                             position_side=position_side,
#                             risk_suffix_list=['tp', 'sl'],
#                             cancel_order_by_id=binance_client.cancel_order_by_id
#                         ):
#                             break
#                         await asyncio.sleep(0.15)
#                     else:
#                         # цикл не прервался — не дождались обновления
#                         self.error_handler.debug_error_notes(f"[INFO][{debug_label}] не удалось отменить риск ордера после 3-х попыток ")
#                     if action == "is_closing":
#                         return
                
#                 if action in {"is_opening", "is_avg"}:
#                     # ждем, пока контекст обновит in_position и avg_price
#                     for attempt in range(120):
#                         pos_data = self.context.position_vars.get(user_name, {}) \
#                             .get(strategy_name, {}) \
#                             .get(symbol, {}) \
#                             .get(position_side, {})
#                         avg_price = pos_data.get("avg_price")
#                         in_position = pos_data.get("in_position")

#                         if in_position and avg_price != last_avg_price and avg_price is not None:
#                             self.error_handler.debug_info_notes(
#                                 f"[READY][{debug_label}] pos_data обновлены на попытке {attempt+1}: "
#                                 f"avg_price={avg_price}, in_position={in_position}"
#                             )
#                             break
#                         await asyncio.sleep(0.15)
#                     else:
#                         # цикл не прервался — не дождались обновления
#                         self.error_handler.debug_error_notes(
#                             f"[TIMEOUT][{debug_label}] не удалось дождаться avg_price/in_position "
#                             f"(avg_price={avg_price}, in_position={in_position})"
#                         )

#                     for attempt in range(3):  # максимум 3 попытки
#                         if await self.risk_set.place_all_risk_orders(
#                             session=client_session,
#                             user_name=user_name,
#                             strategy_name=strategy_name,
#                             symbol=symbol,
#                             position_side=position_side,
#                             risk_suffix_list=['tp', 'sl'],
#                             place_risk_order=binance_client.place_risk_order
#                         ):
#                             break
#                         await asyncio.sleep(0.15)
#                     else:
#                         # цикл не прервался — не дождались обновления
#                         self.error_handler.debug_error_notes(f"[CRITICAL][{debug_label}] не удалось установить риск ордера после 3-х попыток.")

#             except Exception as e:
#                 self.error_handler.debug_error_notes(
#                     f"[Order Error] {task['debug_label']} → {e}",
#                     is_print=True
#                 )

#         tasks = []

#         for task in task_list:
#             try:
#                 action = task["status"]
#                 user_name = task["user_name"]
#                 strategy_name = task["strategy_name"]
#                 symbol = task["symbol"]
#                 position_side = task["position_side"]
#                 debug_label = task["debug_label"]

#                 if action == "is_trailing":
#                     tasks.append(make_trailing_task(task))
#                     continue

#                 if action == "is_closing":
#                     side = "SELL" if position_side == "LONG" else "BUY"
#                     qty = task["position_data"].get("comul_qty", 0.0)

#                 elif action in ["is_opening", "is_avg"]:
#                     side = "BUY" if position_side == "LONG" else "SELL"
#                     symbols_risk = self.context.total_settings[task["user_name"]]["symbols_risk"]
#                     symbol_risk_key = task["symbol"] if task["symbol"] in symbols_risk else "ANY_COINS"
#                     leverage = symbols_risk.get(symbol_risk_key, {}).get("leverage", 1)

#                     for _ in range(5):  # 1 + 3 попытки
#                         cur_price = await self.get_cur_price(
#                             session=task["client_session"],
#                             ws_price_data=self.context.ws_price_data,
#                             symbol=task["symbol"],
#                             get_hot_price=self.get_hot_price
#                         )
#                         if cur_price:
#                             break
#                         await asyncio.sleep(0.25)
#                     else:
#                         # цикл не прервался — не дождались обновления
#                         self.error_handler.debug_error_notes(
#                             f"[CRITICAL][{debug_label}] не удалось установить получить цену при выставлении ордера (is_opening, is_avg)."
#                         )
#                         continue

#                     pos_martin = (
#                         self.context.position_vars
#                             .setdefault(user_name, {})
#                             .setdefault(strategy_name, {})
#                             .setdefault(symbol, {})
#                             .setdefault("martin", {})
#                             .setdefault(position_side, {})
#                     )

#                     base_margin = symbols_risk.get(symbol_risk_key, {}).get("margin_size", 0.0)
#                     margin_size = pos_martin.get("cur_margin_size")
#                     if margin_size is None:
#                         margin_size = base_margin               

#                     print(f"{debug_label}: total margin: {margin_size} usdt")
#                     qty = self.pos_utils.size_calc(
#                         margin_size=margin_size,
#                         entry_price=cur_price,
#                         leverage=leverage,
#                         volume_rate=task["position_data"].get("process_volume"),
#                         precision=task["qty_precision"],
#                         dubug_label=debug_label
#                     )
#                 else:
#                     self.error_handler.debug_info_notes(f"{debug_label} Неизвестный маркер ордера. ")
#                     continue

#                 if not qty or qty <= 0:
#                     self.error_handler.debug_info_notes(f"{debug_label} Нулевой размер позиции — пропуск")
#                     continue

#                 tasks.append(make_trade_task(task, side, qty))

#             except Exception as e:
#                 self.error_handler.debug_error_notes(
#                     f"[compose_trade_instruction] Ошибка при подготовке задачи: {task}\n→ {e}",
#                     is_print=True
#                 )

#         return await asyncio.gather(*tasks)




# class WS_HotPrice_Sream:
#     """Менеджер WebSocket для получения последних сделок (hot price) с Binance Futures."""

#     def __init__(
#         self,
#         context: BotContext,
#         error_handler: ErrorHandler,
#         proxy_list: Optional[str] = [None],
#         ws_url: str = "wss://fstream.binance.com/"
#     ):
#         error_handler.wrap_foreign_methods(self)
#         self.error_handler = error_handler
#         self.context = context

#         # --- состояние ---
#         self.session: Optional[aiohttp.ClientSession] = None
#         self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
#         self.ws_task: Optional[asyncio.Task] = None
#         self.is_connected: bool = False

#         # --- параметры ---
#         self.max_reconnect_attempts: int = 50
#         self.reconnect_attempts: int = 0
#         self.ws_shutdown_event: asyncio.Event = asyncio.Event()
#         self.WEBSOCKET_URL: str = ws_url

#         # --- прокси ---
#         self.proxy_list: List[Optional[str]] = proxy_list or [None]
#         self.proxy_index: int = 0
#         self.proxy_url: Optional[str] = self.proxy_list[self.proxy_index]
#         self.proxy_auth: Optional[aiohttp.BasicAuth] = None

#     # ============================================================
#     #  Обработка входящих сообщений
#     # ============================================================
#     async def handle_ws_message(self, message: str) -> None:
#         try:
#             payload = json.loads(message)
#             data = payload.get("data")
#             if not data:
#                 return

#             symbol = data.get("s")
#             price = float(data.get("p", 0.0))
#             if not symbol or price <= 0:
#                 return

#             # обновляем горячую цену в контексте
#             self.context.ws_price_data[symbol] = {"close": price}

#         except Exception as e:
#             self.error_handler.debug_error_notes(
#                 f"[WS Handle] Error: {e}\n{traceback.format_exc()}"
#             )

#     # ============================================================
#     #  Ping keepalive
#     # ============================================================
#     async def keepalive_ping(self) -> None:
#         """Отправляет ping каждые 15 секунд."""
#         while not self.ws_shutdown_event.is_set() and self.websocket:
#             try:
#                 await self.websocket.ping()
#                 await asyncio.sleep(15)
#             except Exception as e:
#                 self.error_handler.debug_error_notes(f"[Ping] Ошибка: {e}")
#                 break

#     # ============================================================
#     #  Подключение и основной цикл
#     # ============================================================
#     async def connect_and_handle(self, symbols: List[str]) -> None:
#         if not symbols:
#             self.error_handler.debug_error_notes("[WS] Пустой список символов")
#             return

#         # --- формируем trade-стримы ---
#         streams = [f"{symbol.lower()}@trade" for symbol in symbols]
#         ws_url = f"{self.WEBSOCKET_URL}stream?streams={'/'.join(streams)}"

#         if not self.session:
#             self.session = aiohttp.ClientSession()

#         while self.reconnect_attempts < self.max_reconnect_attempts:
#             if self.ws_shutdown_event.is_set():
#                 break

#             try:
#                 # --- создаем websocket ---
#                 self.websocket = await self.session.ws_connect(
#                     ws_url,
#                     proxy=self.proxy_url,
#                     proxy_auth=self.proxy_auth,
#                     autoping=False
#                 )

#                 self.is_connected = True
#                 self.reconnect_attempts = 0
#                 self.error_handler.debug_info_notes(f"[WS] Connected: {ws_url}")

#                 # --- запускаем ping ---
#                 ping_task = asyncio.create_task(self.keepalive_ping())

#                 # --- читаем поток ---
#                 async for msg in self.websocket:
#                     if self.ws_shutdown_event.is_set():
#                         await self.websocket.close(code=1000, message=b"Shutdown")
#                         break

#                     if msg.type == aiohttp.WSMsgType.TEXT:
#                         await self.handle_ws_message(msg.data)
#                     elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
#                         break

#                 ping_task.cancel()
#                 with contextlib.suppress(asyncio.CancelledError):
#                     await ping_task

#             except Exception as e:
#                 self.error_handler.debug_error_notes(
#                     f"[WS Error] {e}\n{traceback.format_exc()}"
#                 )
#                 self.reconnect_attempts += 1
#                 backoff = min(2 * self.reconnect_attempts, 10)
#                 await asyncio.sleep(backoff)

#         self.is_connected = False
#         self.error_handler.debug_error_notes("[WS] Достигнут лимит переподключений")

#     # ============================================================
#     #  Управление WS
#     # ============================================================
#     async def connect_to_websocket(self, symbols: List[str]) -> None:
#         try:
#             await self.stop_ws_process()
#             self.ws_shutdown_event.clear()
#             self.reconnect_attempts = 0
#             self.ws_task = asyncio.create_task(self.connect_and_handle(symbols))
#         except Exception as e:
#             self.error_handler.debug_error_notes(f"[WS Connect] Failed: {e}")

#     async def restart_ws(self):
#         """Принудительный перезапуск."""
#         try:
#             await self.stop_ws_process()
#             await self.connect_to_websocket(list(self.context.fetch_symbols))
#             self.error_handler.debug_info_notes("[WS] Перезапущен")
#         except Exception as e:
#             self.error_handler.debug_error_notes(f"[WS Restart] Ошибка: {e}")

#     async def stop_ws_process(self) -> None:
#         """Останавливает текущий процесс WS."""
#         self.ws_shutdown_event.set()

#         if self.ws_task:
#             self.ws_task.cancel()
#             with contextlib.suppress(asyncio.CancelledError):
#                 await asyncio.wait_for(self.ws_task, timeout=5)
#             self.ws_task = None
#             self.is_connected = False

#         if self.websocket:
#             await self.websocket.close()
#             self.websocket = None

#         if self.session and not self.session.closed:
#             await self.session.close()

#         # self.error_handler.debug_info_notes("[WS] Процесс остановлен")

#     async def sync_ws_streams(self, active_symbols: list) -> None:
#         """Синхронизирует активные символы (перезапускает при изменении списка)."""
#         new_symbols_set = set(active_symbols)
#         if new_symbols_set != getattr(self, "last_symbols_set", set()):
#             self.last_symbols_set = new_symbols_set
#             if new_symbols_set:
#                 await self.connect_to_websocket(list(new_symbols_set))
#             else:
#                 await self.stop_ws_process()





# class NetworkManager:
#     def __init__(self, info_handler: ErrorHandler, proxy_list: Optional[List[Optional[str]]] = None,
#                  user_label: Optional[str] = None, stop_bot: bool = False):
#         info_handler.wrap_foreign_methods(self)
#         self.info_handler = info_handler

#         self.proxy_list: List[Optional[str]] = proxy_list or [None]
#         self.proxy_index: int = 0
#         self.proxy_url: Optional[str] = self.proxy_list[self.proxy_index]

#         self.user_label = user_label or "network"
#         self.session: Optional[aiohttp.ClientSession] = None
#         self._ping_task: Optional[asyncio.Task] = None
#         self.stop_bot = stop_bot

#     # ============================================================
#     #  СЕССИЯ
#     # ============================================================
#     async def initialize_session(self):
#         """Создает новую aiohttp-сессию, проксируя через текущий proxy_url."""
#         if self.session and not self.session.closed:
#             return

#         try:
#             if self.proxy_url:
#                 connector = aiohttp.TCPConnector(ssl=False)
#                 self.session = aiohttp.ClientSession(
#                     connector=connector,
#                     trust_env=False,
#                     proxy=self.proxy_url
#                 )
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: создана новая сессия с прокси {self.proxy_url}")
#             else:
#                 self.session = aiohttp.ClientSession()
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: создана новая сессия без прокси")
#         except Exception as e:
#             self.info_handler.debug_error_notes(
#                 f"{self.user_label}: ошибка при создании сессии: {e}"
#             )

#     async def _check_session_connection(self, session: aiohttp.ClientSession) -> tuple[bool, Optional[int]]:
#         """
#         Проверяет доступность Binance API через текущую сессию.
#         Возвращает (ok, status_code | None).
#         """
#         try:
#             async with session.get(CHECK_URL, timeout=8) as response:
#                 ok = (response.status == 200)
#                 if not ok:
#                     # тут явный лог по не-200 статусу
#                     self.info_handler.debug_error_notes(
#                         f"{self.user_label}: неуспешный HTTP статус → {response.status}"
#                     )
#                 return ok, response.status

#         except Exception as e:
#             self.info_handler.debug_error_notes(
#                 f"{self.user_label}: ошибка соединения → {type(e).__name__}: {e}"
#             )
#             return False, None

#     async def _switch_to_next_proxy(self):
#         """Переключает на следующий прокси из списка."""
#         self.proxy_index = (self.proxy_index + 1) % len(self.proxy_list)
#         self.proxy_url = self.proxy_list[self.proxy_index]
#         self.info_handler.debug_error_notes(
#             f"{self.user_label}: смена прокси → {self.proxy_url or 'без прокси'}"
#         )

#     # ============================================================
#     #  ПРОВЕРКА И ВОССТАНОВЛЕНИЕ
#     # ============================================================
#     async def validate_session(self) -> tuple[bool, bool, Optional[int]]:
#         """
#         Проверяет соединение и восстанавливает при необходимости.
#         Возвращает (ok, was_reconnected, last_status).
#         - ok: True, если удалось получить 200
#         - was_reconnected: был ли переход на другие прокси
#         - last_status: последний HTTP статус или None при сетевой ошибке
#         """
#         was_reconnected = False
#         last_status: Optional[int] = None

#         for attempt in range(1, len(self.proxy_list) * 2):  # 2 прохода по списку
#             await self.initialize_session()

#             ok, status = await self._check_session_connection(self.session)
#             last_status = status

#             if ok:
#                 return True, was_reconnected, last_status

#             # закрываем перед пересозданием
#             try:
#                 await self.session.close()
#             except Exception:
#                 pass

#             await self._switch_to_next_proxy()
#             await asyncio.sleep(min(3 + attempt, 15))
#             was_reconnected = True
#             self.info_handler.debug_error_notes(
#                 f"{self.user_label}: попытка переподключения #{attempt}"
#             )

#         self.info_handler.debug_error_notes(
#             f"❌ {self.user_label}: не удалось восстановить соединение после всех прокси", True
#         )
#         return False, was_reconnected, last_status

#     # ============================================================
#     #  ФОНОВАЯ ПРОВЕРКА / ПИНГ
#     # ============================================================
#     async def ping_session(self):
#         """
#         Поддерживает "живую" сессию, проверяя каждые SESSION_CHECK_INTERVAL секунд.
#         При сбое пересоздает сессию.
#         """
#         while not self.stop_bot:
#             ok, reconnected, status = await self.validate_session()
#             if not ok:
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: ping неудачен — сессия мертва (status={status})"
#                 )
#             elif reconnected:
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: сессия была пересоздана, status={status}"
#                 )
#             else:
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: ping OK, status={status}"
#                 )
#             await asyncio.sleep(SESSION_CHECK_INTERVAL)

#     async def start_ping_loop(self):
#         """Запускает фонового пингера."""
#         if not self._ping_task or self._ping_task.done():
#             self._ping_task = asyncio.create_task(self.ping_session())
#             self.info_handler.debug_error_notes(
#                 f"{self.user_label}: запущен фоновой ping-сервис"
#             )

#     async def shutdown_session(self):
#         """Закрывает aiohttp-сессию и останавливает пинг-задачу."""
#         if self._ping_task and not self._ping_task.done():
#             self._ping_task.cancel()
#             try:
#                 await self._ping_task
#             except asyncio.CancelledError:
#                 pass

#         if self.session and not self.session.closed:
#             try:
#                 await self.session.close()
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: aiohttp-сессия закрыта"
#                 )
#             except Exception as e:
#                 self.info_handler.debug_error_notes(
#                     f"{self.user_label}: ошибка при закрытии сессии: {e}"
#                 )





# import json

# def save_to_json(data: Optional[dict], filename="data.json"):
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


# # def load_from_json(filename: str = "data.json") -> Optional[Any]:
# #     """
# #     Загружает данные из JSON-файла.

# #     :param filename: str – путь до файла (например, '/home/user/data.json')
# #     :return: dict, list или None – данные из файла, либо None при ошибке
# #     """
# #     try:
# #         with open(filename, 'r', encoding='utf-8') as f:
# #             data = json.load(f)
# #         print(f"Файл загружен: {filename}")
# #         return data
# #     except FileNotFoundError:
# #         print(f"Файл не найден: {filename}")
# #     except json.JSONDecodeError as e:
# #         print(f"Ошибка чтения JSON ({filename}): {e}")
# #     except Exception as e:
# #         print(f"Ошибка при загрузке: {e}")

# #     return None


        
    # async def place_risk_order(
    #         self,
    #         session: aiohttp.ClientSession,
    #         strategy_name: str,
    #         symbol: str,
    #         qty: float,
    #         side: str,
    #         position_side: str,
    #         target_price: float,
    #         suffix: str
    #     ):
        
    #     """
    #     Универсальный метод для установки условных ордеров (SL/TP) на Binance Futures.

    #     :param suffix: 'sl' или 'tp' — для логирования
    #     :param market_type: 'STOP_MARKET' или 'TAKE_PROFIT_MARKET'
    #     """
    #     # print(f"suffix: {suffix}")
    #     try:
    #         params = {
    #             "symbol": symbol,
    #             "side": side,
    #             "type": "STOP_MARKET" if suffix == "sl" else "TAKE_PROFIT_MARKET",
    #             "quantity": abs(qty),
    #             "positionSide": position_side,
    #             "stopPrice": target_price,
    #             "closePosition": "true",
    #             "recvWindow": 20000,
    #             "newOrderRespType": 'RESULT'
    #         }
    #         headers = {
    #             'X-MBX-APIKEY': self.api_key
    #         }

    #         params = self.get_signature(params)
    #         async with session.post(self.create_order_url, headers=headers, params=params) as response:
    #             return await self.requests_logger(response, self.user_label, strategy_name, f"place_{suffix.lower()}_order", symbol, position_side)

    #     except Exception as ex:
    #         self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

    #     return {}, self.user_label, strategy_name, symbol, position_side




        
    # async def cancel_order_by_id(
    #         self,
    #         session: aiohttp.ClientSession,
    #         strategy_name: str,
    #         symbol: str,
    #         order_id: str,
    #         suffix: str
    #     ):
    #     """
    #     Универсальный метод отмены ордера по order_id (SL или TP).
    #     Параметр `suffix`: 'SL' или 'TP'
    #     """
    #     try:
    #         params = {
    #             "symbol": symbol,
    #             "orderId": order_id,
    #             "recvWindow": 20000
    #         }
    #         headers = {
    #             'X-MBX-APIKEY': self.api_key
    #         }

    #         params = self.get_signature(params)
    #         async with session.delete(self.cancel_order_url, headers=headers, params=params) as response:
    #             return await self.requests_logger(response, self.user_label, strategy_name, f"cancel_{suffix.lower()}_order", symbol, order_id)

    #     except Exception as ex:
    #         self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

    #     return {}, self.user_label, strategy_name, symbol, order_id