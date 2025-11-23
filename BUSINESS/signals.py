from typing import *
from b_context import BotContext
from c_log import ErrorHandler
from c_validators import TimeframeValidator
import traceback


class SIGNALS:
    def __init__(
            self,
            context: BotContext, 
            info_handler: ErrorHandler, 
            tfr_valid: TimeframeValidator,
        ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.context = context
        self.tfr_valid = tfr_valid    

    def signals_debug(self, msg, symbol=None):
        self.info_handler.debug_info_notes(f"{msg} (Symbol: {symbol})" if symbol else msg, True)
    
    def compose_signals(
            self, user_name, strategy_name, symbol,
            position_side, status, client_session, binance_client):
        debug_label = f"[{user_name}][{strategy_name}][{symbol}][{position_side}]"
        symbol_data = self.context.position_vars[user_name][strategy_name][symbol]
        return {
            "status": status,
            "user_name": user_name,
            "strategy_name": strategy_name,
            "symbol": symbol,
            "position_side": position_side,
            "pos_side": position_side,
            "position_data": symbol_data[position_side],
            "qty_precision": symbol_data.get("qty_precision"),
            "debug_label": debug_label,  
            "client_session": client_session,
            "binance_client": binance_client,                  
        }
    
    def cron_colab(self, entry_rules):
        """Генерация сигналов. """
        _, is_closed = self.tfr_valid.tfr_validate(entry_rules)
        if not is_closed:
            return 0, 0

        return 1, -1
    
    # ////
    def signal_interpreter(
        self,
        long_signal: int,
        short_signal: int,
        in_position: bool,
        position_side: str,
        long_count: int,
        short_count: int,
        long_limit: int = float("inf"),
        short_limit: int = float("inf")
    ) -> tuple[bool, bool]:
        
        is_long = position_side == "LONG"
        is_short = position_side == "SHORT"

        open_signal = not in_position and (
            (long_signal == 1 and is_long) or 
            (short_signal == -1 and is_short)
        )

        if open_signal:
            if is_long and long_count >= long_limit:
                open_signal = False
            elif is_short and short_count >= short_limit:
                open_signal = False

        return open_signal
    
    def get_signal(
            self,  
            user_name: str,
            strategy_name: str,          
            symbol: str,
            position_side: str,
            long_count: dict,
            short_count: dict
        ):

        open_signal = False

        try:
            # --- Сокращения ---
            user_settings = self.context.total_settings[user_name]["core"]
            strategy_settings = self.context.strategy_notes[strategy_name][position_side]
            entry_conditions = strategy_settings.get("entry_conditions", {})

            symbol_vars = self.context.position_vars[user_name][strategy_name][symbol]

            symbol_pos_data = symbol_vars[position_side]
            in_position = symbol_pos_data.get("in_position", False)

            # --- Вычисляем сигнал ---
            result = self.cron_colab(entry_rules=entry_conditions.get("rules", {}))
            if isinstance(result, (tuple, list)) and len(result) == 2:
                long_signal, short_signal = result
                open_signal = self.signal_interpreter(
                    long_signal,
                    short_signal,
                    in_position,
                    position_side,
                    long_count[user_name],
                    short_count[user_name],
                    user_settings.get("long_positions_limit", float("inf")),
                    user_settings.get("short_positions_limit", float("inf")),
                )

        except Exception as e:
            tb = traceback.format_exc()
            self.signals_debug(
                f"❌ Signal function error for [{user_name}][{strategy_name}][{symbol}][{position_side}]: {e}\n{tb}",
                symbol
            )
        finally:
            if open_signal:
                if position_side == "LONG":
                    long_count[user_name] += 1
                elif position_side == "SHORT":
                    short_count[user_name] += 1

            return open_signal
