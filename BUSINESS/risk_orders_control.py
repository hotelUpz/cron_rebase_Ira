import aiohttp
from typing import *
from b_context import BotContext
from c_log import ErrorHandler
from c_utils import PositionUtils
from d_bapi import BinancePrivateApi
# from .patterns import RiskSet


class TP_FALLBACK:
    def __init__(
            self,
            context: BotContext,
            info_handler: ErrorHandler
        ):
        info_handler.wrap_foreign_methods(self)
        self.context = context
        self.info_handler = info_handler

    def tp_control(self, tp: float, nPnl: float, debug_label: str) -> bool:
        """
        Контроль тейк-профита с валидацией входных данных.
        """
        if not isinstance(tp, (int, float)) or not isinstance(nPnl, (int, float)):
            self.info_handler.debug_info_notes(
                f"{debug_label}[TP_CONTROL] Невалидные типы: ({type(tp)}), ({type(nPnl)})"
            )
            return False

        return nPnl >= tp

    def check_tp(
        self,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        nPnl: float,
        normalized_sign: int,
        symbols_risk: dict,
        debug_label: str
        
    ) -> Optional[bool]:
        """
        Проверяет условия для тейк-профита и при необходимости инициирует сигнал закрытия позиции.
        """
        key_symb = "ANY_COINS" if symbol not in symbols_risk else symbol
        take_profit = symbols_risk.get(key_symb, {}).get("fallback_tp")

        if take_profit is None:
            return None

        # Применяем нормализованный знак на PnL
        signed_nPnl = nPnl * normalized_sign

        if not self.tp_control(take_profit, signed_nPnl, debug_label):
            return None

        # Логируем действие
        self.info_handler.trades_info_notes(
            f"[{user_name}][{strategy_name}][{symbol}][{position_side}]. '🏆 Закрываем позицию по резервному тейк-профиту.'. ",
            True
        )

        return True     

class Average:
    def __init__(
            self,
            context: BotContext,
            info_handler: ErrorHandler,
        ):
        info_handler.wrap_foreign_methods(self)
        self.context = context
        self.info_handler = info_handler

    def avg_control_func(
        self,
        grid_orders: list,
        avg_progress_counter: int,
        normalized_sign: int,
        nPnl: float,
        debug_label: str,
    ) -> tuple[int, float]:
        """
        Контроль усреднения.
        Возвращает:
            - обновлённый прогресс (int),
            - объём текущего шага (float), либо 0.0 если усреднение не нужно.
        """
        if not grid_orders or not isinstance(grid_orders, list):
            self.info_handler.debug_info_notes(f"{debug_label} Невалидный grid_orders: ожидался список.")
            return avg_progress_counter, 0.0

        if not isinstance(avg_progress_counter, int) or avg_progress_counter < 0:
            self.info_handler.debug_info_notes(f"{debug_label} Некорректный avg_progress_counter: {avg_progress_counter}")
            return avg_progress_counter, 0.0

        len_grid_orders = len(grid_orders)

        if len_grid_orders <= 1 or avg_progress_counter >= len_grid_orders:
            return avg_progress_counter, 0.0

        step = grid_orders[min(avg_progress_counter, len_grid_orders - 1)]
        indent = -abs(step.get("indent", 0.0))
        volume = step.get("volume", 0.0)

        avg_nPnl = nPnl * normalized_sign

        if avg_nPnl <= indent:
            new_progress = avg_progress_counter + 1
            grid_index = min(new_progress, len_grid_orders-1)

            return grid_index, volume

        return avg_progress_counter, 0.0

    def check_avg_and_report(
        self,
        cur_price: float,
        symbol_data: dict,
        nPnl: float,
        normalized_sign: int,
        settings_pos_options: Dict,
        debug_label: str,
    ) -> bool:
        """Проверяет необходимость усреднения и формирует сигнал."""
        grid_cfg = settings_pos_options["entry_conditions"]["grid_orders"]
        cur_avg_progress = symbol_data.get("avg_progress_counter", 1)

        new_avg_progress, avg_volume = self.avg_control_func(
            grid_cfg,
            cur_avg_progress,
            normalized_sign,
            nPnl,
            debug_label,
        )

        if new_avg_progress == cur_avg_progress or avg_volume == 0.0:
            return False

        symbol_data["avg_progress_counter"] = new_avg_progress
        symbol_data["process_volume"] = avg_volume

        safe_idx = min(new_avg_progress-1, len(grid_cfg) - 1)
        self.info_handler.trades_info_notes(
            f"[{debug_label}] ➗ Усредняем. "
            f"Счётчик {cur_avg_progress} → {new_avg_progress}. "
            f"Cur vol: {avg_volume} "
            f"Cur price: {cur_price} "
            f"Indent: {grid_cfg[safe_idx]}",
            True,
        )
        return True

