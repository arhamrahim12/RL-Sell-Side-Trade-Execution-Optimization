# -*- coding: utf-8 -*-
"""

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1jMp3BRNAxTFpVe96EqMF-tl90dtKksPj
"""

!pip install stable-baselines3[extra] pandas numpy

import pandas as pd
import numpy as np

# loading the data file
data = pd.read_csv('AAPL_Quotes_Data.csv')

# calculate vwap and add it into our DataFrame
data['vwap_price'] = (data['bid_price_1'] * data['bid_size_1']).cumsum() / data['bid_size_1'].cumsum()

# just check if 'vwap_price' was added right
print("Data columns after VWAP calculation:", data.columns)
print(data[['bid_price_1', 'bid_size_1', 'vwap_price']].head())  # check that 'vwap_price' is there

class Benchmark:
    def __init__(self, data):
        self.data = data

    def get_twap_trades(self, data, initial_inventory, preferred_timeframe=390):
        total_steps = len(data)
        twap_shares_per_step = initial_inventory / preferred_timeframe
        remaining_inventory = initial_inventory
        trades = []
        for step in range(min(total_steps, preferred_timeframe)):
            size_of_slice = min(twap_shares_per_step, remaining_inventory)
            remaining_inventory -= int(np.ceil(size_of_slice))
            trade = {
                'timestamp': data.iloc[step]['timestamp'],
                'step': step,
                'price': data.iloc[step]['bid_price_1'],
                'shares': size_of_slice,
                'inventory': remaining_inventory,
            }
            trades.append(trade)
        return pd.DataFrame(trades)

    def get_vwap_trades(self, data, initial_inventory, preferred_timeframe=390):
        total_volume = data['ask_size_1'].sum()
        total_steps = len(data)
        remaining_inventory = initial_inventory
        trades = []
        for step in range(min(total_steps, preferred_timeframe)):
            volume_at_step = data['ask_size_1'].iloc[step]
            size_of_slice = (volume_at_step / total_volume) * initial_inventory
            size_of_slice = min(size_of_slice, remaining_inventory)
            remaining_inventory -= int(np.ceil(size_of_slice))
            trade = {
                'timestamp': data.iloc[step]['timestamp'],
                'step': step,
                'price': data.iloc[step]['bid_price_1'],
                'shares': size_of_slice,
                'inventory': remaining_inventory,
            }
            trades.append(trade)
        return pd.DataFrame(trades)

    def calculate_vwap(self, idx, shares):
        bid_prices = [self.data[f'bid_price_{i}'][idx] for i in range(1,6)]
        bid_sizes = [self.data[f'bid_size_{i}'][idx] for i in range(1,6)]
        cumsum = 0
        for i, size in enumerate(bid_sizes):
            cumsum += size
            if cumsum >= shares:
                break
        return np.sum(np.array(bid_prices[:i+1]) * np.array(bid_sizes[:i+1])) / np.sum(bid_sizes[:i+1])

    def compute_components(self, alpha, shares, idx):
        actual_price = self.calculate_vwap(idx, shares)
        slippage = (self.data['bid_price_1'][idx] - actual_price) * shares
        market_impact = alpha * np.sqrt(shares)
        return np.array([slippage, market_impact])

# total shares to be sold
initial_inventory = 1000

# benchmark class instance
benchmark = Benchmark(data)

# genrate TWAP trades schedule
twap_trades = benchmark.get_twap_trades(data, initial_inventory)
print("TWAP Trades:")
print(twap_trades.head())

# generate VWAP trades schedule
vwap_trades = benchmark.get_vwap_trades(data, initial_inventory)
print("\nVWAP Trades:")
print(vwap_trades.head())

# JSON Export of TWAP and VWAP trades
twap_json = twap_trades.to_json(orient='records', date_format='iso')
vwap_json = vwap_trades.to_json(orient='records', date_format='iso')

# Save JSON files
with open('twap_trades.json', 'w') as twap_file:
    twap_file.write(twap_json)

with open('vwap_trades.json', 'w') as vwap_file:
    vwap_file.write(vwap_json)

# Additionally, print JSON data to console if needed
print("TWAP JSON Output:", twap_json)
print("\nVWAP JSON Output:", vwap_json)

# helper func to simulate strategy & calc costs
def simulate_strategy(benchmark, trades):
    slippage, market_impact = [], []
    alpha = 4.439584265535017e-06  # scaling factor for market impact
    for idx in range(len(trades)):
        shares = trades.iloc[idx]['shares']
        reward = benchmark.compute_components(alpha, shares, idx)
        slippage.append(reward[0])
        market_impact.append(reward[1])
    return slippage, market_impact

# simulate TWAP and VWAP strategies
twap_slippage, twap_market_impact = simulate_strategy(benchmark, twap_trades)
vwap_slippage, vwap_market_impact = simulate_strategy(benchmark, vwap_trades)

# calculate total costs for TWAP and VWAP
total_twap_cost = sum(twap_slippage) + sum(twap_market_impact)
total_vwap_cost = sum(vwap_slippage) + sum(vwap_market_impact)

print(f"Total TWAP Cost: {total_twap_cost}")
print(f"Total VWAP Cost: {total_vwap_cost}")

# RL Model
!pip install gym
!pip install gymnasium

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd

