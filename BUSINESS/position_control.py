from dataclasses import dataclass
import aiohttp
import asyncio
import time
# import math
# from pprint import pprint
from typing import *
from collections.abc import Awaitable
from b_context import BotContext
from c_log import ErrorHandler
from c_utils import format_duration
from d_bapi import BinancePrivateApi
from c_validators import OrderValidator 
from MANAGERS.online import NetworkManager


POS_UPDATE_FREQUENCY: float = 1.0         # seconds. частота обновления позиций при контроле состояния позиций

Side = Literal["LONG", "SHORT"]

@dataclass
class GridStep:
    indent: float   # в процентах (может быть 0, -8, -16 и т.д.)
    volume: float   # "объём" шага (в процентах от общей маржи ИЛИ просто относительные веса)

class GridMath:
    """
    Чистая математика для грида:
    - оценка реального прогресса по notional
    - реконструкция исходного entry_price по avg_price Binance
    """

    def __init__(
        self,
        margin_size: float,
        leverage: float,
        grid_orders: List[dict]
    ):
        """
        margin_size  – твой "общий банк" на сделку в USDT
        leverage     – плечо
        grid_orders  – как в настройках:
            [{'indent': 0.0, 'volume': 10.52, ...}, ...]
        """
        self.margin_size = float(margin_size)
        self.leverage = float(leverage)
        self.steps: List[GridStep] = [
            GridStep(indent=float(g["indent"]), volume=float(g["volume"]))
            for g in grid_orders
        ]

        # базовый полный "банк * плечо"
        self.base_notional = self.margin_size * self.leverage

        # доли (volume%) в виде коэф. [0..1] относительно base_notional
        self._shares = [s.volume / 100.0 for s in self.steps]

        # notional на каждом шаге, если он отработал
        self.step_notional: List[float] = [
            self.base_notional * share for share in self._shares
        ]

        # кумулятивный notional по прогрессу 1..N
        self.cum_notional: List[float] = []
        acc = 0.0
        for n in self.step_notional:
            acc += n
            self.cum_notional.append(acc)

    # -------------------------------------------------------------
    def estimate_progress(self, actual_notional: float) -> int:
        """
        Оценивает реальный progress (сколько шагов грида отработало),
        исходя из фактического notional (из Binance positions).

        Простой и рабочий вариант:
        - для p = 1..N считаем ожидаемый notional(p)
        - выбираем p с минимальным |expected_notional(p) - actual_notional|
        """
        if actual_notional <= 0 or not self.cum_notional:
            return 1

        best_p = 1
        best_diff = float("inf")

        for i, expected in enumerate(self.cum_notional, start=1):
            diff = abs(expected - actual_notional)
            if diff < best_diff:
                best_diff = diff
                best_p = i

        return best_p

    # -------------------------------------------------------------
    @staticmethod
    def reconstruct_entry_price(
        avg_price: float,
        grid_orders: List[dict],
        progress: int,
        side: Side
    ) -> Optional[float]:

        if avg_price <= 0 or progress <= 0:
            return None

        used = grid_orders[:min(progress, len(grid_orders))]
        vols = [float(step["volume"]) for step in used]
        sum_vols = sum(vols)

        if sum_vols <= 0:
            return None

        num = 0.0

        for step, v in zip(used, vols):
            indent = float(step["indent"])

            if side == "LONG":
                k = 1.0 + indent / 100.0
            elif side == "SHORT":
                k = 1.0 - indent / 100.0
            else:
                return None

            if k <= 0:
                return None

            num += v / k

        entry0 = avg_price * (num / sum_vols)
        return entry0


