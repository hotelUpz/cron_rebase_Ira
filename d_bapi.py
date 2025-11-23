import aiohttp
import time
import hmac
import hashlib
import asyncio
import inspect
import random
from typing import *
from c_log import ErrorHandler, log_time
from c_validators import HTTP_Validator
# from pytz.tzinfo import BaseTzInfo


class BinancePublicApi:
    def __init__(self, info_handler: ErrorHandler):    
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

        self.exchangeInfo_url = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
        self.klines_url = 'https://fapi.binance.com/fapi/v1/klines'    
        self.price_url = "https://fapi.binance.com/fapi/v1/ticker/price"
    
    # publis methods:    
    async def get_exchange_info(self, session: aiohttp.ClientSession):
        params = {'recvWindow': 20000}
        try:    
            async with session.get(self.exchangeInfo_url, params=params) as response:            
                if response.status != 200:
                    self.info_handler.debug_error_notes(f"Failed to fetch positions: {response.status}")
                return await response.json()  
        except Exception as ex:
            self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

    async def get_hot_price(self, session: aiohttp.ClientSession, symbol: str) -> float | None:
        """Возвращает текущую (горячую) цену по символу с Binance Futures"""
        params = {'symbol': symbol.upper()}
        try:
            async with session.get(self.price_url, params=params) as response:
                if response.status != 200:
                    self.info_handler.debug_error_notes(
                        f"Failed to fetch price for {symbol}: {response.status}"
                    )
                    return None
                data = await response.json()
                return float(data.get("price", 0.0))
        except Exception as ex:
            self.info_handler.debug_error_notes(
                f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}"
            )
            return None