class TradingEnv(gym.Env):
    def __init__(self, data, initial_inventory=1000, alpha=4.439584265535017e-06):
        super(TradingEnv, self).__init__()

        self.data = data
        self.initial_inventory = initial_inventory
        self.alpha = alpha

        # action space: [fraction of inventory to sell, limit price adjustment factor]
        self.action_space = spaces.Box(low=np.array([0, 0]), high=np.array([1, 1]), dtype=np.float32)

        # obsrvation space includes normalized inventory, time, bid price, bid size, and VWAP
        self.observation_space = spaces.Box(
            low=np.array([0, 0, 0, 0, 0], dtype=np.float32),
            high=np.array([1, 1, np.inf, np.inf, np.inf], dtype=np.float32)
        )

        # calc normalization factors for price & size
        self.max_price = data['bid_price_1'].max()
        self.max_size = data['bid_size_1'].max()

        # keep unfilled limit orders & trade schedule
        self.unfilled_limit_orders = []
        self.trade_schedule = []

        self.reset()

    def reset(self, seed=None, options=None):
        if seed is not None:
            np.random.seed(seed)

        self.remaining_inventory = float(self.initial_inventory)
        self.current_step = 0
        self.trade_schedule = []
        self.unfilled_limit_orders = []  # reset unfilled orders every time

        return self._get_observation(), {}

    def _get_observation(self):
        if self.current_step >= len(self.data):
            return np.array([0, 0, 0, 0, 0], dtype=np.float32)

        # normalize stuff including VWAP
        norm_inventory = self.remaining_inventory / self.initial_inventory
        norm_time = (390 - self.current_step) / 390
        norm_price = self.data['bid_price_1'].iloc[self.current_step] / self.max_price
        norm_size = self.data['bid_size_1'].iloc[self.current_step] / self.max_size
        norm_vwap = self.data['vwap_price'].iloc[self.current_step] / self.max_price

        return np.array([
            norm_inventory,
            norm_time,
            norm_price,
            norm_size,
            norm_vwap
        ], dtype=np.float32)

    def _calculate_vwap(self, idx, shares):
        # calc VWAP based on bid prices & sizes
        bid_prices = [self.data[f'bid_price_{i}'].iloc[idx] for i in range(1, 6)]
        bid_sizes = [self.data[f'bid_size_{i}'].iloc[idx] for i in range(1, 6)]

        total_volume = 0
        weighted_price = 0
        shares_left = shares

        for price, size in zip(bid_prices, bid_sizes):
            volume = min(size, shares_left)
            weighted_price += price * volume
            total_volume += volume
            shares_left -= volume

            if shares_left <= 0:
                break

        return weighted_price / total_volume if total_volume > 0 else bid_prices[0]

    def step(self, action):
        # extract fraction of inventory to sell and limit price adjustment factor
        fraction_to_sell = action[0]
        limit_adjustment_factor = action[1]

        # calc shares to sell
        remaining_time = max(1, 390 - self.current_step)
        max_shares_per_step = self.remaining_inventory / remaining_time
        shares_to_sell = np.clip(fraction_to_sell * max_shares_per_step, 0, self.remaining_inventory)

        # init a default trade price to avoid UnboundLocalError
        trade_price = self.data['bid_price_1'].iloc[self.current_step]
        limit_price = self.data['bid_price_1'].iloc[self.current_step] * (1 + limit_adjustment_factor * 0.001)
        filled = False  # track if limit order fills

        # try to execute limit order if limit price is <= current bid price
        if limit_price <= self.data['bid_price_1'].iloc[self.current_step]:
            trade_price = limit_price
            filled = True
        else:
            # track unfilled limit order details
            self.unfilled_limit_orders.append({
                'step': self.current_step,
                'limit_price': limit_price,
                'shares': shares_to_sell,
                'remaining_inventory': self.remaining_inventory
            })

        # if limit order is not filled by last step, execute as market order
        if not filled and self.current_step == len(self.data) - 1:
            trade_price = self.data['bid_price_1'].iloc[self.current_step]  # execute at market price
            filled = True

        # calculate transaction costs, slippage, and market impact
        actual_price = self._calculate_vwap(self.current_step, shares_to_sell)
        slippage = (trade_price - actual_price) * shares_to_sell
        market_impact = self.alpha * np.sqrt(shares_to_sell)

        # if slippage too high: reduce order size if slippage > threshold
        slippage_threshold = 0.05 * self.data['bid_price_1'].iloc[self.current_step]
        if slippage > slippage_threshold:
            shares_to_sell *= 0.8  # reduce size by 20%
            trade_price = self.data['bid_price_1'].iloc[self.current_step]  # market order if converting from limit

        # record transaction in trade schedule
        self.remaining_inventory -= shares_to_sell
        self.trade_schedule.append({
            'step': self.current_step,
            'timestamp': self.data['timestamp'].iloc[self.current_step],
            'shares': float(shares_to_sell),
            'price': float(trade_price),
            'remaining_inventory': float(self.remaining_inventory),
            'limit_price': float(limit_price) if not filled else None,
            'slippage': float(slippage),
            'market_impact': float(market_impact)
        })

        # calc reward
        transaction_cost = -(slippage + market_impact)
        time_penalty = -0.1 * (self.remaining_inventory / self.initial_inventory) * (1 - self.current_step / 390)
        reward = transaction_cost + time_penalty if filled else -0.1  # penalty for unfilled trades

        # increment step
        self.current_step += 1

        # terminal conditions
        done = (self.current_step >= len(self.data)) or (self.remaining_inventory <= 0)
        truncated = False

        return self._get_observation(), float(reward), done, truncated, {
            'shares_sold': shares_to_sell,
            'remaining_inventory': self.remaining_inventory
        }
