from datetime import datetime
import re
from c_log import ErrorHandler, log_time
import inspect


def validate_symbol(symbol: str) -> bool:
    """
    Проверяет валидность торгового символа.
    """
    if not isinstance(symbol, str) or not symbol:
        return False
    if not re.match(r"^[A-Z0-9]+$", symbol):
        return False
    return True

class TimeframeValidator:
    close_bar_map = {
        "1m": (1, "minute", 1),
        "5m": (5, "minute", 5),
        "15m": (15, "minute", 15),
        "30m": (30, "minute", 30),
        "1h": (1, "hour", 60),
        "4h": (4, "hour", 240),
        "12h": (12, "hour", 720),
        "1d": (1, "day", 1440),
    }

    def __init__(self, info_handler: ErrorHandler):    
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.tfr_bar_cache = {}

    def flatten_dict(self, d):
        parts = []
        for k, v in sorted(d.items()):
            if isinstance(v, dict):
                nested = self.flatten_dict(v)
                parts.append(f"{k}__{nested}")
            else:
                parts.append(f"{k}:{v}")
        return "_".join(parts)

    @staticmethod
    def get_current_value(unit: str) -> int:
        now = datetime.now()
        return {
            "minute": now.minute,
            "hour": now.hour,
            "day": now.day,
        }[unit]
    
    def close_bar_checking(self, tfr: str) -> bool:
        now = datetime.now()
        bar_int, unit, _ = self.close_bar_map[tfr]

        if unit == "minute":
            return now.minute % bar_int == 0
        elif unit == "hour":
            return now.hour % bar_int == 0 and now.minute == 0
        elif unit == "day":
            return now.day % bar_int == 0 and now.hour == 0 and now.minute == 0
        return False

    def are_timeframes_compatible(self, tfr_list: list) -> bool:
        if len(tfr_list) <= 1:
            return True
        values = [self.close_bar_map[tfr][2] for tfr in tfr_list]
        base = values[0]
        return all(v % base == 0 for v in values)

    def tfr_validate(self, entry_rules: dict) -> tuple[bool, bool]:
        unik_cache_key = self.flatten_dict(entry_rules)
        if unik_cache_key not in self.tfr_bar_cache:
            tfr_list = [val.get('tfr') for val in entry_rules.values() if val.get('tfr')]
            if not tfr_list:
                return True, False

            sorted_tfr = sorted(tfr_list, key=lambda x: self.close_bar_map[x][2])
            max_tfr = sorted_tfr[-1]
            compatible = self.are_timeframes_compatible(sorted_tfr)
            self.tfr_bar_cache[unik_cache_key] = (max_tfr, compatible)
        else:
            max_tfr, compatible = self.tfr_bar_cache[unik_cache_key]

        is_closed = self.close_bar_checking(max_tfr)
        return compatible, is_closed
    

