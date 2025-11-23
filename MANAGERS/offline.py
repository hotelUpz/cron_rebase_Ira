import aiofiles
from pathlib import Path
from collections import OrderedDict
from typing import *
from c_log import ErrorHandler


DEBUG_DIR  = Path("INFO/DEBUG")
TRADES_DIR = Path("INFO/TRADES")

DEBUG_ERR_FILE        = DEBUG_DIR  / "error_.txt"
DEBUG_INFO_FILE       = DEBUG_DIR  / "info_.txt"
TRADES_INFO_FILE      = TRADES_DIR / "info_.txt"
TRADES_SECONDARY_FILE = TRADES_DIR / "secondary_.txt"
TRADES_FAILED_FILE    = TRADES_DIR / "failed_.txt"
TRADES_SUCC_FILE      = TRADES_DIR / "success_.txt"


class WriteLogManager():
    """Управляет асинхронной записью логов в файлы и очисткой списков логов."""

    def __init__(self, info_handler: ErrorHandler, max_log_lines: int = 250) -> None:
        self.info_handler = info_handler
        self.MAX_LOG_LINES: int = max_log_lines
        info_handler.wrap_foreign_methods(self)

    async def write_logs(self) -> None:
        logs: List[Tuple[List[str], Path]] = [
            (self.info_handler.debug_err_list, DEBUG_ERR_FILE),
            (self.info_handler.debug_info_list, DEBUG_INFO_FILE),
            (self.info_handler.trade_info_list, TRADES_INFO_FILE),
            (self.info_handler.trade_failed_list, TRADES_FAILED_FILE),
            (self.info_handler.trade_succ_list, TRADES_SUCC_FILE),
        ]

        for log_list, file_path in logs:
            if not log_list:
                continue

            file_path.parent.mkdir(parents=True, exist_ok=True)  # Создаёт директорию, если не существует

            existing_lines: List[str] = []
            if file_path.exists():
                async with aiofiles.open(str(file_path), "r", encoding="utf-8") as f:
                    existing_lines = await f.readlines()

            new_lines = [f"{log}\n" for log in log_list]
            total_lines = existing_lines + new_lines
            total_lines = list(OrderedDict.fromkeys(total_lines))

            if len(total_lines) > self.MAX_LOG_LINES:
                total_lines = total_lines[-self.MAX_LOG_LINES:]

            async with aiofiles.open(str(file_path), "w", encoding="utf-8") as f:
                await f.writelines(total_lines)

            log_list.clear()

        self.info_handler.trade_secondary_list.clear()