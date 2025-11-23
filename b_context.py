import asyncio
from typing import *
from copy import deepcopy
from a_settings import *
from c_log import ErrorHandler
from c_utils import PositionUtils, get_proxy_list
from c_validators import validate_symbol


class BotContext:
    def __init__(self):
        """ Инициализируем глобальные структуры"""

        # Переменные состояния бота
        self.first_iter: bool = True
        self.stop_bot: bool = False

        # Статическая информация
        self.symbol_info: dict = {}
        self.fetch_symbols: Set[str] = set()

        # Настройки и текущие данные
        self.strategy_notes: dict = {}
        self.total_settings: dict = {}  
        self.user_contexts: dict = {}
        self.api_key_list: list = []

        # Переменные позиции
        self.first_update_done: dict[str, bool] = {}
        self.position_vars: dict = {}
        self.ws_price_data: Dict[str, Dict[str, float]] = {}    
        self.report_list = []

        # Ссылки на глобальные объекты
        self.pos_lock: asyncio.Lock = asyncio.Lock()
        self.ws_async_lock: asyncio.Lock = asyncio.Lock()


class BaseDataInitializer:
    def __init__(
            self,
            context: BotContext, 
            info_handler: ErrorHandler, 
            pos_utils: PositionUtils
        ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.context = context
        self.pos_utils = pos_utils

    def init_base_structure(self):
        users_data: dict = deepcopy(UsersSettings().users_config)

        # Отфильтруем сразу активные стратегии у всех пользователей
        active_strategy_names: set = set()
        for user_data in users_data.values():
            for strategy_name, strategy_cfg in user_data.get("strategies_symbols", []):
                if strategy_cfg.get("enable"):
                    active_strategy_names.add(strategy_name)

        # Отфильтруем strategy_notes — оставим только активные
        all_strategy_notes: list = [
            (name, cfg) for name, cfg in StrategySettings().strategy_notes
            if name in active_strategy_names
        ]

        # print(all_strategy_notes)

        self._load_user_data(users_data)
        if self.context.stop_bot:
            return

        self._validate_strategy_notes(all_strategy_notes)
        if self.context.stop_bot:
            return
        
        self._get_strategy_notes(all_strategy_notes)

    def _get_strategy_notes(self, all_strategy_notes: list):
        self.context.strategy_notes = dict(all_strategy_notes)

    def _has_duplicate_keys(self, pair_list: list, source_name: str, user: str = "") -> bool:
        keys_only = [k[0] for k in pair_list]
        duplicates = set(k for k in keys_only if keys_only.count(k) > 1)
        if duplicates:
            prefix = f"У пользователя '{user}' " if user else ""
            print(f"❌ {prefix}обнаружены дубликаты в '{source_name}': {duplicates}")
            self.context.stop_bot = True
            return True
        return False

    def _validate_strategy_notes(self, all_strategy_notes):
        strategy_keys = [k[0] for k in all_strategy_notes if k]
        if self._has_duplicate_keys(all_strategy_notes, source_name="StrategySettings().strategy_notes"):
            raise

        for user_data in self.context.total_settings.values():
            for strategy_name in user_data.get("strategies_symbols", {}).keys():
                if strategy_name not in strategy_keys:
                    print(f"❌ Неизвестная стратегия '{strategy_name}' не найдена в StrategySettings().strategy_notes")
                    self.context.stop_bot = True
                    raise

        self._avi_strategies = strategy_keys  # сохранить для `_compute_historical_limits`

    def _load_user_data(self, users_data):
        """
        Загружает данные пользователей, формирует:
        - strategies_symbols
        - symbols_risk
        - core
        - proxies (список)
        - сохраняет всё в context.total_settings
        """

        for user, user_data in users_data.items():

            # ---------- 1. Активные стратегии ----------
            raw_config = user_data.get("strategies_symbols", [])
            raw_config = [k for k in raw_config if k and k[1].get("enable")]

            if self._has_duplicate_keys(raw_config, source_name="strategies_symbols", user=user):
                return

            # ---------- 2. Quote asset ----------
            quote_asset = (
                user_data.get("core", {})
                .get("quote_asset", "USDT")
                .strip() or "USDT"
            )

            # ---------- 3. User risk ----------
            user_defined_risk = deepcopy(user_data.get("symbols_risk", {}))
            strategies_symbols = {}
            user_symbol_risk = {}

            # ---------- 4. Формируем symbols & risk со стратегий ----------
            for strategy_name, strat_cfg in raw_config:
                debug_label = f"[{user}][{strategy_name}]"

                raw_symbols = strat_cfg.get("symbols", set())
                symbols_with_suffix = set()

                for symbol in raw_symbols:
                    if not symbol or not symbol.strip():
                        continue

                    base = symbol.strip()

                    if not validate_symbol(base):
                        self.info_handler.debug_error_notes(
                            f"⚠️ {debug_label}: символ '{symbol}' пуст или повреждён."
                        )
                        raise RuntimeError("Symbol validate error")

                    full_symbol = base + quote_asset
                    symbols_with_suffix.add(full_symbol)

                    # риск по конкретному символу
                    if base in user_defined_risk:
                        user_symbol_risk[full_symbol] = user_defined_risk[base]

                # записываем обработанные символы в стратегию
                strat_cfg["symbols"] = symbols_with_suffix

                # добавляем в глобальный список торговли
                self.context.fetch_symbols.update(symbols_with_suffix)

                # в итоговую структуру
                strategies_symbols[strategy_name] = strat_cfg
                del strategies_symbols[strategy_name]["enable"]

            # если для юзера нет активных стратегий
            if not strategies_symbols:
                print(f"⚠️ У пользователя '{user}' нет активных стратегий — пропускаем.")
                continue

            # ---------- 5. ANY_COINS ----------
            if "ANY_COINS" in user_defined_risk:
                user_symbol_risk["ANY_COINS"] = user_defined_risk["ANY_COINS"]

            # ---------- 6. MULTI-PROXY ДЛЯ ЮЗЕРА ----------
            # формат: user_data["proxies"] = [ {enable:True, ...}, None, ... ]
            raw_proxy_list = user_data.get("proxies", [])

            # конвертируем dict → http://login:pass@ip:port
            proxy_list = get_proxy_list(raw_proxy_list)

            # если нет ни одной записи
            if not proxy_list:
                proxy_list = [None]

            # ---------- 7. CORE ----------
            core = user_data.get("core", {}).copy()

            if "direction" in core:
                core["direction"] = self.pos_utils.get_avi_directions(
                    core["direction"],
                    user
                )

            # ---------- 8. ФИНАЛЬНАЯ ЗАПИСЬ В CONTEXT ----------
            self.context.total_settings[user] = {
                "keys": user_data.get("keys", {}),             # API ключи
                "core": core,                                  # настройки торговли
                "strategies_symbols": strategies_symbols,      # стратегии с символами
                "symbols_risk": user_symbol_risk,              # риск-таблица
                # 🔥 новый мульти-прокси список
                "proxies": proxy_list,
            }

        # ---------- 9. Final check ----------
        if not self.context.total_settings:
            print("❌ Нет подходящих пользователей с активными стратегиями.")
            self.context.stop_bot = True


class PositionVarsSetup:
    def __init__(
            self,
            context: BotContext, 
            info_handler: ErrorHandler, 
            pos_utils: PositionUtils
        ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.context = context
        self.pos_utils = pos_utils
    
    @staticmethod
    def pos_vars_root_template() -> dict:
        """Базовый шаблон переменных позиции"""
        return {
            "avg_progress_counter": 1,   # бумажный progress
            "avg_progress_real": 1,      # фактический progress

            "avg_price": None,
            "entry_price": None,
            "comul_qty": None,
            "notional": None,
            "in_position": False,
            "problem_closed": False,

            "process_volume": 0.0,
            "is_tp": False,
            "is_sl": False,
            "c_time": None
        }

        
    def set_pos_defaults(self, symbol_data, symbol, pos_side, update_flag: bool = False) -> bool:
        """
        Безопасная инициализация структуры данных контроля позиций.
        Теперь включает расчёт initial_notional.
        """

        if not update_flag:
            qty_prec, price_prec = None, None
            try:
                precisions = self.pos_utils.get_qty_precisions(self.context.symbol_info, symbol)
                if isinstance(precisions, (list, tuple)) and len(precisions) >= 2:
                    qty_prec, price_prec = precisions[0], precisions[1]
                else:
                    self.info_handler.debug_error_notes(f"⚠️ [INFO]: Не удается получить precisions для {symbol}")
            except Exception as e:
                self.info_handler.debug_error_notes(f"⚠️ [ERROR] при получении precisions для {symbol}: {e}")
                self.context.stop_bot = True
                raise RuntimeError(f"Ошибка получения precision для {symbol}: {e}")

            if qty_prec is None or price_prec is None:
                print(f"❌ Не удалось определить qty/price precision для {symbol}")
                return False

            symbol_data.setdefault("qty_precision", qty_prec)
            symbol_data.setdefault("price_precision", price_prec)

        # ==========================================================
        # 3. Создаём pos_side ветку с новым initial_notional
        # ==========================================================
        root = self.pos_vars_root_template()

        symbol_data.setdefault(pos_side, {}).update(root)

        return True

    def setup_pos_vars(self):
        """Инициализация структуры данных контроля позиций"""
        bad_symbols = set()
        for user_name, details in self.context.total_settings.items():
            dubug_label = f"[{user_name}]"

            if user_name not in self.context.position_vars:
                self.context.position_vars[user_name] = {}

            for strategy_name, strategy_details in details.get("strategies_symbols").items():
                if strategy_name not in self.context.position_vars:
                    self.context.position_vars[user_name][strategy_name] = {}
                
                symbols = strategy_details.get("symbols", set())
                if not symbols:
                    self.info_handler.debug_error_notes(f"⚠️ {dubug_label}: символы пусты. ")
                    raise

                for pos_side in ["LONG", "SHORT"]:
                    for symbol in symbols.copy():
                        symbol_data = self.context.position_vars[user_name][strategy_name].setdefault(symbol, {})
                        if not self.set_pos_defaults(symbol_data, symbol, pos_side):
                            bad_symbols.add(symbol)
                            break

        self.context.fetch_symbols -= bad_symbols