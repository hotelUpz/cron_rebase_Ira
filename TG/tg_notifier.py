import asyncio
import aiohttp
from a_settings import *
from b_context import BotContext
from c_log import ErrorHandler, log_time
from c_utils import milliseconds_to_datetime
from typing import *
import random
import traceback


# === Утилита для форматирования сообщений ===
class MessageFormatter:
    def __init__(self, context: BotContext, info_handler: ErrorHandler):
        self.context = context
        self.info_handler = info_handler

    def preform_message(
        self,
        marker: str,
        body: dict,
        is_print: bool = True
    ) -> None:
        # === ВСЯ ВАША ФУНКЦИЯ ===
        msg = ""
        try:          
            head = f"{HEAD_LINE_TYPE}" * HEAD_WIDTH
            footer = f"{FOOTER_LINE_TYPE}" * FOOTER_WIDTH

            user_name = body.get("user_name")
            symbol = body.get("symbol")
            # leverage = body.get("leverage")
            pos_side = body.get("pos_side")

            cur_time = milliseconds_to_datetime(body.get("cur_time"))
            
            # if marker == "signal":
            #     pass
            #     msg = (
            #         f"{head}\n"
            #         f"SIGNAL RECEIVED |[{symbol}]\n\n"
            #         f"[{cur_time}]\n\n"
            #         f"{footer}\n"
            #     )
            # elif marker == "market_order_sent":
            #     msg = (
            #         f"{head}\n\n"
            #         f"MARKET ORDER SENT [{symbol}]\n"
            #         f"[{cur_time}]\n\n"
            #         f"{footer}\n"
            #     )
            # elif marker == "market_order_filled":
            #     entry_price = to_human_digit(body.get("entry_price"))
            #     tp_price_levels = body.get("tp_price_levels")
            #     tp_msg_templete = ""
            #     delimiter = "|"
            #     to_next_line = ""
            #     for ind, tp in enumerate(tp_price_levels, start=1):
            #         delimiter = "|" if (ind % 2 != 0 and ind != len(tp_price_levels)) else ""
            #         to_next_line = "\n" if (ind % 2 == 0 or ind == len(tp_price_levels)) else ""
            #         tp_msg_templete += f"TP{ind}: {to_human_digit(tp)} {delimiter}{to_next_line}"

            #     sl = to_human_digit(body.get("cur_sl"))

            #     msg = (
            #         f"{head}\n\n"
            #         f"MARKET ORDER FILLED [{symbol}]\n\n"
            #         f"[{cur_time}]\n\n"
            #         f"LEVERAGE - {leverage} | ENTRY - {entry_price}\n\n"
            #         f"{tp_msg_templete}\n"
            #         f"SL: {sl}\n\n"
            #         f"{footer}\n"
            #     )
            # elif marker == "progress":
            #     progress = body.get("progress")
            #     sl = body.get("cur_sl")
            #     msg = (
            #         f"{head}\n\n"                                  
            #         f"[{symbol}] | TP{progress} SUCCESS\n"
            #         f"NEW_SL - {sl}\n\n"
            #         f"{footer}\n"
            #     )
            # elif marker in {"market_order_failed", "tp_order_failed", "sl_order_failed"}:
            #     reason = body.get("reason")
            #     msg = (
            #         f"{head}\n\n"                                  
            #         f"MARKET ORDER FAILED [{symbol}]\n"
            #         f"[{cur_time}]\n"
            #         f"REASON - {reason}\n\n"
            #         f"{footer}\n"
            #     )
            if marker == "report":
                pnl_pct = body.get("pnl_pct")
                pnl_usdt = body.get("pnl_usdt")
                commission = body.get("commission", "N/A")
                time_in_deal = body.get("time_in_deal", "N/A")

                if pnl_pct is None:
                    emo = "N/A"
                elif pnl_pct > 0:
                    emo = f"{EMO_SUCCESS} SUCCESS"
                elif pnl_pct < 0:
                    emo = f"{EMO_LOSE} LOSE"
                else:
                    emo = f"{EMO_ZERO} 0 P&L"

                pnl_pct_str = f"{pnl_pct:.2f}%" if pnl_pct is not None else "N/A"
                if pnl_usdt is not None:
                    sign = "+" if pnl_usdt > 0 else "-" if pnl_usdt < 0 else ""
                    pnl_usdt_str = f"{sign} {abs(pnl_usdt):.4f}"
                else:
                    pnl_usdt_str = "N/A"

                msg = (
                    f"{head}\n\n"
                    f"[{user_name}][{symbol}][{pos_side}] | {emo}\n"
                    f"PNL {pnl_pct_str} | PNL {pnl_usdt_str} USDT\n"
                    f"COMISSION - {commission}\n"
                    f"CLOSING TIME - [{cur_time}]\n"
                    f"TIME IN DEAL - {time_in_deal}\n"
                    f"{footer}\n"
                )
            else:
                print(f"Неизвестный тип сообщения в preform_message. Marker: {marker}")

            # --- Сохраняем и печатаем ---
            self.context.report_list.append(msg)
            if is_print:
                print(msg)

        except Exception as e:
            err_msg = f"[ERROR] preform_message: {e}\n"
            err_msg += traceback.format_exc()
            self.info_handler.debug_error_notes(err_msg, is_print=True)
    