class PositionCleaner():
    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        set_pos_defaults: Callable,
        preform_message: Callable,    
    ):
        info_handler.wrap_foreign_methods(self)
        self.context = context
        self.info_handler = info_handler
        self.set_pos_defaults = set_pos_defaults
        self.preform_message = preform_message
        self.validate = OrderValidator(info_handler=info_handler)

    async def pnl_report(
            self,
            user_name: str,
            strategy_name: str,
            symbol: str,
            pos_side: str,
            get_realized_pnl: Callable
        ):
        """
        Отчет по реализованному PnL для Binance (через API),
        без использования текущей цены.
        """
        debug_label = f"{user_name}_{symbol}_{pos_side}"
        cur_time = int(time.time() * 1000)
        pos_data = (
            self.context.position_vars
            .get(user_name, {})
            .get(strategy_name, {})
            .get(symbol, {})
            .get(pos_side, {})
        )

        start_time = pos_data.get("c_time")  # время открытия позиции
        notional = pos_data.get("notional")
        pnl_usdt, commission = 0.0, 0.0

        try:
            pnl_usdt, commission = await get_realized_pnl(
                symbol=symbol,
                direction=pos_side.upper(),
                start_time=start_time,
                end_time=cur_time
            )
            # print(pnl_usdt, commission)
        except:
            self.info_handler.debug_error_notes(f"[{debug_label}]: ошибка при расчете пнл.")
            return

        if pnl_usdt is None:
            self.info_handler.debug_error_notes(f"[{debug_label}]: ошибка при расчете pnl_usdt.")
            return

        pnl_pct = (pnl_usdt / notional) * 100
        time_in_deal = cur_time - start_time if start_time else None

        body = {
            "user_name": user_name,
            "symbol": symbol,
            "pos_side": pos_side,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "commission": commission,
            "cur_time": cur_time,
            "time_in_deal": format_duration(time_in_deal)
        }

        self.preform_message(
            marker="report",
            body=body,
            is_print=True
        )

        return pnl_usdt

    async def close_position_cleanup(
            self,
            session,
            user_name,
            strategy_name,
            symbol,
            position_side,
            cancel_all_risk_orders: Callable,
            get_realized_pnl: Callable
        ):

        try:
            await self.pnl_report(
                user_name=user_name,
                strategy_name=strategy_name,
                symbol=symbol,
                pos_side=position_side,
                get_realized_pnl=get_realized_pnl
            )

        finally:
            try:
                # 🚫 Отменяем TP и SL
                await cancel_all_risk_orders(
                        session,
                        symbol,
                        position_side
                    )
            finally:            
                # ♻️ Переинициализация текущей позиции
                # async with self.context.pos_lock:   
                symbol_data = self.context.position_vars[user_name][strategy_name].setdefault(symbol, {})
                self.set_pos_defaults(symbol_data, symbol, position_side, update_flag=True)


