# MANAGERS.online.py
import asyncio
import aiohttp
import json
from typing import *
from b_context import BotContext
from c_log import ErrorHandler
import contextlib
# import traceback


CHECK_URL = "https://api.binance.com/api/v3/ping"
SESSION_CHECK_INTERVAL = 15  # секунд


class NetworkManager:
    def __init__(self, info_handler: ErrorHandler, proxy_list: Optional[List[Optional[str]]] = None,
                 user_label: Optional[str] = None, stop_bot: bool = False):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

        self.proxy_list: List[Optional[str]] = proxy_list or [None]
        self.proxy_index: int = 0
        self.proxy_url: Optional[str] = self.proxy_list[self.proxy_index]

        self.user_label = user_label or "network"
        self.session: Optional[aiohttp.ClientSession] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.stop_bot = stop_bot

    # ============================================================
    #  СЕССИЯ
    # ============================================================
    async def initialize_session(self):
        """Создает новую aiohttp-сессию, проксируя через текущий proxy_url."""
        if self.session and not self.session.closed:
            return

        try:
            if self.proxy_url:
                connector = aiohttp.TCPConnector(ssl=False)
                self.session = aiohttp.ClientSession(
                    connector=connector,
                    trust_env=False,
                    proxy=self.proxy_url
                )
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: создана новая сессия с прокси {self.proxy_url}")
            else:
                self.session = aiohttp.ClientSession()
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: создана новая сессия без прокси")
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"{self.user_label}: ошибка при создании сессии: {e}"
            )

    async def _check_session_connection(self, session: aiohttp.ClientSession) -> tuple[bool, Optional[int]]:
        """
        Проверяет доступность Binance API через текущую сессию.
        Возвращает (ok, status_code | None).
        """
        try:
            async with session.get(CHECK_URL, timeout=8) as response:
                ok = (response.status == 200)
                if not ok:
                    # тут явный лог по не-200 статусу
                    self.info_handler.debug_error_notes(
                        f"{self.user_label}: неуспешный HTTP статус → {response.status}"
                    )
                return ok, response.status

        except Exception as e:
            self.info_handler.debug_error_notes(
                f"{self.user_label}: ошибка соединения → {type(e).__name__}: {e}"
            )
            return False, None

    async def _switch_to_next_proxy(self):
        """Переключает на следующий прокси из списка."""
        self.proxy_index = (self.proxy_index + 1) % len(self.proxy_list)
        self.proxy_url = self.proxy_list[self.proxy_index]
        self.info_handler.debug_error_notes(
            f"{self.user_label}: смена прокси → {self.proxy_url or 'без прокси'}"
        )

    # ============================================================
    #  ПРОВЕРКА И ВОССТАНОВЛЕНИЕ
    # ============================================================
    async def validate_session(self) -> tuple[bool, bool, Optional[int]]:
        """
        Проверяет соединение и восстанавливает при необходимости.
        Возвращает (ok, was_reconnected, last_status).
        - ok: True, если удалось получить 200
        - was_reconnected: был ли переход на другие прокси
        - last_status: последний HTTP статус или None при сетевой ошибке
        """
        was_reconnected = False
        last_status: Optional[int] = None

        for attempt in range(1, len(self.proxy_list) * 2):  # 2 прохода по списку
            await self.initialize_session()

            ok, status = await self._check_session_connection(self.session)
            last_status = status

            if ok:
                return True, was_reconnected, last_status

            # закрываем перед пересозданием
            try:
                await self.session.close()
            except Exception:
                pass

            await self._switch_to_next_proxy()
            await asyncio.sleep(min(3 + attempt, 15))
            was_reconnected = True
            self.info_handler.debug_error_notes(
                f"{self.user_label}: попытка переподключения #{attempt}"
            )

        self.info_handler.debug_error_notes(
            f"❌ {self.user_label}: не удалось восстановить соединение после всех прокси", True
        )
        return False, was_reconnected, last_status

    # ============================================================
    #  ФОНОВАЯ ПРОВЕРКА / ПИНГ
    # ============================================================
    async def ping_session(self):
        """
        Поддерживает "живую" сессию, проверяя каждые SESSION_CHECK_INTERVAL секунд.
        При сбое пересоздает сессию.
        """
        while not self.stop_bot:
            ok, reconnected, status = await self.validate_session()
            if not ok:
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: ping неудачен — сессия мертва (status={status})"
                )
            elif reconnected:
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: сессия была пересоздана, status={status}"
                )
            else:
                # self.info_handler.debug_error_notes(
                #     f"{self.user_label}: ping OK, status={status}"
                # )
                pass
            await asyncio.sleep(SESSION_CHECK_INTERVAL)

    async def start_ping_loop(self):
        """Запускает фонового пингера."""
        if not self._ping_task or self._ping_task.done():
            self._ping_task = asyncio.create_task(self.ping_session())
            self.info_handler.debug_error_notes(
                f"{self.user_label}: запущен фоновой ping-сервис"
            )

    async def shutdown_session(self):
        """Закрывает aiohttp-сессию и останавливает пинг-задачу."""
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self.session and not self.session.closed:
            try:
                await self.session.close()
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: aiohttp-сессия закрыта"
                )
            except Exception as e:
                self.info_handler.debug_error_notes(
                    f"{self.user_label}: ошибка при закрытии сессии: {e}"
                )

