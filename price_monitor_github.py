from web3 import Web3
import time
import json
import os
from datetime import datetime, timezone, timedelta

RPC_URL = "https://base.llamarpc.com"
w3 = Web3(Web3.HTTPProvider(RPC_URL))
Q192 = 2 ** 192

# File to store the data (GitHub Actions compatible)
LOCAL_DATA_FILE = "price_data_bwork_mainnetv2.json"
MAX_DATA_POINTS = 4 * 30 + 1  # 30 days worth of 4 daily intervals + 1 Current Price

# Define the target times for data collection (in UTC)
TARGET_HOURS = [0, 6, 12, 18]  # Midnight, 6am, noon, 6pm

def save_data(timestamps, blocks, prices):
    """Save the arrays to JSON file"""
    data = {
        "timestamps": timestamps,
        "blocks": blocks,
        "prices": prices,
        "last_updated": time.time()
    }
    
    try:
        with open(LOCAL_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Data saved to {LOCAL_DATA_FILE}")
    except Exception as e:
        print(f"Error saving file: {e}")

def load_data():
    """Load the arrays from JSON file"""
    possible_paths = [
        f"mainnetB0x/{LOCAL_DATA_FILE}",
        LOCAL_DATA_FILE
    ]
    
    file_path = None
    for path in possible_paths:
        if os.path.exists(path):
            file_path = path
            print(f"Found existing file at: {path}")
            break
    
    if not file_path:
        print(f"No existing save file found, starting fresh")
        return [], [], []
        
    try:
        with open(file_path, 'r') as f:
            content = f.read().strip()
            if not content:
                print(f"File {file_path} is empty, starting fresh")
                return [], [], []
            data = json.loads(content)
        
        timestamps = data.get("timestamps", [])
        blocks = data.get("blocks", [])
        prices = data.get("prices", [])
        last_updated = data.get("last_updated", 0)
        
        print(f"Loaded {len(timestamps)} data points from {file_path}")
        if last_updated > 0:
            last_updated_dt = datetime.fromtimestamp(last_updated, tz=timezone.utc)
            print(f"Last updated: {last_updated_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        return timestamps, blocks, prices
        
    except Exception as e:
        print(f"Error loading data file: {e}")
        print("Starting fresh...")
        return [], [], []

def is_target_time(timestamp, tolerance_minutes=30):
    """Check if a timestamp is close to a target time"""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    hour = dt.hour
    minute = dt.minute
    
    for target_hour in TARGET_HOURS:
        minutes_from_target = abs((hour * 60 + minute) - (target_hour * 60))
        minutes_from_target = min(minutes_from_target, 24 * 60 - minutes_from_target)
        
        if minutes_from_target <= tolerance_minutes:
            return True
    
    return False

def get_exact_target_timestamp(timestamp):
    """Get the exact target timestamp this timestamp is closest to"""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    date_only = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    target_times = []
    for hour in TARGET_HOURS:
        target_dt = date_only.replace(hour=hour)
        target_times.append((int(target_dt.timestamp()), hour))
    
    next_day_midnight = date_only + timedelta(days=1)
    target_times.append((int(next_day_midnight.timestamp()), 0))
    
    closest_target = None
    min_distance = float('inf')
    
    for target_ts, target_hour in target_times:
        distance = abs(timestamp - target_ts)
        if distance < min_distance:
            min_distance = distance
            closest_target = target_ts
    
    return closest_target

def comprehensive_data_cleanup_with_dedup(timestamps, blocks, prices):
    """Cleanup with deduplication"""
    if not timestamps:
        print("No data to clean")
        return timestamps, blocks, prices
    
    print(f"Starting cleanup with deduplication: {len(timestamps)} data points")
    
    target_groups = {}
    non_target_data = []
    
    for i in range(len(timestamps)):
        data_point = {
            'timestamp': timestamps[i],
            'block': blocks[i],
            'price': prices[i],
            'index': i
        }
        
        if is_target_time(timestamps[i], tolerance_minutes=30):
            exact_target = get_exact_target_timestamp(timestamps[i])
            
            if exact_target not in target_groups:
                target_groups[exact_target] = []
            target_groups[exact_target].append(data_point)
        else:
            non_target_data.append(data_point)
    
    print(f"Found {len(target_groups)} unique target times")
    print(f"Found {len(non_target_data)} non-target data points")
    
    deduplicated_targets = []
    total_removed = 0
    
    for exact_target, data_points in target_groups.items():
        if len(data_points) == 1:
            deduplicated_targets.append(data_points[0])
        else:
            closest_point = None
            min_distance = float('inf')
            
            for dp in data_points:
                distance = abs(dp['timestamp'] - exact_target)
                if distance < min_distance:
                    min_distance = distance
                    closest_point = dp
            
            deduplicated_targets.append(closest_point)
            removed_count = len(data_points) - 1
            total_removed += removed_count
    
    print(f"Removed {total_removed} duplicate target time data points")
    
    deduplicated_targets.sort(key=lambda x: x['timestamp'])
    non_target_data.sort(key=lambda x: x['timestamp'])
    
    cleaned_timestamps = [dp['timestamp'] for dp in deduplicated_targets]
    cleaned_blocks = [dp['block'] for dp in deduplicated_targets]
    cleaned_prices = [dp['price'] for dp in deduplicated_targets]
    
    if non_target_data:
        most_recent = non_target_data[-1]
        recent_dt = datetime.fromtimestamp(most_recent['timestamp'], tz=timezone.utc)
        print(f"Keeping most recent current price: {recent_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        cleaned_timestamps.append(most_recent['timestamp'])
        cleaned_blocks.append(most_recent['block'])
        cleaned_prices.append(most_recent['price'])
        
        if len(non_target_data) > 1:
            print(f"Removed {len(non_target_data) - 1} old current price points")
    
    combined_data = list(zip(cleaned_timestamps, cleaned_blocks, cleaned_prices))
    combined_data.sort(key=lambda x: x[0])
    
    final_timestamps = [x[0] for x in combined_data]
    final_blocks = [x[1] for x in combined_data]
    final_prices = [x[2] for x in combined_data]
    
    print(f"Cleanup complete: {len(final_timestamps)} data points remaining")
    
    return final_timestamps, final_blocks, final_prices

def get_storage_with_retry(address, slot, block, retries=5, base_delay=6):
    """Get storage at with retry logic with exponential backoff"""
    attempt = 0
    while attempt < retries:
        try:
            time.sleep(3)  # 3 second delay before each RPC call
            data = w3.eth.get_storage_at(address, slot, block_identifier=block)
            bytes32_hex = "0x" + data.hex().rjust(64, "0")
            return int.from_bytes(data, "big")
        except Exception as e:
            print(f"Retry {attempt+1}/{retries} failed: {e}")
            attempt += 1
            if attempt < retries:
                retry_delay = base_delay * (2 ** attempt)
                print(f"Waiting {retry_delay} seconds before retry...")
                time.sleep(retry_delay)
    raise RuntimeError(f"Failed to fetch storage slot {slot} after {retries} retries")

def unpack_slot0(packed):
    """Unpack slot0 data"""
    sqrtPriceX96 = packed & ((1 << 160) - 1)
    tick = (packed >> 160) & ((1 << 24) - 1)
    if tick & (1 << 23):
        tick -= (1 << 24)
    protocolFee = (packed >> 184) & ((1 << 24) - 1)
    lpFee = (packed >> 208) & ((1 << 24) - 1)
    return sqrtPriceX96, tick, protocolFee, lpFee

def sqrtPriceX96_to_price(sq):
    """Convert sqrtPriceX96 to price"""
    return (sq ** 2) / Q192

def getSlot0(block):
    """Get price data for a specific block"""
    print(f"\n--- Fetching data for block {block} ---")
    
    pool_manager = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
    
    # BWORK/WETH POOL
    pool_slot = '0x6d7608e5974f1aa1bc8ac9b33ae7fdd41a55b24f53007a7f5ed41ee5b15fb194'
    packed = get_storage_with_retry(pool_manager, pool_slot, block)
    sqrtPriceX96, tick, protocolFee, lpFee = unpack_slot0(packed)
    price = sqrtPriceX96_to_price(sqrtPriceX96)
    print(f"BWORK/WETH - Price: {price}")
    
    # Check if BWORK/WETH price is zero or too small
    if price == 0 or price < 1e-18:
        print(f"WARNING: BWORK/WETH price is zero or too small at block {block}")
        actual_price = 0
        return actual_price
    
    # WETH/USD POOL
    pool_slot = '0xe570f6e770bf85faa3d1dbee2fa168b56036a048a7939edbcd02d7ebddf3f948'
    packed = get_storage_with_retry(pool_manager, pool_slot, block)
    sqrtPriceX96, tick, protocolFee, lpFee = unpack_slot0(packed)
    price2 = sqrtPriceX96_to_price(sqrtPriceX96) * 10**12
    print(f"WETH/USD - Price: {price2}")
    
    actual_price = price2 * (1/price)
    print(f"Actual Price of BWORK: {actual_price}")
    return actual_price

def get_current_block_and_timestamp():
    """Get the current block number and timestamp"""
    try:
        time.sleep(3)
        current_block = w3.eth.block_number
        time.sleep(3)
        block_data = w3.eth.get_block(current_block)
        current_timestamp = block_data["timestamp"]
        return current_block, current_timestamp
    except Exception as e:
        print(f"Error getting current block: {e}")
        return None, None

def estimate_block_from_timestamp(target_timestamp, current_block, current_timestamp):
    """Estimate block number from timestamp"""
    try:
        blocks_24h_ago_estimate = int((24 * 60 * 60) / 2)
        sample_block_24h_ago = max(1, current_block - blocks_24h_ago_estimate)
        
        time.sleep(3)
        sample_block_data = w3.eth.get_block(sample_block_24h_ago)
        sample_timestamp_24h_ago = sample_block_data["timestamp"]
        
        actual_time_diff = current_timestamp - sample_timestamp_24h_ago
        actual_block_diff = current_block - sample_block_24h_ago
        
        if actual_block_diff > 0 and actual_time_diff > 0:
            seconds_per_block = actual_time_diff / actual_block_diff
            print(f"Calculated seconds per block: {seconds_per_block:.3f}")
        else:
            seconds_per_block = 2.0
            print("Using fallback: 2 seconds per block")
        
        time_diff = current_timestamp - target_timestamp
        blocks_diff = int(time_diff / seconds_per_block)
        estimated_block = current_block - blocks_diff
        
        return max(1, estimated_block)
        
    except Exception as e:
        print(f"Error calculating block: {e}")
        time_diff = current_timestamp - target_timestamp
        blocks_diff = int(time_diff / 2)
        estimated_block = current_block - blocks_diff
        return max(1, estimated_block)

def get_target_timestamps_for_day(day_timestamp):
    """Get the 4 target timestamps for a given day"""
    dt = datetime.fromtimestamp(day_timestamp, tz=timezone.utc)
    start_of_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    target_timestamps = []
    for hour in TARGET_HOURS:
        target_dt = start_of_day.replace(hour=hour)
        target_timestamps.append(int(target_dt.timestamp()))
    
    return target_timestamps

def get_30_day_date_range(current_timestamp):
    """Get the consistent 30-day date range"""
    current_dt = datetime.fromtimestamp(current_timestamp, tz=timezone.utc)
    end_date = current_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=29)
    
    return start_date, end_date

def get_missing_timestamps(timestamps, current_timestamp, target_days=30):
    """Find all missing target timestamps for exactly 30 days"""
    existing_target_times = set()
    for ts in timestamps:
        if is_target_time(ts):
            existing_target_times.add(ts)
    
    missing_timestamps = []
    start_date, end_date = get_30_day_date_range(current_timestamp)
    
    print(f"Collecting data for {target_days} complete days:")
    print(f"From: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    current_date = start_date
    total_days = 0
    while current_date <= end_date:
        total_days += 1
        target_day_timestamp = int(current_date.timestamp())
        target_timestamps = get_target_timestamps_for_day(target_day_timestamp)
        
        for target_ts in target_timestamps:
            if target_ts < current_timestamp and target_ts not in existing_target_times:
                found_close = False
                for existing_ts in existing_target_times:
                    if abs(existing_ts - target_ts) < 30 * 60:
                        found_close = True
                        break
                
                if not found_close:
                    missing_timestamps.append(target_ts)
        
        current_date += timedelta(days=1)
    
    missing_timestamps.sort()
    print(f"Total days in range: {total_days} (should be 30)")
    print(f"Expected target points: {total_days * 4}")
    
    return missing_timestamps

def collect_historical_data(timestamps, blocks, prices, target_days=30, max_collect=60):
    """Collect historical data for missing target times, limited to max_collect points per run"""
    current_block, current_timestamp = get_current_block_and_timestamp()
    if current_block is None:
        return timestamps, blocks, prices
    
    # Hardcoded timestamp - data is only trustworthy from this point forward
    trustworthy_data_start = 1759276801
    
    all_missing_timestamps = get_missing_timestamps(timestamps, current_timestamp, target_days)
    
    if not all_missing_timestamps:
        print("No missing historical data points found")
        return timestamps, blocks, prices
    
    points_to_collect = min(len(all_missing_timestamps), max_collect)
    missing_timestamps = all_missing_timestamps[:points_to_collect]
    
    print(f"Total missing data points: {len(all_missing_timestamps)}")
    print(f"Collecting up to {max_collect} data points in this run")
    print(f"Will attempt to collect {points_to_collect} timestamps")
    
    skipped_count = 0
    collected_count = 0
    
    for i, target_timestamp in enumerate(missing_timestamps):
        if collected_count >= max_collect:
            print(f"\nReached maximum collection limit of {max_collect} data points")
            break
            
        try:
            estimated_block = estimate_block_from_timestamp(target_timestamp, current_block, current_timestamp)
            
            time.sleep(3)
            block_data = w3.eth.get_block(estimated_block)
            actual_timestamp = block_data["timestamp"]
            
            attempts = 0
            while abs(actual_timestamp - target_timestamp) > 30 * 60 and attempts < 10:
                if actual_timestamp < target_timestamp:
                    estimated_block += int((target_timestamp - actual_timestamp) / 2)
                else:
                    estimated_block -= int((actual_timestamp - target_timestamp) / 2)
                
                time.sleep(3)
                block_data = w3.eth.get_block(estimated_block)
                actual_timestamp = block_data["timestamp"]
                attempts += 1
            
            target_dt = datetime.fromtimestamp(target_timestamp, tz=timezone.utc)
            actual_dt = datetime.fromtimestamp(actual_timestamp, tz=timezone.utc)
            
            print(f"\nAttempting {i+1}/{points_to_collect} (Collected so far: {collected_count}/{max_collect})")
            print(f"  Block {estimated_block}")
            print(f"  Target: {target_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"  Actual: {actual_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            try:
                # Check if timestamp is before trustworthy data period
                if actual_timestamp < trustworthy_data_start:
                    price = 0.0077777777777
                    print(f"  ⚠ Before trustworthy data period - setting price to 0")
                else:
                    price = getSlot0(estimated_block)
                
                insert_pos = 0
                for j, existing_ts in enumerate(timestamps):
                    if actual_timestamp > existing_ts:
                        insert_pos = j + 1
                    else:
                        break
                
                timestamps.insert(insert_pos, actual_timestamp)
                blocks.insert(insert_pos, estimated_block)
                prices.insert(insert_pos, price)
                collected_count += 1
                print(f"  ✓ Successfully collected (total: {collected_count})")
                
                if (collected_count) % 10 == 0:
                    save_data(timestamps, blocks, prices)
                    print(f"  Progress saved: {collected_count} collected, {skipped_count} skipped")
                
            except ValueError as ve:
                print(f"  SKIPPED: {ve}")
                skipped_count += 1
            
            time.sleep(1)
            
        except Exception as e:
            print(f"Error collecting historical data point {i+1}: {e}")
            skipped_count += 1
            continue
    
    print(f"\n{'='*60}")
    print(f"Historical data collection complete for this run!")
    print(f"  Successfully collected: {collected_count} data points")
    print(f"  Skipped: {skipped_count} data points (pool not initialized)")
    
    remaining_missing = len(get_missing_timestamps(timestamps, current_timestamp, target_days))
    if remaining_missing > 0:
        print(f"  Still remaining: {remaining_missing} data points to collect in future runs")
    else:
        print(f"  ✓ All historical data collected!")
    print(f"{'='*60}")
    
    return timestamps, blocks, prices

def enforce_30_day_limit(timestamps, blocks, prices):
    """Ensure we keep exactly 30 complete days of target data + current price"""
    if not timestamps:
        return timestamps, blocks, prices
    
    current_time = time.time()
    start_date, end_date = get_30_day_date_range(current_time)
    cutoff_timestamp = int(start_date.timestamp())
    
    print(f"Enforcing 30-day limit: keeping data from {start_date.strftime('%Y-%m-%d')} onwards")
    
    indices_to_remove = []
    for i, ts in enumerate(timestamps):
        if is_target_time(ts) and ts < cutoff_timestamp:
            indices_to_remove.append(i)
    
    for i in reversed(indices_to_remove):
        timestamps.pop(i)
        blocks.pop(i)
        prices.pop(i)
    
    if indices_to_remove:
        print(f"Removed {len(indices_to_remove)} data points older than 30 days")
    
    return timestamps, blocks, prices

def update_current_price(timestamps, blocks, prices, current_timestamp, current_block, current_price):
    """Update or add the current price data point"""
    indices_to_remove = []
    for i in range(len(timestamps)):
        if not is_target_time(timestamps[i]):
            indices_to_remove.append(i)
    
    for i in reversed(indices_to_remove):
        timestamps.pop(i)
        blocks.pop(i)
        prices.pop(i)
    
    if indices_to_remove:
        print(f"Removed {len(indices_to_remove)} old current price points")
    
    timestamps.append(current_timestamp)
    blocks.append(current_block)
    prices.append(current_price)
    
    print("Added new current price point")
    
    return timestamps, blocks, prices

def main():
    """Main execution function for GitHub Actions"""
    print("="*60)
    print("Base B0x Price Monitor - GitHub Actions Edition")
    print("="*60)
    
    ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = load_data()
    print("Data loaded")
    
    ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = enforce_30_day_limit(
        ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices
    )
    
    ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = comprehensive_data_cleanup_with_dedup(
        ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices
    )
    
    current_block, current_timestamp = get_current_block_and_timestamp()
    if current_block is None:
        print("Failed to get current block info, exiting")
        return
    
    print(f"\nCurrent block: {current_block}")
    current_dt = datetime.fromtimestamp(current_timestamp, tz=timezone.utc)
    print(f"Current time: {current_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    print("\nChecking for missing historical data...")
    ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = collect_historical_data(
        ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices, target_days=30
    )
    
    ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = enforce_30_day_limit(
        ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices
    )
    
    print("\nGetting current price...")
    try:
        current_price = getSlot0(current_block)
        print(f"Current price: ${current_price:.6f}")
        
        ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices = update_current_price(
            ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices,
            current_timestamp, current_block, current_price
        )
        
    except Exception as e:
        print(f"Error getting current price: {e}")
    
    save_data(ArrayOfTimestamps, ArrayOfBlocksSearched, ArrayOfActualPrices)
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total data points: {len(ArrayOfTimestamps)}")
    target_count = sum(1 for ts in ArrayOfTimestamps if is_target_time(ts))
    current_count = len(ArrayOfTimestamps) - target_count
    print(f"Target time points: {target_count}")
    print(f"Current price points: {current_count}")
    
    if ArrayOfActualPrices:
        print(f"Latest price: ${ArrayOfActualPrices[-1]:.6f}")
    
    print("="*60)
    print("Price monitoring complete!")

if __name__ == "__main__":
    main()
