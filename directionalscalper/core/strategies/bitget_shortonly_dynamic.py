import time, math
from decimal import Decimal, ROUND_HALF_UP
from .strategy import Strategy

class BitgetShortOnlyDynamicStrategy(Strategy):
    def __init__(self, exchange, manager, config):
        super().__init__(exchange, config)
        self.manager = manager
        self.last_cancel_time = 0

    def limit_order(self, symbol, side, amount, price, reduce_only=False):
        min_qty_usd = 5
        current_price = self.exchange.get_current_price(symbol)
        min_qty_bitget = min_qty_usd / current_price

        print(f"Min trade quantity for {symbol}: {min_qty_bitget}")

        if float(amount) < min_qty_bitget:
            print(f"The amount you entered ({amount}) is less than the minimum required by Bitget for {symbol}: {min_qty_bitget}.")
            return
        order = self.exchange.create_order(symbol, 'limit', side, amount, price, reduce_only=reduce_only)
        return order

    def take_profit_order(self, symbol, side, amount, price, reduce_only=True):
        min_qty_usd = 5
        current_price = self.exchange.get_current_price(symbol)
        min_qty_bitget = min_qty_usd / current_price

        print(f"Min trade quantity for {symbol}: {min_qty_bitget}")

        if float(amount) < min_qty_bitget:
            print(f"The amount you entered ({amount}) is less than the minimum required by Bitget for {symbol}: {min_qty_bitget}.")
            return
        order = self.exchange.create_order(symbol, 'limit', side, amount, price, reduce_only=reduce_only)
        return order

    def close_position(self, symbol, side, amount):
        try:
            self.exchange.create_market_order(symbol, side, amount)
            print(f"Closed {side} position for {symbol} with amount {amount}")
        except Exception as e:
            print(f"An error occurred while closing the position: {e}")

    def get_open_take_profit_order_quantity(self, orders, side):
        for order in orders:
            if order['side'] == side and order['reduce_only']:
                return order['qty'], order['id']
        return None, None

    def parse_symbol(self, symbol):
        if "bitget" in self.exchange.name.lower():
            if symbol == "PEPEUSDT" or symbol == "PEPEUSDT_UMCBL":
                return "1000PEPEUSDT"
            return symbol.replace("_UMCBL", "")
        return symbol

    def cancel_take_profit_orders(self, symbol, side):
        self.exchange.cancel_close_bitget(symbol, side)

    def has_open_orders(self, symbol):
        open_orders = self.exchange.get_open_orders(symbol)
        return len(open_orders) > 0
    
    def calculate_short_take_profit(self, short_pos_price, symbol):
        if short_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            short_target_price = Decimal(short_pos_price) - (ma_6_high - ma_6_low)
            short_target_price = short_target_price.quantize(
                Decimal('1e-{}'.format(price_precision)),
                rounding=ROUND_HALF_UP
            )

            short_profit_price = short_target_price

            return float(short_profit_price)
        return None
    
    def round_amount(self, amount, price_precision):
        return round(amount, int(price_precision))

    def run(self, symbol):
        min_dist = self.config.min_distance
        min_vol = self.config.min_volume
        wallet_exposure = self.config.wallet_exposure
        min_order_value = 6
        max_retries = 5
        retry_delay = 5

        while True:
            # Get balance
            quote_currency = "USDT"

            for i in range(max_retries):
                try:
                    dex_equity = self.exchange.get_balance_bitget(quote_currency)
                    break
                except Exception as e:
                    if i < max_retries - 1:
                        print(f"Error occurred while fetching balance: {e}. Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        raise e

            market_data = self.exchange.get_market_data_bitget(symbol)

            price_precision = market_data["precision"]

            # Orderbook data
            orderbook = self.exchange.get_orderbook(symbol)
            best_bid_price = orderbook['bids'][0][0]
            best_ask_price = orderbook['asks'][0][0]

            # Max trade quantity calculation
            leverage = float(market_data["leverage"]) if market_data["leverage"] != 0 else 50.0

            max_trade_qty = round(
                (float(dex_equity) * wallet_exposure / float(best_ask_price))
                / (100 / leverage),
                int(float(market_data["min_qty"])),
            )

            print(f"Max trade quantity for {symbol}: {max_trade_qty}")

            current_price = self.exchange.get_current_price(symbol)

            original_amount = min_order_value / current_price

            # amount = self.round_amount(og_amount, price_precision)
            amount = math.ceil(original_amount * 100) / 100

            print(f"Dynamic entry amount: {amount}")

            min_qty_bitget = min_order_value / current_price

            print(f"Min trade quantitiy for {symbol}: {min_qty_bitget}")
            print(f"Min volume: {min_vol}")
            print(f"Min distance: {min_dist}")

            # Get data from manager
            data = self.manager.get_data()

            # Parse the symbol according to the exchange being used
            parsed_symbol = self.parse_symbol(symbol)

            # Data we need from API
            one_minute_volume = self.manager.get_asset_value(parsed_symbol, data, "1mVol")
            five_minute_distance = self.manager.get_asset_value(parsed_symbol, data, "5mSpread")
            trend = self.manager.get_asset_value(parsed_symbol, data, "Trend")
            print(f"1m Volume: {one_minute_volume}")
            print(f"5m Spread: {five_minute_distance}")
            print(f"Trend: {trend}")

            # data = self.exchange.exchange.fetch_positions([symbol])
            # print(f"Bitget positions response: {data}")   
 
            # Get position data from exchange
            for i in range(max_retries):
                try:
                    print("Fetching position data")
                    position_data = self.exchange.get_positions_bitget(symbol) 
                    break
                except Exception as e:
                    if i < max_retries - 1:
                        print(f"Error occurred while fetching position data: {e}. Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        raise e

            short_pos_qty = position_data["short"]["qty"]
            long_pos_qty = position_data["long"]["qty"]
            short_upnl = position_data["short"]["upnl"]
            long_upnl = position_data["long"]["upnl"]

            print(f"Short pos qty: {short_pos_qty}")
            print(f"Long pos qty: {long_pos_qty}")
            print(f"Short uPNL: {short_upnl}")
            print(f"Long uPNL: {long_upnl}")

            short_pos_price = position_data["short"]["price"] if short_pos_qty > 0 else None
            long_pos_price = position_data["long"]["price"] if long_pos_qty > 0 else None

            print(f"Short pos price: {short_pos_price}")
            print(f"Long pos price: {long_pos_price}")

            # Get the 1-minute moving averages
            print(f"Fetching MA data")
            m_moving_averages = self.manager.get_1m_moving_averages(symbol)
            m5_moving_averages = self.manager.get_5m_moving_averages(symbol)
            ma_6_low = m_moving_averages["MA_6_L"]
            ma_3_low = m_moving_averages["MA_3_L"]
            ma_3_high = m_moving_averages["MA_3_H"]
            ma_1m_3_high = self.manager.get_1m_moving_averages(symbol)["MA_3_H"]
            ma_5m_3_high = self.manager.get_5m_moving_averages(symbol)["MA_3_H"]

            # Take profit calc
            short_take_profit = self.calculate_short_take_profit(short_pos_price, symbol)

            print(f"Long take profit: {short_take_profit}")

            if short_take_profit is not None:
                precise_long_take_profit = round(short_take_profit, int(-math.log10(price_precision)))

            should_short = best_bid_price > ma_3_high

            should_add_to_short = False

            if short_pos_price is not None:
                should_add_to_short = short_pos_price < ma_6_low

            print(f"Long condition: {should_short}")
            print(f"Add long condition: {should_add_to_short}")

            # Long only logic
            if trend is not None and isinstance(trend, str):
                if one_minute_volume is not None and five_minute_distance is not None:
                    if one_minute_volume > min_vol and five_minute_distance > min_dist:

                        if trend.lower() == "short" and should_short and long_pos_qty == 0:

                            self.limit_order(symbol, "buy", amount, best_bid_price, reduce_only=False)
                            print(f"Placed initial long entry")
                            time.sleep(0.05)
                        else:
                            if trend.lower() == "long" and should_add_to_short and long_pos_qty < max_trade_qty and best_bid_price < long_pos_price:
                                print(f"Placed additional long entry")
                                self.limit_order(symbol, "buy", amount, best_bid_price, reduce_only=False)
                                time.sleep(0.05)
            
            open_orders = self.exchange.get_open_orders_bitget(symbol)

            if short_pos_qty > 0 and short_take_profit is not None:
                existing_short_tp_qty, existing_short_tp_id = self.get_open_take_profit_order_quantity(open_orders, "close_short")
                if existing_short_tp_qty is None or existing_short_tp_qty != short_pos_qty:
                    try:
                        if existing_short_tp_id is not None:
                            self.cancel_take_profit_orders(symbol, "short")
                            print(f"Short take profit canceled")
                            time.sleep(0.05)

                        self.exchange.create_take_profit_order(symbol, "limit", "buy", short_pos_qty, short_take_profit, reduce_only=True)
                        print(f"Short take profit set at {short_take_profit}")
                        time.sleep(0.05)
                    except Exception as e:
                        print(f"Error in placing short TP: {e}")

            # Cancel entries
            current_time = time.time()
            if current_time - self.last_cancel_time >= 60:  # Execute this block every 1 minute
                try:
                    if best_ask_price < ma_1m_3_high or best_ask_price < ma_5m_3_high:
                        self.exchange.cancel_all_entries(symbol)
                        print(f"Canceled entry orders for {symbol}")
                        time.sleep(0.05)
                except Exception as e:
                    print(f"An error occurred while canceling entry orders: {e}")

                self.last_cancel_time = current_time  # Update the last cancel time

            time.sleep(30)
