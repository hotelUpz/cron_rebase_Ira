class StrategySettings():
    strategy_notes = [      
        ("cron", {                                    # Ключ - название стратегии, можно любой например с суфиксами (volf_stoch1)
            "LONG": {
                "entry_conditions": {
                    "rules": {
                        'CRON': {
                            'enable': True,            # True/False -- использовать/не использовать
                            'tfr': "5m"
                        },             
                    },                  
                    "grid_orders": [
                        {'indent': 0.0, 'volume': 10.52, 'signal': True},
                        {'indent': -8.0, 'volume': 11.57, 'signal': False}, # %
                        {'indent': -16, 'volume': 12.73, 'signal': False}, # %
                        {'indent': -24, 'volume': 14, 'signal': False}, # %
                        {'indent': -34, 'volume': 15.4, 'signal': False}, # %
                        {'indent': -55, 'volume': 16.94, 'signal': False}, # %
                        {'indent': -89, 'volume': 18.63, 'signal': False}, # %
                    ],                      
                },

            },

            "SHORT": {
                "entry_conditions": {
                    "rules": {
                        'CRON': {
                            'enable': True,            # True/False -- использовать/не использовать
                            'tfr': "5m",
                        },            
                    },           
                    "grid_orders": [
                        {'indent': 0.0, 'volume': 10.52, 'signal': True},
                        {'indent': -8.0, 'volume': 12.73, 'signal': False}, # %
                        {'indent': -16.0, 'volume': 14, 'signal': False}, # %
                        {'indent': -24.0, 'volume': 15.4, 'signal': False}, # %
                        {'indent': -81.0, 'volume': 16.94, 'signal': False}, # %
                        {'indent': -243.0, 'volume': 18.63, 'signal': False}, # %
                    ],                     
                },
            }                        
        })
    ]
    

class UsersSettings():
    users_config = {
        "IraInvest": {                                  # -- имя пользователя
            "keys": {
                "BINANCE_API_PUBLIC_KEY": "atvd6xJm8aCJKyCeeqnFdidbNoHAz4OwHMBVEMNCnfhKjUoiJ2F6LPJ11eHeyoZ5", # Ira base
                "BINANCE_API_PRIVATE_KEY": "0QOqV5mlLLPFUIIVxc7kSIjAqKVFEWrKje1d2sT0UkCrsXc7DD4wYNgn39wCTvyG"
            },

            "proxies": [             
                {
                    "enable": True,
                    "proxy_address": '154.218.20.43',
                    "proxy_port": '64630',
                    "proxy_login": '1FDJcwJR',
                    "proxy_password": 'U2yrFg4a'
                }
                # None
            ],

            "core": { 
                "margin_type": "CROSSED",         # Тип маржи. Кросс-маржа → "CROSSED", Изолированная → "ISOLATED"
                "quote_asset": "USDT",            # → валюта, в которой указана цена (например, USDT, USDC, BUSD)
                "direction": 3,                   # 1 -- LONG, 2 --SHORT, 3 -- BOTH
                "long_positions_limit": 5,        # количество одновременно открываемых лонгов
                "short_positions_limit": 5,       # количество одновременно открываемых шортов
            },

            "symbols_risk": {
                # # ____________________ # -- здесь через запятую точечная настройка рисков для конкретного символа (как ниже)
                "ANY_COINS": {
                    "margin_size": 25.6,          # размер маржи в USDT (либо другой базовой валюте)
                    "leverage": 10,              # размер плеча. Общий объем на сделку == (margin_size x leverage)
                    "sl": None,                  # %, float, отрицательное значение. Отключено -- None
                    "fallback_sl": None,           # tp на случай отказа основного тейка
                    "tp": 0.6,  # TP             # %, float, положительное значение. Отключено -- None
                    "tp_order_type": "LIMIT",    # MARKET | LIMIT
                    "fallback_tp": 0.9,           # tp на случай отказа основного тейка
                },
            },

            "strategies_symbols": [
                ("cron", {                                  # -- название стратегии
                    "enable": True,
                    "symbols": {"BR", "ARIA", "REI", "SOPH", "PARTI"},
                }),
            ],

        }
    }

TG_BOT_TOKEN: str = "" # -- токен бота
TG_BOT_ID:    str = "" # -- id бота

# ----------- UTILS ---------------
TZ_STR:        str = "Europe/Berlin"         # часовой пояс ("Europe/Berlin")
WRITE_TO_LOG:  bool = False                  # флаг записи логов в файл
MAX_LOG_LINES: int = 1001                    # количество строк в лог файлах

# --- STYLES ---
HEAD_WIDTH = 35
HEAD_LINE_TYPE = "" #  либо "_"
FOOTER_WIDTH = 35
FOOTER_LINE_TYPE = "" #  либо "_"
EMO_SUCCESS = "🟢"
EMO_LOSE = "🔴"
EMO_ZERO = "⚪"
EMO_ORDER_FILLED = "🤞"


# curl -x 'http://154.218.20.43:64630' --proxy-user '1FDJcwJR:U2yrFg4a' https://ipinfo.io/json