class OrderValidator:
    """
    Отвечает за валидацию ответов от Binance при установке и отмене ордеров.
    """
    def __init__(self, info_handler: ErrorHandler):    
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

    def validate_market_response(self, order_answer, debug_label) -> tuple[bool, dict | None]:
        """Обработка логирования результатов ордера и возврат qty/price."""
        if not order_answer:
            self.info_handler.debug_error_notes(
                f"Ошибка создания ордера: \n{order_answer}. {debug_label}"
            )
            return False, None

        try:
            now_time = log_time()
            specific_keys = ["orderId", "symbol", "positionSide", "side", "executedQty", "avgPrice"]
            order_details = "\n".join(f"{k}: {order_answer[k]}" for k in specific_keys if k in order_answer)
            order_answer_str = f'Время создания ордера: {now_time}\n{order_details}'
        except Exception as ex:
            self.info_handler.debug_error_notes(
                f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}"
            )
            return False, None

        if order_answer.get('status') in ['FILLED', 'NEW', 'PARTIALLY_FILLED']:
            self.info_handler.trades_info_notes(f"{debug_label}: {order_answer_str}. ")
            return True, {
                "qty": abs(float(order_answer.get("executedQty", 0.0))),
                "price": float(order_answer.get("avgPrice", 0.0))
            }

        self.info_handler.debug_info_notes(
            f"{debug_label}: {order_answer_str}. ",
            False
        )
        return False, None

    def validate_risk_response(
        self,
        order_response,
        suffix: str,
        dubug_label: str = None
    ) -> tuple[bool, int | None]:
        """
        Проверка валидности установки SL/TP ордера.
        :returns: (успешность, order_id или None)
        """
        try:
            if order_response and isinstance(order_response[0], dict):
                order_data = order_response[0]

                # Проверка на Binance ошибку
                if "code" in order_data and order_data["code"] < 0:
                    self.info_handler.debug_error_notes(f"{dubug_label} ❌ Binance ошибка при установке {suffix}: {order_data}")
                    return False, None

                if "orderId" in order_data and order_data.get("status") != "REJECTED":
                    order_id = order_data["orderId"]
                    self.info_handler.trades_info_notes(f"{dubug_label} Новый {suffix}-ордер установлен: {order_id}", True)
                    return True, order_id
                else:
                    self.info_handler.debug_error_notes(
                        f"{dubug_label} ❌ Ошибка при установке {suffix}: {order_data}",
                        False
                    )
            else:
                self.info_handler.debug_error_notes(
                    f"{dubug_label} ❌ Неизвестный ответ при установке {suffix}: {order_response}",
                    False
                )
        except Exception as ex:
            self.info_handler.debug_error_notes(
                f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}",
                False
            )

        return False, None

    def validate_cancel_risk_response(
        self,
        cancel_response_tuple,
        suffix: str,
        dubug_label: str = None
    ) -> bool:
        """
        Проверка успешности отмены SL/TP ордера.
        :returns: True если отмена успешна или ордер уже не существует.
        """
        try:
            if cancel_response_tuple and isinstance(cancel_response_tuple[0], dict):
                cancel_response = cancel_response_tuple[0]

                # Успешная отмена
                if cancel_response.get("status") == "CANCELED":
                    return True

                # Binance ошибка "Unknown order sent" - считаем нефатальной
                if cancel_response.get("code") == -2011:
                    self.info_handler.trades_info_notes(
                        f"{dubug_label} ⚠️ Ордер уже отменён или не существует ({suffix}).", True
                    )
                    return True

                # Иная ошибка
                self.info_handler.debug_error_notes(
                    f"{dubug_label} ❌ Ошибка при отмене {suffix}: {cancel_response}"
                )

            else:
                self.info_handler.debug_error_notes(
                    f"{dubug_label} ❌ Неизвестный ответ при отмене {suffix}: {cancel_response_tuple}"
                )

        except Exception as ex:
            self.info_handler.debug_error_notes(
                f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}",
                False
            )

        return False


class HTTP_Validator:
    def __init__(self, info_handler: ErrorHandler):    
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

    async def _status_extracter(self, resp):
        """Проверяет и возвращает данные запроса и статус."""
        try:
            return await resp.json(), resp.status
        except Exception as e:
            print(f"Ошибка при разборе JSON. File c_log: {e}")
            return None, None
        
    async def _req_info_handler(self, user_name, strategy_name, target, error_text, error_code, symbol=None):
        """Логирует ошибку в ответе."""
        error_dict = {
            "user_name": user_name,
            "strategy_name": strategy_name,
            "error_text": error_text,
            "error_code": error_code,
            "target": target,
            "time": f"{log_time()}"
        }
        if symbol:
            error_dict["symbol"] = symbol
        # async with self.async_lock:
        self.info_handler.trade_secondary_list.append(error_dict)

    async def _log_sorter(self, is_success, data, status, user_name, strategy_name, target, symbol=None):
        """Логирование успешных и ошибочных запросов."""
        log_entry = {
            "id": f"[{user_name}][{strategy_name}]",
            "target": target,            
            "request_text" if is_success else "error_text": data,
            "request_code" if is_success else "error_code": status,
            "time": f"{log_time()}"
        }
        if symbol:
            log_entry["symbol"] = symbol

        if target == "place_order":
            # async with self.async_lock:
            (self.info_handler.trade_succ_list if is_success else self.info_handler.trade_failed_list).append(log_entry)
        else:
            # async with self.async_lock:
            self.info_handler.trade_secondary_list.append(log_entry)

    async def requests_logger(self, resp, user_name, strategy_name, target, symbol=None, pos_side=None):
        """Обработка и логирование данных запроса."""
        if resp is None:
            await self._req_info_handler(user_name, strategy_name, target, "Response is None", "N/A", symbol)
            return None

        resp_j, status = await self._status_extracter(resp)

        # Определяем успешность запроса
        is_success = isinstance(resp_j, dict) and status == 200

        # Логируем результат запроса
        await self._log_sorter(
            is_success,
            resp_j if is_success else await resp.text(),
            status,    
            user_name,    
            strategy_name,            
            target,
            symbol
        )

        return resp_j, user_name, strategy_name, symbol, pos_side