import asyncio
from collections import defaultdict
from typing import Callable, List, Dict, Optional

from b_context import BotContext
from c_log import ErrorHandler
from c_utils import PositionUtils
from c_validators import OrderValidator
from d_bapi import BinancePrivateApi


# =====================================================================
# ============================== RISK SET ==============================
# =====================================================================

class RiskSet:
    """
    Управляет постановкой TP/SL и корректной отменой ВСЕХ риск-ордеров
    по SYMBOL + POSITION_SIDE (через cancel_orders_by_symbol_side).
    """

    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        validate: OrderValidator
    ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.context = context
        self.validate = validate

    # ------------------------------------------------------------------
    # ОТМЕНА ВСЕХ РИСК-ОРДЕРОВ ПО SYMBOL + SIDE
    # ------------------------------------------------------------------
    async def cancel_orders_for_side(
        self,
        session,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        binance_client: BinancePrivateApi
    ) -> bool:

        debug = f"[{user_name}][{strategy_name}][{symbol}][{position_side}]"

        try:
            await binance_client.cancel_orders_by_symbol_side(
                session=session,
                symbol=symbol,
                position_side=position_side
            )
            return True
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[CANCEL ERROR]{debug} → {e}",
                is_print=True
            )
            return False

    # ------------------------------------------------------------------
    # Постановка ОДНОГО TP/SL (ID не используем)
    # ------------------------------------------------------------------
    async def _place_one(
        self,
        session,
        user: str,
        strategy: str,
        symbol: str,
        side: str,                 # LONG / SHORT (позиция)
        suffix: str,               # "tp" или "sl"
        place_risk_order: Callable,
    ) -> bool:

        debug = f"[{user}][{strategy}][{symbol}][{side}]"

        cfg = self.context.total_settings[user]["symbols_risk"]
        key = symbol if symbol in cfg else "ANY_COINS"

        percent = cfg[key].get(suffix)   # tp / sl в %
        if percent is None:
            # Не задан TP/SL — считаем, что всё ок, просто нечего ставить
            return True

        pos = self.context.position_vars[user][strategy][symbol][side]
        avg_price = pos.get("avg_price")
        qty = pos.get("comul_qty")
        precision = self.context.position_vars[user][strategy][symbol].get("price_precision", 2)

        if not avg_price or not qty:
            self.info_handler.debug_error_notes(
                f"[ERROR]{debug} tp/sl calc skipped: avg_price={avg_price}, qty={qty}"
            )
            return False

        is_long = side == "LONG"
        sign = 1 if is_long else -1
        # для SL — процент берём со знаком минус
        shift_pct = percent if suffix == "tp" else -abs(percent)

        try:
            target = round(avg_price * (1 + sign * shift_pct / 100), precision)
        except Exception as e:
            self.info_handler.debug_error_notes(f"[ERROR]{debug} tp/sl calc failed: {e}")
            return False

        order_side = "SELL" if is_long else "BUY"

        try:
            response = await place_risk_order(
                session=session,
                strategy_name=strategy,
                symbol=symbol,
                qty=qty,
                side=order_side,
                position_side=side,
                target_price=target,
                suffix=suffix,
                order_type=cfg[key].get("tp_order_type")
            )
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[ERROR]{debug} place_risk_order failed: {e}",
                is_print=True
            )
            return False

        validated = self.validate.validate_risk_response(response, suffix.upper(), debug)
        return bool(validated)

    # ------------------------------------------------------------------
    async def place_all_risk_orders(
        self,
        session,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        risk_suffix_list: List[str],      # ['tp', 'sl'] или только ['tp'] / ['sl']
        place_risk_order: Callable
    ) -> List[bool]:
        """
        Ставит все нужные риск-ордера (TP/SL) параллельно.
        """
        if not risk_suffix_list:
            return []

        tasks = [
            self._place_one(
                session,
                user_name,
                strategy_name,
                symbol,
                position_side,
                suffix,
                place_risk_order
            )
            for suffix in risk_suffix_list
        ]
        return await asyncio.gather(*tasks)


# =====================================================================
# =========================== HANDLE ORDERS ============================
# =====================================================================