class TelegramNotifier(MessageFormatter):
    def __init__(
            self,
            token: str,
            chat_ids: list[int],
            context: BotContext,
            info_handler: ErrorHandler
        ):
        super().__init__(context, info_handler)
        self.token = token
        self.chat_ids = [x.strip() for x in chat_ids if x and isinstance(x, str)]
        self.base_tg_url = f"https://api.telegram.org/bot{self.token}"
        self.send_text_endpoint = "/sendMessage"
        self.send_photo_endpoint = "/sendPhoto"
        self.delete_msg_endpoint = "/deleteMessage"

        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler
        self.stop_bot = context.stop_bot
        self.report_list = context.report_list

    async def send_report_batches(self, is_send: bool = True, batch_size: int = 1):
        """Отправляет накопленные сообщения в TG пачками по batch_size, удаляя их только после отправки."""
        if not isinstance(batch_size, int) or batch_size < 1:
            print(f"[ERROR] Invalid batch_size={batch_size!r}: must be int >= 1")
            return

        while self.report_list and not self.stop_bot:
            # Чистим список от пустых значений, не меняя ссылку
            self.report_list[:] = [
                x for x in self.report_list
                if isinstance(x, str) and x.strip()
            ]

            if not self.report_list:
                break

            batch = self.report_list[:batch_size]
            text_block = "\n\n".join(batch)

            try:
                if is_send:
                    await self.send(
                        text=text_block,
                        photo_bytes=None,
                        disable_notification=False
                    )
            except Exception as e:
                print(f"[ERROR][TG send]: {e} ({log_time()})")

            # Удаляем только отправленные сообщения
            del self.report_list[:len(batch)]
            await asyncio.sleep(0.25)

    async def send(
        self,
        text: str,
        photo_bytes: bytes = None,
        disable_notification: bool = False,
        max_retries: int = float("inf"),
    ):
        """
        Отправка сообщения с авто-реконнектом и повторными попытками.
        """

        async def _try_send(session: aiohttp.ClientSession, chat_id):
            if photo_bytes:
                url = self.base_tg_url + self.send_photo_endpoint
                data = aiohttp.FormData()
                data.add_field("chat_id", str(chat_id))
                data.add_field("caption", text or "")
                data.add_field("parse_mode", "HTML")
                data.add_field("disable_web_page_preview", "true")
                data.add_field("disable_notification", str(disable_notification).lower())
                data.add_field("photo", photo_bytes, filename="spread.png", content_type="image/png")
            else:
                url = self.base_tg_url + self.send_text_endpoint
                data = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": disable_notification,
                }

            # Повторные попытки с backoff
            attempt = 0
            while not self.stop_bot:
                attempt += 1
                try:
                    async with session.post(url, data=data, timeout=10) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            raise Exception(f"HTTP {resp.status}: {err_text}")

                        # response_json = await resp.json()
                        # message_id = response_json.get("result", {}).get("message_id")
                        return True  # успех

                except Exception as e:
                    wait_time = random.uniform(1, 3)  # backoff
                    if self.info_handler:
                        self.info_handler.debug_error_notes(
                            f"[TelegramSender] Попытка {attempt}/{max_retries} не удалась ({e}), "
                            f"повтор через {wait_time:.1f}с",
                            is_print=True,
                        )
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(wait_time)

        # Новый session для каждой пачки отправок
        async with aiohttp.ClientSession() as session:
            tasks = [_try_send(session, chat_id) for chat_id in self.chat_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return all(r is True for r in results)
