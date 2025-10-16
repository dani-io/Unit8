# 🧠 Unit8
### *The Unified Autonomous Trading Framework for Humans & Machines*

**Unit8** is an open-source, modular framework for building, testing, and running autonomous trading systems.  
Its goal is simple:  
To create a trading brain that works seamlessly across **MetaTrader5** and **Crypto exchanges (CCXT)** — without rewriting a single line of strategy code.

---

## 🚀 Key Features

- **Cross-Broker Engine:**  
  A unified API for trading on MT5 and CCXT exchanges (Binance, OKX, KuCoin, ...)

- **Dual Backtesting Core:**  
  Choose between two backtesting engines:  
  → `Backtrader` for detailed simulation  
  → `Vectorbt` for ultra-fast parameter testing  

- **Modular Strategy System:**  
  Design, test, and run multiple strategies in one environment —  
  from Ichimoku and EMA crosses to custom algorithmic models.

- **Smart Risk Manager:**  
  Position sizing based on balance percentage, real spread, and commission  
  + automatic daily loss and volume limits.

- **Dynamic Spread Control:**  
  Avoids trading during abnormal spreads or high-volatility periods (e.g., news events).

- **Fundamental Shield:**  
  News-aware module that pauses trading during impactful economic events.

- **Unified Execution Interface:**  
  The same strategy code works in both live and backtest modes.

- **Telegram & Dashboard Integration (Unit24):**  
  Telegram bot for quick monitoring + Next.js dashboard for full control.

---

## ⚙️ Module Structure

```
unit8/
├── core/
│   ├── strategy.py       ← Base class for all strategies
│   ├── indicators.py     ← Ichimoku, MACD, SMA, EMA, Stochastic
│   ├── risk.py           ← Risk & position sizing
│   ├── spread.py         ← Spread tracking & filtering
│   ├── execution_engine.py
│   ├── fundamental.py
│   └── monitor.py
│
├── services/
│   ├── broker/
│   │   ├── mt5_broker.py
│   │   ├── ccxt_broker.py
│   │   └── mock_broker.py
│   └── notifier/
│       ├── telegram_bot.py
│       └── discord_webhook.py
│
├── backtest/
│   ├── backtrader_engine.py
│   ├── vector_engine.py
│   └── analysis.py
│
└── config/
    └── settings.json
```

---

## 🧩 Design Philosophy

> **“A trader doesn’t predict the market — he harmonizes with it.”**

Unit8 is built for flexibility.  
Each module is independent and fully testable.  
Every layer (data, strategy, risk, execution) is decoupled but works together like a well-tuned orchestra.

---

## 🐍 Quick Setup

```bash
git clone https://github.com/dani-io/unit8.git
cd unit8
pip install -r requirements.txt
```

---

## ⚡ Example Usage

```python
from unit8.core.strategy import IchimokuStrategy
from unit8.core.execution_engine import ExecutionEngine
from unit8.services.broker.mt5_broker import MT5Broker

broker = MT5Broker(config)
engine = ExecutionEngine(mode="live", broker=broker)
strategy = IchimokuStrategy(engine=engine)

strategy.run()
```

---

## 🌌 Roadmap

- AI optimizer for strategy parameters  
- Strategy & model marketplace  
- Dockerized microservices for parallel execution  
- Integration with Unit24 Dashboard and Telegram  

---

## 🧩 License  
**MIT License** — free to learn, build, and evolve.  
Just treat it with fairness :)

---

## 💬 Inspiration  
> “Trade is an art. Code is the brush.” 🎨