# # python -m MANAGERS.networks

class WS_HotPrice_Stream:
    """WebSocket с ротацией прокси — поведение полностью как в NetworkManager."""

    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        proxy_list: Optional[List[Optional[str]]] = None,
        ws_url: str = "wss://fstream.binance.com/"
    ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.context = context

        # proxy
        self.proxy_list = proxy_list or [None]
        self.proxy_index = 0
        self.proxy_url = self.proxy_list[self.proxy_index]
        self.proxy_auth = None

        # ws state
        self.ws_url_base = ws_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.ws_shutdown_event = asyncio.Event()
        self.is_connected = False

        # ping
        self.ping_task: Optional[asyncio.Task] = None

        # symbols
        self.last_symbols_set = set()

        # testing fields
        self.last_connect_status = None
        self.last_error = None

    # ---------------------------------------------------------
    # PROXY ROTATION
    # ---------------------------------------------------------
    def _switch_to_next_proxy(self):
        self.proxy_index = (self.proxy_index + 1) % len(self.proxy_list)
        self.proxy_url = self.proxy_list[self.proxy_index]
        self.info_handler.debug_error_notes(
            f"[WS] Смена прокси → {self.proxy_url or 'без прокси'}"
        )

    # ---------------------------------------------------------
    # OPEN SESSION + CONNECT WS
    # ---------------------------------------------------------
    async def _try_connect_once(self, url: str) -> bool:
        """Одна попытка подключения (как validate_session в NetworkManager)."""
        try:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession()

            self.websocket = await self.session.ws_connect(
                url,
                proxy=self.proxy_url,
                proxy_auth=self.proxy_auth,
                autoping=False
            )

            self.last_connect_status = "success"
            self.last_error = None

            self.info_handler.debug_info_notes(
                f"[WS] Успешное подключение → {url} / proxy={self.proxy_url}"
            )
            return True

        except Exception as e:
            self.last_connect_status = "fail"
            self.last_error = str(e)

            self.info_handler.debug_error_notes(
                f"[WS ERROR] proxy={self.proxy_url}, err={e}"
            )
            return False

    # ---------------------------------------------------------
    # CONNECT WITH FULL ROTATION
    # ---------------------------------------------------------
    async def _connect_with_rotation(self, url: str) -> bool:
        """Поведение полностью как у NetworkManager — пройти весь список."""
        for attempt in range(1, len(self.proxy_list) * 2 + 1):

            ok = await self._try_connect_once(url)
            if ok:
                return True

            # close session before retry
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None

            self._switch_to_next_proxy()

            await asyncio.sleep(min(attempt + 1, 10))

        return False

    # ---------------------------------------------------------
    # MAIN LOOP
    # ---------------------------------------------------------
    async def _ws_main_loop(self, symbols: List[str]):
        streams = [f"{s.lower()}@trade" for s in symbols]
        ws_url = f"{self.ws_url_base}stream?streams={'/'.join(streams)}"

        while not self.ws_shutdown_event.is_set():
            try:
                ok = await self._connect_with_rotation(ws_url)
                if not ok:
                    self.info_handler.debug_error_notes("[WS] Не найден рабочий прокси.")
                    await asyncio.sleep(3)
                    continue

                self.is_connected = True

                # run ping
                self.ping_task = asyncio.create_task(self._ping_loop())

                try:
                    async for msg in self.websocket:
                        if self.ws_shutdown_event.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_ws_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break

                except Exception as e:
                    self.info_handler.debug_error_notes(f"[WS LOOP ERROR] {e}")

                # on disconnect
                self.is_connected = False

                if self.ping_task:
                    self.ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.ping_task

                # close session fully
                if self.session and not self.session.closed:
                    await self.session.close()
                    self.session = None

                self._switch_to_next_proxy()
            
            finally:
                await asyncio.sleep(1)

        self.info_handler.debug_error_notes("[WS] Main loop stopped")

    # ---------------------------------------------------------
    async def _ping_loop(self):
        while not self.ws_shutdown_event.is_set():
            await asyncio.sleep(15)
            if self.websocket:
                try:
                    await self.websocket.ping()
                except:
                    break

    # ---------------------------------------------------------
    async def _handle_ws_message(self, message: str):
        try:
            data = json.loads(message).get("data")
            if not data:
                return
            symbol = data.get("s")
            price = float(data.get("p", 0))
            if symbol and price > 0:
                self.context.ws_price_data[symbol] = {"close": price}
        except Exception as e:
            self.info_handler.debug_error_notes(f"[WS MSG ERROR] {e}")

    # ---------------------------------------------------------
    async def sync_ws_streams(self, symbols: List[str]):
        s = set(symbols)
        if s != self.last_symbols_set:
            self.last_symbols_set = s
            await self.stop_ws()
            if s:
                self.ws_shutdown_event.clear()
                self.ws_task = asyncio.create_task(self._ws_main_loop(list(s)))

    # ---------------------------------------------------------
    async def stop_ws(self):
        self.ws_shutdown_event.set()

        if self.ws_task:
            self.ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.ws_task
            self.ws_task = None

        if self.ping_task:
            self.ping_task.cancel()

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

        self.is_connected = False