class Sync(PositionCleaner):
    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,        
        set_pos_defaults: Callable, 
        preform_message: Callable
    ):        
        super().__init__(context, info_handler, set_pos_defaults, preform_message)  

    @staticmethod
    def unpack_position_info(position: dict) -> dict:
        if not isinstance(position, dict):
            return {
                "symbol": "",
                "side": "",
                "amount": 0.0,
                "entry_price": 0.0,
                "notional": 0.0,        # добавил
                "leverage": 0.0,        # добавил
                "margin": 0.0           # добавил
            }

        return {
            "symbol": position.get("symbol", "").upper(),
            "side": position.get("positionSide", "").upper(),
            "amount": abs(float(position.get("positionAmt", 0.0))),
            "entry_price": float(position.get("entryPrice", 0.0)),
            "notional": abs(float(position.get("notional", 0.0))),          # USDT размер позиции
            "leverage": float(position.get("leverage", 0.0)),          # плечо
            "margin": float(position.get("isolatedMargin", 0.0))       # маржа (если isolated)
        }  
    
    async def _handle_partial_close(
        self,
        session,
        strategy_name,
        symbol,
        position_side,
        symbol_data,
        make_order,
        debug_label
    ):
        """
        Допродаём остаток при частичном закрытии позиции.
        """
        qty_left = symbol_data.get("comul_qty", 0.0)
        if qty_left <= 0:
            return

        side = "SELL" if position_side == "LONG" else "BUY"

        market_result = await make_order(
            session=session,
            strategy_name=strategy_name,
            symbol=symbol,
            qty=qty_left,
            side=side,
            position_side=position_side,
            market_type="MARKET"
        )

        if not market_result or not isinstance(market_result, (list, tuple)):
            symbol_data["problem_closed"] = True
            self.info_handler.debug_info_notes(
                f"[INFO][{debug_label}] partial close FAILED.",
                is_print=True
            )
            return

        success, _ = self.validate.validate_market_response(
            market_result[0], debug_label
        )

        if not success:
            symbol_data["problem_closed"] = True
            self.info_handler.debug_info_notes(
                f"[INFO][{debug_label}] partial close FAILED.",
                is_print=True
            )

    async def _handle_full_close(
        self,
        session,
        user_name,
        strategy_name,
        symbol,
        position_side,
        cancel_all_risk_orders,
        get_realized_pnl
    ):
        """
        Полное закрытие → отчёт → отмена риск ордеров → reset позиции
        """
        await self.close_position_cleanup(
            session=session,
            user_name=user_name,
            strategy_name=strategy_name,
            symbol=symbol,
            position_side=position_side,
            cancel_all_risk_orders=cancel_all_risk_orders,
            get_realized_pnl=get_realized_pnl
        )    

    async def update_positions(
        self,
        session,
        user_name: str,
        strategy_name: str,
        target_symbols: Set[str],
        positions: List[Dict],
        cancel_all_risk_orders: Callable,
        get_realized_pnl: Callable,
        make_order: Callable
    ) -> None:
        """
        Обновляет данные о позициях для указанной стратегии и символов.
        Логика разделена на:
        1) классификацию состояния
        2) обновление локального кэша
        3) обработку частичных и полных закрытий
        """

        strategy_positions = self.context.position_vars[user_name][strategy_name]

        try:
            # === 1. Отфильтровать позиции под эту стратегию ===
            relevant = [
                pos for pos in positions
                if pos and pos.get("symbol", "").upper() in target_symbols
            ]

            for pos in relevant:
                info = self.unpack_position_info(pos)
                symbol      = info["symbol"]
                side        = info["side"]       # LONG/SHORT
                amount      = info["amount"]     # текущая позиция по бирже
                binance_price = info["entry_price"]
                notional    = info["notional"]

                symbol_data = strategy_positions.get(symbol, {}).get(side)
                debug = f"{user_name}_{strategy_name}_{symbol}_{side}"

                if not symbol_data:
                    self.info_handler.debug_info_notes(f"[SKIP] No local data for {debug}")
                    continue

                # === 2. Классифицируем состояние ===

                old_amt = symbol_data.get("comul_qty", 0.0)
                in_position_now = amount > 0
                was_in_position = symbol_data.get("in_position", False)

                is_new_position = in_position_now and not was_in_position
                is_partial_close = (
                    in_position_now and
                    was_in_position and
                    amount < old_amt / 2 and
                    old_amt > 0
                )
                is_full_close = (not in_position_now) and was_in_position

                # === Обновляем прогресс позиции ===
                strategy_cfg = self.context.strategy_notes.get(strategy_name, {})
                side_cfg = strategy_cfg.get(side, {})
                grid_orders = side_cfg.get("entry_conditions", {}).get("grid_orders", [])

                symbols_risk = self.context.total_settings[user_name]["symbols_risk"]
                key = symbol if symbol in symbols_risk else "ANY_COINS"
                margin_size = symbols_risk[key].get("margin_size", 0.0)
                leverage    = symbols_risk[key].get("leverage", 1.0)

                reconstructed_entry = None

                if notional and margin_size > 0 and grid_orders:
                    grid_math = GridMath(margin_size, leverage, grid_orders)
                    real_progress = grid_math.estimate_progress(notional)

                    # print(f"[SYNC][{debug}] real_progress = {real_progress}")

                    # async with self.context.pos_lock:
                    symbol_data["avg_progress_real"] = real_progress
                    if real_progress > symbol_data.get("avg_progress_counter", 1):
                        symbol_data["avg_progress_counter"] = real_progress

                    if real_progress > 1 and (user_name not in self.context.first_update_done or not self.context.first_update_done[user_name]):
                        # при первом обновлении синхронизируем entry_price
                        reconstructed_entry = GridMath.reconstruct_entry_price(
                            avg_price=binance_price,
                            grid_orders=grid_orders,
                            progress=real_progress,
                            side=side
                        )
                        # print(f"[SYNC][{debug}] reconstructed_entry = {reconstructed_entry}")

                # === 4. Обновляем локальный кэш позиций ===
                if is_new_position:
                    if reconstructed_entry:
                        corrected_entry_price = reconstructed_entry
                    else:
                        corrected_entry_price = binance_price
                    # первое появление позиции
                    # async with self.context.pos_lock:
                    symbol_data.update({
                        "in_position": True,
                        "comul_qty": amount,
                        "notional": notional,
                        "entry_price": corrected_entry_price,
                        "avg_price": binance_price,
                        "c_time": int(time.time() * 1000),
                    })

                elif in_position_now:
                    # обычное обновление позиции
                    # НЕ пересоздаём entry_price и c_time
                    # async with self.context.pos_lock:
                    symbol_data.update({
                        "in_position": True,
                        "comul_qty": amount,
                        "avg_price": binance_price,   # Binance avg price
                        "notional": notional,
                    })

                # === 4. Обработка закрытий ===
                if is_partial_close:
                    print(f"[SYNC][{debug}] PARTIAL CLOSE detected.")
                    await self._handle_partial_close(
                        session=session,
                        strategy_name=strategy_name,
                        symbol=symbol,
                        position_side=side,
                        symbol_data=symbol_data,
                        make_order=make_order,
                        debug_label=debug
                    )

                if is_full_close:
                    print(f"[SYNC][{debug}] FULL CLOSE detected.")
                    # async with self.context.pos_lock:
                    await self._handle_full_close(
                        session=session,
                        user_name=user_name,
                        strategy_name=strategy_name,
                        symbol=symbol,
                        position_side=side,
                        cancel_all_risk_orders=cancel_all_risk_orders,
                        get_realized_pnl=get_realized_pnl
                    )           

        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[{user_name}][{strategy_name}] update_positions failed: {e}"
            )  
        finally: 
            # отмечаем завершение первого обновления  
            self.context.first_update_done[user_name] = True   

    async def refresh_positions_state(
        self,
        session: aiohttp.ClientSession,
        user_name: str,
        fetch_positions: Callable[[aiohttp.ClientSession], Awaitable[Dict]],
        cancel_all_risk_orders: Callable,
        get_realized_pnl: Callable,
        make_order: Callable
    ) -> None:
        """
        Обновляет состояние позиций для всех стратегий последовательно.
        """
        # print("refresh_positions_state1")
        debug_label = f"[{user_name}]"        
        try:
            positions = await fetch_positions(session)
            positions = positions.get("positions", [])      
            # print(positions)   
            # 
            # pprint(positions)
            if not positions:
                return     
                   
            async with self.context.pos_lock:
                # Параллельная обработка всех стратегий пользователя
                await asyncio.gather(*[
                    self.update_positions(
                        session,
                        user_name,
                        strategy_name,
                        strategy_details.get("symbols", set()),
                        positions,
                        cancel_all_risk_orders,
                        get_realized_pnl,
                        make_order
                    )
                    for strategy_name, strategy_details in self.context.total_settings[user_name].get("strategies_symbols", {}).items()
                ])

        except aiohttp.ClientError as e:
            self.info_handler.debug_error_notes(f"{debug_label}[HTTP Error] Failed to fetch positions: {e}. ")
            raise
        except Exception as e:
            self.info_handler.debug_error_notes(f"{debug_label}[Unexpected Error] Failed to refresh positions: {e}. ")
            raise      

    async def _sync_user_positions(self, user_name: str):
        # print("sync_pos_all_users1")
        connector: NetworkManager = self.context.user_contexts[user_name]["connector"]
        session: aiohttp.ClientSession = connector.session
        binance_client: BinancePrivateApi = self.context.user_contexts[user_name]["binance_client"]     

        if not session or not binance_client:
            print(f"[SYNC][{user_name}] No session or Binance client.")
            return  

        await self.refresh_positions_state(
            session=session,
            user_name=user_name,
            fetch_positions=binance_client.fetch_positions,
            cancel_all_risk_orders=binance_client.cancel_orders_by_symbol_side,
            get_realized_pnl=binance_client.get_realized_pnl,
            make_order=binance_client.make_order
        )   

    # ------------------------------
    # POS LOOP
    # ------------------------------
    async def run_positions_sync_loop(self):
        cycle_time = time.monotonic()

        while not self.context.stop_bot:
            now = time.monotonic()
            if now - cycle_time >= POS_UPDATE_FREQUENCY:

                for user_name in list(self.context.total_settings.keys()):
                    try:
                        await self._sync_user_positions(user_name)
                    except Exception as e:
                        print(f"[SYNC][{user_name}] ERROR: {e}")

                    # микропаузa: анти-flood Binance
                    await asyncio.sleep(0)

                cycle_time = now

            await asyncio.sleep(0.25)


    # async def positions_flow_manager(self):
    #     """Цикл обновления позиций и синхронизации кэша"""

    #     all_users = list(self.context.total_settings.keys())    

    #     while not self.context.stop_bot:            
    #         try:
    #             await asyncio.gather(*[self._sync_user_positions(user_name) for user_name in all_users])
    #         except Exception as e:
    #             print(f"[SYNC][ERROR] refresh_positions_state: {e}")
    #         finally:
    #             await asyncio.sleep(POS_UPDATE_FREQUENCY)