class RiskOrdersControl:
    """Управляет рисками и мониторингом позиций для торговых стратегий."""

    def __init__(
            self,
            context: BotContext,
            info_handler: ErrorHandler,
            pos_utils: PositionUtils
        ):
        info_handler.wrap_foreign_methods(self)
        self.context = context
        self.info_handler = info_handler
        self.pos_utils = pos_utils

        self.avg_control = Average(context=context, info_handler=info_handler)
        self.tp_control = TP_FALLBACK(context=context, info_handler=info_handler)

    # ================================================================
    #     1️⃣ ЛОГИКА ТЕЙК-ПРОФИТА (отделена полностью)
    # ================================================================
    def check_take_profit_logic(
        self,
        *,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        symbol_pos: dict,
        symbols_risk: dict,
        normalized_sign: int,
        cur_nPnl: float,
        compose_signals: Callable,
        client_session: Optional[aiohttp.ClientSession],
        binance_client: BinancePrivateApi,
        debug_label: str
    ):
        """Проверка срабатывания fallback TP."""

        tp_res = self.tp_control.check_tp(
            user_name=user_name,
            strategy_name=strategy_name,
            symbol=symbol,
            position_side=position_side,
            nPnl=cur_nPnl,
            normalized_sign=normalized_sign,
            symbols_risk=symbols_risk,
            debug_label=debug_label
        )

        # нет сигнала
        if not tp_res:
            return None

        # этот TP уже срабатывал раньше
        if symbol_pos.get("is_tp"):
            return None

        # ставим флаг (важно!)
        symbol_pos["is_tp"] = True

        # отдаём сигнал платформе
        return compose_signals(
            user_name, strategy_name, symbol, position_side,
            "is_closing", client_session, binance_client
        )

    # ================================================================
    #     2️⃣ ЛОГИКА УСРЕДНЕНИЯ (отделена полностью)
    # ================================================================
    def check_average_logic(
        self,
        *,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        symbol_pos: dict,
        settings_pos_options: dict,
        normalized_sign: int,
        cur_price: float,
        cur_nPnl2: float,
        compose_signals: Callable,
        client_session: Optional[aiohttp.ClientSession],
        binance_client: BinancePrivateApi,
        debug_label: str
    ):
        """Проверка усреднения (+ логирование)."""

        avg_res = self.avg_control.check_avg_and_report(
            cur_price=cur_price,
            symbol_data=symbol_pos,
            nPnl=cur_nPnl2,
            normalized_sign=normalized_sign,
            settings_pos_options=settings_pos_options,
            debug_label=debug_label
        )

        if not avg_res:
            return None

        # даём сигнал на открытие усреднения
        return compose_signals(
            user_name, strategy_name, symbol, position_side,
            "is_avg", client_session, binance_client
        )

    # ================================================================
    #     3️⃣ ОСНОВНОЙ МЕТОД – ТЕПЕРЬ ТОЛЬКО ДИРИЖЁР
    # ================================================================
    def risk_symbol_monitoring(
        self,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        compose_signals: Callable,
        client_session: Optional[aiohttp.ClientSession],
        binance_client: BinancePrivateApi
    ):
        """
        Мониторит позицию и управляет рисками.
        Теперь логика полностью вынесена в отдельные методы.
        """

        debug_label = f"[{user_name}][{strategy_name}][{symbol}][{position_side}]"
        symbol_data = self.context.position_vars.get(user_name, {}).get(strategy_name, {}).get(symbol, {})
        symbol_pos = symbol_data.get(position_side, {})
        symbols_risk = self.context.total_settings.get(user_name, {}).get("symbols_risk", {})
        settings_pos_options = self.context.strategy_notes.get(strategy_name, {}).get(position_side, {})

        try:
            # ---------------------- проверки базовые ----------------------
            if not symbol_pos.get("in_position"):
                return
            
            if symbol_pos.get("is_tp"):
                return

            normalized_sign = {"LONG": 1, "SHORT": -1}.get(position_side)
            if normalized_sign is None:
                self.info_handler.debug_error_notes(f"Invalid position_side {debug_label}")
                return

            cur_price = self.context.ws_price_data.get(symbol, {}).get("close")
            if not cur_price:
                return

            avg_price = symbol_pos.get("avg_price", 0.0)
            if not avg_price:
                return

            # ---------------------- PnL по AVG ----------------------
            cur_nPnl = self.pos_utils.nPnL_calc(
                cur_price, avg_price, debug_label
            )
            if cur_nPnl is None:
                return

            # 1️⃣ Проверяем TAKE-PROFIT
            tp_signal = self.check_take_profit_logic(
                user_name=user_name,
                strategy_name=strategy_name,
                symbol=symbol,
                position_side=position_side,
                symbol_pos=symbol_pos,
                symbols_risk=symbols_risk,
                normalized_sign=normalized_sign,
                cur_nPnl=cur_nPnl,
                compose_signals=compose_signals,
                client_session=client_session,
                binance_client=binance_client,
                debug_label=debug_label
            )

            if tp_signal:
                return tp_signal

            # ---------------------- PnL по ENTRY ----------------------
            entry_price = symbol_pos.get("entry_price", 0.0)
            if not entry_price:
                return

            cur_nPnl2 = self.pos_utils.nPnL_calc(
                cur_price, entry_price, debug_label
            )
            if cur_nPnl2 is None:
                return

            # 2️⃣ Проверяем УСРЕДНЕНИЕ
            avg_signal = self.check_average_logic(
                user_name=user_name,
                strategy_name=strategy_name,
                symbol=symbol,
                position_side=position_side,
                symbol_pos=symbol_pos,
                settings_pos_options=settings_pos_options,
                normalized_sign=normalized_sign,
                cur_price=cur_price,
                cur_nPnl2=cur_nPnl2,
                compose_signals=compose_signals,
                client_session=client_session,
                binance_client=binance_client,
                debug_label=debug_label
            )

            if avg_signal:
                return avg_signal

        except aiohttp.ClientError as e:
            self.info_handler.debug_error_notes(f"[HTTP Error] {debug_label}: {e}", True)

        except Exception as e:
            self.info_handler.debug_error_notes(f"[Unexpected Error] {debug_label}: {e}", True)