class BinancePrivateApi(HTTP_Validator):
    def __init__(
            self,
            info_handler: ErrorHandler,
            api_key: str = None,
            api_secret: str = None,
            user_label: str = "Nik"
        ) -> None:
        super().__init__(info_handler)

        self.balance_url = 'https://fapi.binance.com/fapi/v2/balance'
        self.create_order_url = self.cancel_order_url = 'https://fapi.binance.com/fapi/v1/order'
        self.cancel_order_symbol_side = 'https://fapi.binance.com/fapi/v1/allOpenOrders'
        self.change_trade_mode = 'https://fapi.binance.com/fapi/v1/positionSide/dual'
        self.set_margin_type_url = 'https://fapi.binance.com/fapi/v1/marginType'
        self.set_leverage_url = 'https://fapi.binance.com/fapi/v1/leverage'        
        self.positions2_url = 'https://fapi.binance.com/fapi/v2/account'       
      

        self.api_key, self.api_secret = api_key, api_secret 
        self.user_label = user_label

    def get_signature(self, params: dict):
        params['timestamp'] = int(time.time() * 1000)
        params_str = '&'.join([f'{k}={v}' for k, v in params.items()])
        signature = hmac.new(bytes(self.api_secret, 'utf-8'), params_str.encode('utf-8'), hashlib.sha256).hexdigest()
        params['signature'] = signature
        return params

    # private methods:   
    async def get_avi_balance(
            self,
            session: aiohttp.ClientSession,
            quote_asset: str
        ) -> float:
        """Получает доступный баланс quote_asset на Binance Futures"""
        headers = {
            "X-MBX-APIKEY": self.api_key
        }

        params = self.get_signature({})  # Подписываем запрос

        async with session.get(self.balance_url, headers=headers, params=params) as response:

            if response.status != 200:
                self.info_handler.debug_error_notes(f"[{self.user_label}][ERROR][get_avi_balance]: {response.status}, {await response.text()}")
                return 0.0
            
            data = await response.json()
            for asset in data:
                if asset["asset"] == quote_asset:
                    return float(asset["availableBalance"])  # Возвращаем доступный баланс quote_asset

        return 0.0  # Если не нашли quote_asset  
        
    async def fetch_positions(self, session: aiohttp.ClientSession):
        params = self.get_signature({'recvWindow': 20000})
        headers = {
            'X-MBX-APIKEY': self.api_key
        }
        async with session.get(self.positions2_url, headers=headers, params=params) as response:
            if response.status != 200:
                self.info_handler.debug_error_notes(f"[{self.user_label}]: Failed to fetch positions: {response.status}, {await response.text()}", True)
            return await response.json()      

    async def get_realized_pnl(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        direction: Optional[str] = None,  # "LONG"/"SHORT"
    ) -> tuple[float, float]:
        """
        Считает реализованный PnL за период по символу (Binance Futures).
        Поддерживает фильтрацию по направлению позиции ("LONG"/"SHORT").
        Делает до 7 реконнектов, пересоздавая сессию на каждой попытке.
        """
        params = {
            "symbol": symbol,
            "recvWindow": 20000
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        headers = {"X-MBX-APIKEY": self.api_key}
        rows = []
        max_retries = 7

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://fapi.binance.com/fapi/v1/userTrades",
                        params=self.get_signature(params),
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            rows = await resp.json()
                            break
                        else:
                            self.info_handler.debug_error_notes(
                                f"[get_realized_pnl][Binance] status={resp.status}, "
                                f"attempt={attempt}/{max_retries}"
                            )
            except Exception as e:
                self.info_handler.debug_error_notes(
                    f"[get_realized_pnl][Binance] {e}, attempt={attempt}/{max_retries}"
                )

            if attempt < max_retries:
                await asyncio.sleep(random.uniform(1, 2))

        if not rows:
            return 0.0, 0.0

        pnl_usdt = 0.0
        commission = 0.0

        for row in rows:
            try:
                ts = int(row.get("time", 0))
                if start_time and ts < start_time:
                    continue

                pos_side = row.get("positionSide", "").upper()
                if direction and pos_side != direction.upper():
                    continue

                pnl_usdt += float(row.get("realizedPnl", 0.0))
                commission += float(row.get("commission", 0.0))
            except Exception:
                continue

        return round(pnl_usdt, 4), round(commission, 4)
                
    async def set_hedge_mode(
            self,
            session: aiohttp.ClientSession,
            true_hedg: bool,
        ):
        try:
            params = {
                "dualSidePosition": str(true_hedg).lower(),            
            }
            headers = {
                'X-MBX-APIKEY': self.api_key
            }
            params = self.get_signature(params)
            async with session.post(self.change_trade_mode, headers=headers, params=params) as response:
                try:
                    resp_j = await response.json()
                except:
                    resp_j = await response.text()

                self.info_handler.trade_secondary_list.append(f"[{self.user_label}]: {resp_j}. Time: {log_time()}")          
           
        except Exception as ex:
            self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")
   
    async def set_margin_type(
            self,
            session: aiohttp.ClientSession,
            strategy_name: str,
            symbol: str,
            margin_type: str
        ):
        try:
            params = {
                'symbol': symbol,
                'marginType': margin_type,
                'recvWindow': 20000,
                'newClientOrderId': 'CHANGE_MARGIN_TYPE'
            }
            headers = {
                'X-MBX-APIKEY': self.api_key
            }
            params = self.get_signature(params)
            async with session.post(self.set_margin_type_url, headers=headers, params=params) as response:
                await self.requests_logger(response, self.user_label, strategy_name, "set_margin_type", symbol)
        except Exception as ex:
            self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

    async def set_leverage(
            self,
            session: aiohttp.ClientSession,
            strategy_name: str,
            symbol: str,
            lev_size: int
        ):
        try:
            params = {
                'symbol': symbol,
                'recvWindow': 20000,
                'leverage': lev_size
            }
            headers = {
                'X-MBX-APIKEY': self.api_key
            }
            params = self.get_signature(params)
            async with session.post(self.set_leverage_url, headers=headers, params=params) as response:
                await self.requests_logger(response, self.user_label, strategy_name, "set_leverage", symbol)
            
        except Exception as ex:
            self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

    async def make_order(
            self,
            session: aiohttp.ClientSession,
            strategy_name: str,
            symbol: str,
            qty: float,
            side: str,
            position_side: str,
            market_type: str = "MARKET"
        ):
        # try:
        #     mess = "Параметры запроса ордера:...\n"
        #     mess += f"{strategy_name}\n, {api_key}\n, {api_secret}\n, {symbol}\n, {qty}\n, {side}\n, {position_side}\n"
        #     self.info_handler.debug_info_notes(mess, True)
        # except:
        #     passs
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": market_type,
                "quantity": abs(qty) if qty else 0.0,
                "positionSide": position_side,
                "recvWindow": 20000,
                "newOrderRespType": 'RESULT'
            }
            headers = {
                'X-MBX-APIKEY': self.api_key
            }           

            params = self.get_signature(params)
            async with session.post(self.create_order_url, headers=headers, params=params) as response:
                return await self.requests_logger(response, self.user_label, strategy_name, "place_order", symbol, position_side)
            
        except Exception as ex:
            self.info_handler.debug_error_notes(f"{ex} in {inspect.currentframe().f_code.co_name} at line {inspect.currentframe().f_lineno}")

        return {}, self.user_label, strategy_name, symbol, position_side      

    async def place_risk_order(
            self,
            session: aiohttp.ClientSession,
            strategy_name: str,
            symbol: str,
            qty: float,
            side: str,
            position_side: str,
            target_price: float,
            suffix: str,
            order_type: str # MASRKET | LIMIT
        ):
        """
        Универсальный метод для установки условных ордеров (SL/TP/LIMIT) на Binance Futures.

        :param suffix: 
            'sl'  — стоп-лосс
            'tp'  — тейк-профит
        """
        try:
            if suffix == "sl":
                params = {
                    "symbol": symbol,
                    "side": side,
                    "type": "STOP_MARKET",
                    "quantity": abs(qty),
                    "positionSide": position_side,
                    "stopPrice": target_price,
                    "closePosition": "true",
                    "recvWindow": 20000,
                    "newOrderRespType": "RESULT"
                }

            elif suffix == "tp": 
                if order_type.upper() == "MARKET":       
                    params = {
                        "symbol": symbol,
                        "side": side,
                        "type": "TAKE_PROFIT_MARKET",
                        "quantity": abs(qty),
                        "positionSide": position_side,
                        "stopPrice": target_price,
                        "closePosition": "true",
                        "recvWindow": 20000,
                        "newOrderRespType": "RESULT"
                    }

                elif order_type.upper() == "LIMIT":                
                    params = {
                        "symbol": symbol,
                        "side": side,
                        "type": "LIMIT",
                        "quantity": abs(qty),
                        "positionSide": position_side,
                        "price": str(target_price),  # лимитная цена
                        "timeInForce": "GTC",       # удерживать пока не исполнится
                        "recvWindow": 20000,
                        "newOrderRespType": "RESULT"
                    }

                else:
                    raise ValueError(f"Неизвестный suffix: {suffix}")

            headers = {"X-MBX-APIKEY": self.api_key}
            params = self.get_signature(params)

            async with session.post(
                self.create_order_url,
                headers=headers,
                params=params
            ) as response:
                return await self.requests_logger(
                    response,
                    self.user_label,
                    strategy_name,
                    f"place_{suffix.lower()}_order",
                    symbol,
                    position_side
                )

        except Exception as ex:
            self.info_handler.debug_error_notes(
                f"{ex} in {inspect.currentframe().f_code.co_name} "
                f"at line {inspect.currentframe().f_lineno}"
            )

        return {}, self.user_label, strategy_name, symbol, position_side

    async def cancel_orders_by_symbol_side(
            self,
            session: aiohttp.ClientSession,
            symbol: str,
            position_side: str
        ) -> bool:
        """
        Возвращает:
        - True  → все TP/SL отменены или отсутствуют
        - False → хотя бы один TP/SL ОСТАЛСЯ НЕОТМЕНЁН (ошибка Binance)
        """

        headers = {"X-MBX-APIKEY": self.api_key}

        # 1) Список открытых ордеров
        params = {"symbol": symbol}
        params = self.get_signature(params)

        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/openOrders",
                headers=headers,
                params=params
            ) as resp:
                orders = await resp.json()
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[CANCEL][{self.user_label}] Ошибка запроса openOrders: {e}",
                True
            )
            return False
            # 🚨 Ошибка здесь критична — мы НЕ знаем текущее состояние ордеров

        # 2) Фильтрация TP/SL
        risk_orders = [
            o for o in orders
            if o.get("positionSide") == position_side
            and o.get("type") in (
                "LIMIT",                   # твой TP-LIMIT
                "TAKE_PROFIT_MARKET",     # TP-market
                "STOP_MARKET",            # SL-market
                "TAKE_PROFIT",            # старые TP
                "STOP"                    # старые SL
            )
        ]

        if not risk_orders:
            return True   # нечего отменять

        # будет считать успех каждого ордера
        all_ok = True

        # 3) Отменяем каждый ордер
        for o in risk_orders:
            oid = o["orderId"]

            params = {
                "symbol": symbol,
                "orderId": oid,
                "recvWindow": 20000
            }
            params = self.get_signature(params)

            try:
                async with session.delete(
                    self.cancel_order_url,
                    headers=headers,
                    params=params
                ) as resp:

                    try:
                        result = await resp.json()
                    except:
                        txt = await resp.text()
                        result = {"raw": txt}

            except Exception as e:
                self.info_handler.debug_error_notes(
                    f"[CANCEL][{self.user_label}] Ошибка DELETE {symbol}: {e}",
                    True
                )
                all_ok = False
                continue

            # ---------- умная валидация ----------
            if isinstance(result, dict):

                # успешная отмена
                if result.get("status") == "CANCELED":
                    continue

                # ордера уже нет → тоже успех
                if result.get("code") == -2011:
                    continue

            # другие ошибки → фиксируем как fail
            self.info_handler.debug_error_notes(
                f"[{self.user_label}][{symbol}] ❌ Ошибка отмены {oid}: {result}",
                True
            )
            all_ok = False

        return all_ok