class HandleOrders:
    """
    - группирует задачи по юзерам и символам
    - делает MARKET-ордера
    - ждёт обновления позиции в контексте
    - ставит TP/SL
    - отменяет TP/SL через cancel_orders_by_symbol_side
    """

    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        pos_utils: PositionUtils,
        risk_set: RiskSet,
        get_hot_price: Callable,
        get_cur_price: Callable
    ):
        info_handler.wrap_foreign_methods(self)
        self.context = context
        self.info_handler = info_handler
        self.pos_utils = pos_utils
        self.risk_set = risk_set
        self.get_hot_price = get_hot_price
        self.get_cur_price = get_cur_price

    # ------------------------------------------------------------------
    @staticmethod
    def _market_side(status: str, position_side: str) -> str:
        """
        Определяет сторону MARKET-ордера:
        - is_opening / is_avg — в сторону позиции
        - is_closing          — против позиции
        """
        is_long = position_side == "LONG"

        if status in ("is_opening", "is_avg"):
            return "BUY" if is_long else "SELL"

        if status == "is_closing":
            return "SELL" if is_long else "BUY"

        raise ValueError(f"Unknown status '{status}' for _market_side()")

    # ------------------------------------------------------------------
    async def _wait_for_position_update(
        self,
        user_name: str,
        strategy_name: str,
        symbol: str,
        position_side: str,
        prev_avg_price: Optional[float],
        debug_label: str,
        attempts: int = 80,
        delay: float = 0.15
    ) -> Optional[Dict]:
        """
        Ждём, пока Sync обновит позицию после MARKET-ордера.
        Условия успеха:
        - in_position = True
        - avg_price не None и не равен prev_avg_price
        - comul_qty > 0
        """

        for attempt in range(1, attempts + 1):
            symbol_data = (
                self.context.position_vars
                    .get(user_name, {})
                    .get(strategy_name, {})
                    .get(symbol, {})
            )
            pos = symbol_data.get(position_side, {}) if symbol_data else {}

            in_position = pos.get("in_position", False)
            avg_price = pos.get("avg_price")
            comul_qty = pos.get("comul_qty", 0.0)

            if in_position and avg_price and avg_price != prev_avg_price and comul_qty > 0:
                self.info_handler.debug_info_notes(
                    f"[READY]{debug_label} pos_data updated on attempt {attempt}: "
                    f"avg_price={avg_price}, qty={comul_qty}",
                    False
                )
                return pos

            await asyncio.sleep(delay)

        self.info_handler.debug_error_notes(
            f"[TIMEOUT]{debug_label} position not updated: "
            f"prev_avg_price={prev_avg_price}",
            True
        )
        return None

    # ------------------------------------------------------------------
    async def compose_trade_instruction(self, task_list: List[Dict]):
        """
        Внешняя точка входа:
        task_list — список словарей от SIGNALS.compose_signals / RiskOrdersControl.
        """
        groups = defaultdict(list)
        for t in task_list:
            groups[t["user_name"]].append(t)

        await asyncio.gather(*[
            self._process_user_tasks(user_tasks)
            for user_tasks in groups.values()
        ])

    # ------------------------------------------------------------------
    async def _process_user_tasks(self, tasks_for_user: List[Dict]):
        """
        Для одного юзера группируем задачи по символам.
        """
        by_symbol = defaultdict(list)
        for t in tasks_for_user:
            by_symbol[t["symbol"]].append(t)

        # по каждому символу выполняем задачи последовательно
        await asyncio.gather(*[
            self._handle_symbol(symbol, symbol_tasks)
            for symbol, symbol_tasks in by_symbol.items()
        ])

    # ------------------------------------------------------------------
    async def _handle_symbol(self, symbol: str, tasks: List[Dict]):
        """
        Для одного символа последовательно исполняем задания.
        (без барьеров и сложной синхронизации)
        """
        for t in tasks:
            qty = await self._calc_qty(t)
            if qty is None or qty <= 0:
                continue
            t["_qty"] = qty
            await self._execute_single_order(t)
            t.pop("_qty", None)

    # ------------------------------------------------------------------
    async def _calc_qty(self, t: Dict) -> float:
        """
        Расчёт количества:
        - для is_closing: берём comul_qty из position_data
        - для is_opening / is_avg: считаем по margin_size + leverage
        """
        status = t["status"]
        user = t["user_name"]
        symbol = t["symbol"]
        session = t["client_session"]
        debug = t["debug_label"]

        if status == "is_closing":
            return t["position_data"].get("comul_qty", 0.0)

        cfg = self.context.total_settings[user]["symbols_risk"]
        key = symbol if symbol in cfg else "ANY_COINS"

        leverage = cfg[key].get("leverage", 1)
        margin = cfg[key].get("margin_size", 0.0)

        price = None
        for _ in range(5):
            price = await self.get_cur_price(
                session=session,
                ws_price_data=self.context.ws_price_data,
                symbol=symbol,
                get_hot_price=self.get_hot_price
            )
            if price:
                break
            await asyncio.sleep(0.2)

        if not price:
            self.info_handler.debug_error_notes(f"[{debug}] failed to get price for qty calc")
            return 0.0

        return self.pos_utils.size_calc(
            margin_size=margin,
            entry_price=price,
            leverage=leverage,
            volume_rate=t["position_data"].get("process_volume"),
            precision=t["qty_precision"],
            dubug_label=debug
        )

    # ------------------------------------------------------------------
    async def _market_order(
        self,
        binance: BinancePrivateApi,
        session,
        strategy: str,
        symbol: str,
        side_pos: str,
        order_side: str,
        qty: float,
        debug: str
    ):
        """
        Обёртка вокруг binance.make_order + валидация.
        """
        try:
            response = await binance.make_order(
                session=session,
                strategy_name=strategy,
                symbol=symbol,
                qty=qty,
                side=order_side,
                position_side=side_pos,
                market_type="MARKET"
            )
        except Exception as e:
            self.info_handler.debug_error_notes(f"[ERROR]{debug} market error: {e}", True)
            return False, None

        success, parsed = self.risk_set.validate.validate_market_response(response[0], debug)
        return success, parsed

    # ------------------------------------------------------------------
    # async def _execute_single_order(self, task: Dict):

    #     user = task["user_name"]
    #     symbol = task["symbol"]
    #     strategy = task["strategy_name"]
    #     position_side = task["position_side"]   # LONG / SHORT
    #     status = task["status"]                 # is_opening / is_avg / is_closing
    #     debug = task["debug_label"]

    #     session = task["client_session"]
    #     binance: BinancePrivateApi = task["binance_client"]

    #     qty = task["_qty"]

    #     # ---- доп. защита: сверяемся с текущим состоянием позиции ----
    #     pos = (
    #         self.context.position_vars
    #             .get(user, {})
    #             .get(strategy, {})
    #             .get(symbol, {})
    #             .get(position_side, {})
    #     )

    #     in_position = pos.get("in_position", False)

    #     if status == "is_closing" and not in_position:
    #         # Нечего закрывать
    #         return

    #     if status == "is_opening" and in_position:
    #         # Уже в позиции — не открываем повторно
    #         return

    #     if status == "is_avg" and not in_position:
    #         # Усреднять нечего
    #         return

    #     prev_avg_price = pos.get("avg_price") if pos else None

    #     order_side = self._market_side(status, position_side)

    #     # ------------------------------
    #     # MARKET-ОРДЕР
    #     # ------------------------------
    #     ok, _ = await self._market_order(
    #         binance, session, strategy, symbol, position_side, order_side, qty, debug
    #     )
    #     if not ok:
    #         return

    #     # ------------------------------
    #     # ЛОГИКА ПОСЛЕ MARKET
    #     # ------------------------------

    #     # 1) Закрытие: просто отменяем риск-ордера и выходим
    #     if status == "is_closing":
    #         await self._cancel_risk_orders(binance, session, task)
    #         return

    #     # 2) Усреднение: сначала отменяем старые риск-ордера
    #     if status == "is_avg":
    #         await self._cancel_risk_orders(binance, session, task)

    #     # 3) Для открытия и усреднения — ждём обновления позиции,
    #     #    а потом ставим TP/SL
    #     updated_pos = await self._wait_for_position_update(
    #         user_name=user,
    #         strategy_name=strategy,
    #         symbol=symbol,
    #         position_side=position_side,
    #         prev_avg_price=prev_avg_price,
    #         debug_label=debug
    #     )
    #     if not updated_pos:
    #         # если контекст не обновился — лучше не ставить TP/SL,
    #         # чтобы не насрать кривыми ордерами
    #         return

    #     await self._place_risk_orders(binance, session, task)

    async def _execute_single_order(self, task: Dict):

        user = task["user_name"]
        symbol = task["symbol"]
        strategy = task["strategy_name"]
        position_side = task["position_side"]   # LONG / SHORT
        status = task["status"]                 # is_opening / is_avg / is_closing
        debug = task["debug_label"]

        session = task["client_session"]
        binance: BinancePrivateApi = task["binance_client"]

        qty = task["_qty"]

        pos = (
            self.context.position_vars
                .get(user, {})
                .get(strategy, {})
                .get(symbol, {})
                .get(position_side, {})
        )

        in_position = pos.get("in_position", False)

        # --- Защитные проверки ---
        if status == "is_closing" and not in_position:
            return
        if status == "is_opening" and in_position:
            return
        if status == "is_avg" and not in_position:
            return

        prev_avg_price = pos.get("avg_price") if pos else None

        # ==========================
        # 1️⃣ Установка leverage и margin без промедления
        # ==========================
        cfg = self.context.total_settings[user]["symbols_risk"]
        key = symbol if symbol in cfg else "ANY_COINS"

        leverage = cfg[key].get("leverage", 1)
        margin_type = (
            self.context.total_settings[user]
            .get("core", {})
            .get("margin_type", "CROSSED")
        )

        try:
            if status in ("is_opening", "is_avg"):
                await binance.set_margin_type(
                    session=session,
                    strategy_name=strategy,
                    symbol=symbol,
                    margin_type=margin_type
                )
                await binance.set_leverage(
                    session=session,
                    strategy_name=strategy,
                    symbol=symbol,
                    lev_size=int(leverage)
                )
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[LEVER/MARGIN ERROR]{debug} → {e}",
                is_print=True
            )

        # ==========================
        # 2️⃣ MARKET ORDER
        # ==========================
        order_side = self._market_side(status, position_side)

        ok, _ = await self._market_order(
            binance, session, strategy, symbol, position_side, order_side, qty, debug
        )
        if not ok:
            return

        # ==========================
        # 3️⃣ Реакция на тип операции
        # ==========================

        # Закрытие — сразу отменяем TP/SL и уходим
        if status == "is_closing":
            await self._cancel_risk_orders(binance, session, task)
            return

        # Усреднение — отменяем старые TP/SL
        if status == "is_avg":
            await self._cancel_risk_orders(binance, session, task)

        # Ждём обновления позиции
        updated_pos = await self._wait_for_position_update(
            user_name=user,
            strategy_name=strategy,
            symbol=symbol,
            position_side=position_side,
            prev_avg_price=prev_avg_price,
            debug_label=debug
        )
        if not updated_pos:
            return

        # Ставим TP/SL если есть в конфиге
        await self._place_risk_orders(binance, session, task)

    # ------------------------------------------------------------------
    async def _cancel_risk_orders(self, binance: BinancePrivateApi, session, task: Dict):

        user = task["user_name"]
        symbol = task["symbol"]
        side = task["position_side"]
        strategy = task["strategy_name"]

        await self.risk_set.cancel_orders_for_side(
            session=session,
            user_name=user,
            strategy_name=strategy,
            symbol=symbol,
            position_side=side,
            binance_client=binance
        )

    # ------------------------------------------------------------------
    async def _place_risk_orders(self, binance: BinancePrivateApi, session, task: Dict):

        user = task["user_name"]
        symbol = task["symbol"]
        side = task["position_side"]
        strategy = task["strategy_name"]

        cfg = self.context.total_settings[user]["symbols_risk"]
        key = symbol if symbol in cfg else "ANY_COINS"

        suffixes: List[str] = []
        if cfg[key].get("sl"):
            suffixes.append("sl")
        if cfg[key].get("tp"):
            suffixes.append("tp")

        if not suffixes:
            return

        await self.risk_set.place_all_risk_orders(
            session=session,
            user_name=user,
            strategy_name=strategy,
            symbol=symbol,
            position_side=side,
            risk_suffix_list=suffixes,
            place_risk_order=binance.place_risk_order
        )