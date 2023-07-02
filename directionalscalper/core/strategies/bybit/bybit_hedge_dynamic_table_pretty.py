import time
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
from ..strategy import Strategy
from typing import Tuple
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich import box


class BybitHedgeDynamicLeverageTablePretty(Strategy):
    def __init__(self, exchange, manager, config):
        super().__init__(exchange, config, manager)
        self.manager = manager
        self.last_cancel_time = 0
        self.wallet_exposure_limit = self.config.wallet_exposure_limit
        self.current_wallet_exposure = 1.0
        self.printed_trade_quantities = False
        self.checked_amount_validity = False
        self.max_long_trade_qty = None
        self.max_short_trade_qty = None
        self.initial_max_long_trade_qty = None
        self.initial_max_short_trade_qty = None
        self.long_leverage_increased = False
        self.short_leverage_increased = False

    def run(self, symbol):
        # Create console and table instances
        console = Console()

        # Create table with two columns
        table = Table(show_header=False, header_style="bold magenta", title="Directional Scalper v2.0.0")
        table.add_column("Key")
        table.add_column("Value")
        live = Live(table, refresh_per_second=2)

        quote_currency = "USDT"
        max_retries = 5
        retry_delay = 5

        # Initialize exchange-related variables outside the live context
        wallet_exposure = self.config.wallet_exposure
        min_dist = self.config.min_distance
        min_vol = self.config.min_volume
        current_leverage = self.exchange.get_current_leverage_bybit(symbol)
        max_leverage = self.exchange.get_max_leverage_bybit(symbol)

        print("Setting up exchange")
        self.exchange.setup_exchange_bybit(symbol)

        print("Setting leverage")
        if current_leverage != max_leverage:
            print(f"Current leverage is not at maximum. Setting leverage to maximum. Maximum is {max_leverage}")
            self.exchange.set_leverage_bybit(max_leverage, symbol)

        previous_five_minute_distance = None
        previous_thirty_minute_distance = None
        previous_one_hour_distance = None
        previous_four_hour_distance = None

        with live:
            while True:

                print(f"[Bybit hedge dynamic entry/exit unstuck strategy running]")
                print(f"Min volume: {min_vol}")
                print(f"Min distance: {min_dist}")

                # Get API data
                data = self.manager.get_data()
                one_minute_volume = self.manager.get_asset_value(symbol, data, "1mVol")
                one_minute_distance = self.manager.get_asset_value(symbol, data, "1mSpread")
                five_minute_distance = self.manager.get_asset_value(symbol, data, "5mSpread")
                thirty_minute_distance = self.manager.get_asset_value(symbol, data, "30mSpread")
                one_hour_distance = self.manager.get_asset_value(symbol, data, "1hSpread")
                four_hour_distance = self.manager.get_asset_value(symbol, data, "4hSpread")
                trend = self.manager.get_asset_value(symbol, data, "Trend")
                print(f"1m Volume: {one_minute_volume}")
                print(f"1m Spread: {one_minute_distance}")
                print(f"5m Spread: {five_minute_distance}")
                print(f"30m Spread: {thirty_minute_distance}")
                print(f"1h Spread: {one_hour_distance}")
                print(f"4h Spread: {four_hour_distance}")
                print(f"Trend: {trend}")

                #price_precision = int(self.exchange.get_price_precision(symbol))

                #print(f"Precision: {price_precision}")

                quote_currency = "USDT"

                for i in range(max_retries):
                    try:
                        total_equity = self.exchange.get_balance_bybit(quote_currency)
                        break
                    except Exception as e:
                        if i < max_retries - 1:
                            print(f"Error occurred while fetching balance: {e}. Retrying in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                        else:
                            raise e
                        
                print(f"Total equity: {total_equity}")

                for i in range(max_retries):
                    try:
                        available_equity = self.exchange.get_available_balance_bybit(quote_currency)
                        break
                    except Exception as e:
                        if i < max_retries - 1:
                            print(f"Error occurred while fetching available balance: {e}. Retrying in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                        else:
                            raise e

                print(f"Available equity: {available_equity}")

                current_price = self.exchange.get_current_price(symbol)
                market_data = self.get_market_data_with_retry(symbol, max_retries = 5, retry_delay = 5)
                #contract_size = self.exchange.get_contract_size_bybit(symbol)
                best_ask_price = self.exchange.get_orderbook(symbol)['asks'][0][0]
                best_bid_price = self.exchange.get_orderbook(symbol)['bids'][0][0]

                print(f"Best bid: {best_bid_price}")
                print(f"Best ask: {best_ask_price}")
                print(f"Current price: {current_price}")

                if self.max_long_trade_qty is None or self.max_short_trade_qty is None:
                    self.max_long_trade_qty = self.max_short_trade_qty = self.calc_max_trade_qty(total_equity,
                                                                                                best_ask_price,
                                                                                                max_leverage)

                    # Set initial quantities if they're None
                    if self.initial_max_long_trade_qty is None:
                        self.initial_max_long_trade_qty = self.max_long_trade_qty
                        print(f"Initial max trade qty set to {self.initial_max_long_trade_qty}")
                    if self.initial_max_short_trade_qty is None:
                        self.initial_max_short_trade_qty = self.max_short_trade_qty  
                        print(f"Initial trade qty set to {self.initial_max_short_trade_qty}")                                                            
                            
                print(f"Max long trade quantity for {symbol}: {self.max_long_trade_qty}")
                print(f"Max short trade quantity for {symbol}: {self.max_short_trade_qty}")
                print(f"Initial long trade qty locked: {self.initial_max_long_trade_qty}")
                print(f"Initial short trade qty locked: {self.initial_max_short_trade_qty}")
                # debug_data = market_data
                # print(f"Debug market data: {debug_data}")

                # Calculate the dynamic amount
                long_dynamic_amount = 0.001 * self.initial_max_long_trade_qty
                short_dynamic_amount = 0.001 * self.initial_max_short_trade_qty

                min_qty = float(market_data["min_qty"])
                min_qty_str = str(min_qty)

                # Get the precision level of the minimum quantity
                if ".0" in min_qty_str:
                    # The minimum quantity does not have a fractional part, precision is 0
                    precision_level = 0
                else:
                    # The minimum quantity has a fractional part, get its precision level
                    precision_level = len(min_qty_str.split(".")[1])

                # Round the amount to the precision level of the minimum quantity
                long_dynamic_amount = round(long_dynamic_amount, precision_level)
                short_dynamic_amount = round(short_dynamic_amount, precision_level)

                print(f"Long dynamic amount: {long_dynamic_amount}")
                print(f"Short dynamic amount: {short_dynamic_amount}")

                self.check_amount_validity_once_bybit(long_dynamic_amount, symbol)
                self.check_amount_validity_once_bybit(short_dynamic_amount, symbol)

                # Check if the amount is less than the minimum quantity allowed by the exchange
                if long_dynamic_amount < min_qty:
                    print(f"Dynamic amount too small for 0.001x, using min_qty")
                    long_dynamic_amount = min_qty
                
                if short_dynamic_amount < min_qty:
                    print(f"Dynamic amount too small for 0.001x, using min_qty")
                    short_dynamic_amount = min_qty

                print(f"Min qty: {min_qty}")

                self.print_trade_quantities_once_bybit(self.max_long_trade_qty)
                self.print_trade_quantities_once_bybit(self.max_short_trade_qty)

                #self.exchange.debug_derivatives_markets_bybit()

                #print(f"Market data for {symbol}: {market_data}")

                #self.exchange.debug_derivatives_positions(symbol)

                # Get the 1-minute moving averages
                print(f"Fetching MA data")
                m_moving_averages = self.manager.get_1m_moving_averages(symbol)
                m5_moving_averages = self.manager.get_5m_moving_averages(symbol)
                ma_6_low = m_moving_averages["MA_6_L"]
                ma_3_low = m_moving_averages["MA_3_L"]
                ma_3_high = m_moving_averages["MA_3_H"]
                ma_1m_3_high = self.manager.get_1m_moving_averages(symbol)["MA_3_H"]
                ma_5m_3_high = self.manager.get_5m_moving_averages(symbol)["MA_3_H"]

                position_data = self.exchange.get_positions_bybit(symbol)

                #print(f"Bybit pos data: {position_data}")

                short_pos_qty = position_data["short"]["qty"]
                long_pos_qty = position_data["long"]["qty"]

                print(f"Short pos qty: {short_pos_qty}")
                print(f"Long pos qty: {long_pos_qty}")

                if long_pos_qty >= self.max_long_trade_qty:
                    self.max_long_trade_qty *= 2  # double the maximum long trade quantity
                    print(f"Long leverage temporarily increased to 2x")
                    self.long_leverage_increased = True
                elif long_pos_qty < self.max_long_trade_qty:
                    self.max_long_trade_qty = self.calc_max_trade_qty(total_equity,
                                                                    best_ask_price,
                                                                    max_leverage)
                    print(f"Long leverage returned to normal 1x")
                    self.long_leverage_increased = False

                if short_pos_qty >= self.max_short_trade_qty:
                    self.max_short_trade_qty *= 2  # double the maximum short trade quantity
                    print(f"Short leverage temporarily increased to 2x")
                    self.short_leverage_increased = True
                elif short_pos_qty < self.max_short_trade_qty:
                    self.max_short_trade_qty = self.calc_max_trade_qty(total_equity,
                                                                    best_ask_price,
                                                                    max_leverage)
                    print(f"Short leverage returned to normal 1x")
                    self.short_leverage_increased = False

                if self.long_leverage_increased:
                    print(f"Long position currently increased to 2x")
                else:
                    print(f"Long position currently at normal leverage 1x")
                
                if self.short_leverage_increased:
                    print(f"Short position currently increased to 2x")
                else:
                    print(f"Short position currently at normal leverage 1x")

                short_upnl = position_data["short"]["upnl"]
                long_upnl = position_data["long"]["upnl"]

                print(f"Short uPNL: {short_upnl}")
                print(f"Long uPNL: {long_upnl}")

                cum_realised_pnl_long = position_data["long"]["cum_realised"]
                cum_realised_pnl_short = position_data["short"]["cum_realised"]

                print(f"Short cum. PNL: {cum_realised_pnl_short}")
                print(f"Long cum. PNL: {cum_realised_pnl_long}")

                short_pos_price = position_data["short"]["price"] if short_pos_qty > 0 else None
                long_pos_price = position_data["long"]["price"] if long_pos_qty > 0 else None

                print(f"Long pos price {long_pos_price}")
                print(f"Short pos price {short_pos_price}")

                short_take_profit = None
                long_take_profit = None

                if five_minute_distance != previous_five_minute_distance:
                    short_take_profit = self.calculate_short_take_profit_spread_bybit(short_pos_price, symbol, five_minute_distance)
                    long_take_profit = self.calculate_long_take_profit_spread_bybit(long_pos_price, symbol, five_minute_distance)
                else:
                    if short_take_profit is None or long_take_profit is None:
                        short_take_profit = self.calculate_short_take_profit_spread_bybit(short_pos_price, symbol, five_minute_distance)
                        long_take_profit = self.calculate_long_take_profit_spread_bybit(long_pos_price, symbol, five_minute_distance)
                        
                previous_five_minute_distance = five_minute_distance

                print(f"Short TP: {short_take_profit}")
                print(f"Long TP: {long_take_profit}")

                should_short = self.short_trade_condition(best_bid_price, ma_3_high)
                should_long = self.long_trade_condition(best_bid_price, ma_3_high)

                should_add_to_short = False
                should_add_to_long = False
            
                if short_pos_price is not None:
                    should_add_to_short = short_pos_price < ma_6_low
                    short_tp_distance_percent = ((short_take_profit - short_pos_price) / short_pos_price) * 100
                    short_expected_profit_usdt = short_tp_distance_percent / 100 * short_pos_price * short_pos_qty
                    print(f"Short TP price: {short_take_profit}, TP distance in percent: {-short_tp_distance_percent:.2f}%, Expected profit: {-short_expected_profit_usdt:.2f} USDT")

                if long_pos_price is not None:
                    should_add_to_long = long_pos_price > ma_6_low
                    long_tp_distance_percent = ((long_take_profit - long_pos_price) / long_pos_price) * 100
                    long_expected_profit_usdt = long_tp_distance_percent / 100 * long_pos_price * long_pos_qty
                    print(f"Long TP price: {long_take_profit}, TP distance in percent: {long_tp_distance_percent:.2f}%, Expected profit: {long_expected_profit_usdt:.2f} USDT")

                print(f"Short condition: {should_short}")
                print(f"Long condition: {should_long}")
                print(f"Add short condition: {should_add_to_short}")
                print(f"Add long condition: {should_add_to_long}")

                if trend is not None and isinstance(trend, str):
                    if one_minute_volume is not None and five_minute_distance is not None:
                        if one_minute_volume > min_vol and five_minute_distance > min_dist:

                            if trend.lower() == "long" and should_long and long_pos_qty == 0:
                                print(f"Placing initial long entry")
                                self.limit_order_bybit(symbol, "buy", long_dynamic_amount, best_bid_price, positionIdx=1, reduceOnly=False)
                                print(f"Placed initial long entry")
                            else:
                                if trend.lower() == "long" and should_add_to_long and long_pos_qty < self.max_long_trade_qty and best_bid_price < long_pos_price:
                                    print(f"Placed additional long entry")
                                    self.limit_order_bybit(symbol, "buy", long_dynamic_amount, best_bid_price, positionIdx=1, reduceOnly=False)

                            if trend.lower() == "short" and should_short and short_pos_qty == 0:
                                print(f"Placing initial short entry")
                                self.limit_order_bybit(symbol, "sell", short_dynamic_amount, best_ask_price, positionIdx=2, reduceOnly=False)
                                print("Placed initial short entry")
                            else:
                                if trend.lower() == "short" and should_add_to_short and short_pos_qty < self.max_short_trade_qty and best_ask_price > short_pos_price:
                                    print(f"Placed additional short entry")
                                    self.limit_order_bybit(symbol, "sell", short_dynamic_amount, best_bid_price, positionIdx=2, reduceOnly=False)
            
                open_orders = self.exchange.get_open_orders(symbol)

                if long_pos_qty > 0 and long_take_profit is not None:
                    existing_long_tps = self.get_open_take_profit_order_quantities(open_orders, "sell")
                    total_existing_long_tp_qty = sum(qty for qty, _ in existing_long_tps)
                    print(f"Existing long TPs: {existing_long_tps}")
                    if not math.isclose(total_existing_long_tp_qty, long_pos_qty):
                        try:
                            for qty, existing_long_tp_id in existing_long_tps:
                                if not math.isclose(qty, long_pos_qty):
                                    self.exchange.cancel_take_profit_order_by_id(existing_long_tp_id, symbol)
                                    print(f"Long take profit {existing_long_tp_id} canceled")
                                    time.sleep(0.05)
                        except Exception as e:
                            print(f"Error in cancelling long TP orders {e}")

                    if len(existing_long_tps) < 1:
                        try:
                            self.exchange.create_take_profit_order_bybit(symbol, "limit", "sell", long_pos_qty, long_take_profit, positionIdx=1, reduce_only=True)
                            print(f"Long take profit set at {long_take_profit}")
                            time.sleep(0.05)
                        except Exception as e:
                            print(f"Error in placing long TP: {e}")

                if short_pos_qty > 0 and short_take_profit is not None:
                    existing_short_tps = self.get_open_take_profit_order_quantities(open_orders, "buy")
                    total_existing_short_tp_qty = sum(qty for qty, _ in existing_short_tps)
                    print(f"Existing short TPs: {existing_short_tps}")
                    if not math.isclose(total_existing_short_tp_qty, short_pos_qty):
                        try:
                            for qty, existing_short_tp_id in existing_short_tps:
                                if not math.isclose(qty, short_pos_qty):
                                    self.exchange.cancel_take_profit_order_by_id(existing_short_tp_id, symbol)
                                    print(f"Short take profit {existing_short_tp_id} canceled")
                                    time.sleep(0.05)
                        except Exception as e:
                            print(f"Error in cancelling short TP orders: {e}")

                    if len(existing_short_tps) < 1:
                        try:
                            self.exchange.create_take_profit_order_bybit(symbol, "limit", "buy", short_pos_qty, short_take_profit, positionIdx=2, reduce_only=True)
                            print(f"Short take profit set at {short_take_profit}")
                            time.sleep(0.05)
                        except Exception as e:
                            print(f"Error in placing short TP: {e}")

                # Cancel entries
                current_time = time.time()
                if current_time - self.last_cancel_time >= 60:  # Execute this block every 1 minute
                    try:
                        if best_ask_price < ma_1m_3_high or best_ask_price < ma_5m_3_high:
                            self.exchange.cancel_all_entries_bybit(symbol)
                            print(f"Canceled entry orders for {symbol}")
                            time.sleep(0.05)
                    except Exception as e:
                        print(f"An error occurred while canceling entry orders: {e}")

                    self.last_cancel_time = current_time  # Update the last cancel time

                # Clear all rows from the table to avoid growing the table indefinitely
                table.rows.clear()

                # Add key-value pairs to the table
                table.add_row("1m Volume", str(one_minute_volume))
                table.add_row("1m Spread", str(one_minute_distance))
                table.add_row("5m Spread", str(five_minute_distance))
                table.add_row("30m Spread", str(thirty_minute_distance))
                table.add_row("1h Spread", str(one_hour_distance))
                table.add_row("4h Spread", str(four_hour_distance))
                table.add_row("Trend", str(trend))

                time.sleep(2)  # Adjust sleep time as needed
                # time.sleep(2)
                time.sleep(30)

                # # Clear all rows from the table to avoid growing the table indefinitely
                # table.rows.clear()

                # # Add key-value pairs to the table
                # timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                # table.add_row("Timestamp", timestamp)
                # table.add_row("Symbol", str(symbol))
                # table.add_row("Total Equity", str(total_equity))
                # table.add_row("Available Equity", str(available_equity))
                # table.add_row("Current Price", str(current_price))
                # table.add_row("Best Bid", str(best_bid_price))
                # table.add_row("Best Ask", str(best_ask_price))
                # table.add_row("Max Long Trade Quantity", str(self.max_long_trade_qty))
                # table.add_row("Initial Max Long Trade Quantity", str(self.initial_max_long_trade_qty))
                # table.add_row("Max Trade Quantity", str(long_dynamic_amount))
                # table.add_row("Long Position Quantity", str(long_pos_qty))
                # table.add_row("Long uPNL", str(long_upnl))
                # table.add_row("Long Cumulative PNL", str(cum_realised_pnl_long))
                # table.add_row("Long Position Price", str(long_pos_price))
                # table.add_row("Long Take Profit", str(long_take_profit))
                # table.add_row("Should Long", str(should_long))
                # table.add_row("Should Add to Long", str(should_add_to_long))
