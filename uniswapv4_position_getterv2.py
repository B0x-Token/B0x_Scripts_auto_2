import json
import time
import requests
import os
import random
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from web3 import Web3
from eth_abi import encode as abi_encode
from eth_abi import decode as abi_decode
from datetime import datetime


CONFIG = {
    "RPC_URL": "https://base.gateway.tenderly.co",
    "POOL_MANAGER_ADDRESS": "0x498581ff718922c3f8e6a244956af099b2652b2b",
    "TOKEN_OWNER_CHECKER": "0x94b1A7bE1df147DbeEbC6b06de577CcFeD9Dc052",
    "NFT_CONTRACT": "0x7C5f5A4bBd8fD63184577525326123B519429bDc",
    "MODIFY_LIQUIDITY_TOPIC": "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec",
    "MODIFY_LIQUIDITY_TOPIC2": "0xa2da1740db0db2cf3059413cc2b1ad1185d311ee69bbce1720459eea7c9e4bea",
    "BLOCK_RANGE_SIZE": 1000,
    "OWNER_CHECK_BATCH_SIZE": 100,   # hard max requested: 100 tokenIds per TOKEN_OWNER_CHECKER call
    "SLEEP_BETWEEN_BATCHES": 0.6,    # seconds between block-range scan batches
}

# Confirmed signature: getOwnersSafe(address nftContract, uint256[] tokenIds) -> address[]
TOKEN_OWNER_CHECKER_FUNCTION_SIG = "getOwnersSafe(address,uint256[])"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass
class PoolKey:
    currency0: str
    currency1: str
    fee: int
    tickSpacing: int
    hooks: str


@dataclass
class Position:
    token_id: int
    pool_key: PoolKey
    owner: str
    block_number: int
    tx_hash: str
    tick_lower: int
    tick_upper: int
    timestamp: str


class UniswapV4Monitor:
    def __init__(self, rpc_url: str, start_block: int,
                 save_file: str = "uniswap_v4_data.json",
                 save_file_server: str = "/var/www/html/data.bzerox.org/mainnet/mainnet_uniswap_v4_data.json"):
        self.rpc_url = rpc_url
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        self.start_block = start_block
        self.current_block = start_block
        self.save_file = save_file
        self.save_file_server = save_file_server

        self.max_retries = 5
        self.base_retry_delay = 1.0
        self.max_retry_delay = 60.0

        self.pool_manager_address = Web3.to_checksum_address(CONFIG["POOL_MANAGER_ADDRESS"])
        self.token_owner_checker_address = Web3.to_checksum_address(CONFIG["TOKEN_OWNER_CHECKER"])
        self.nft_address = Web3.to_checksum_address(CONFIG["NFT_CONTRACT"])
        self.modify_liquidity_topic = CONFIG["MODIFY_LIQUIDITY_TOPIC"]
        self.target_pool_id = CONFIG["MODIFY_LIQUIDITY_TOPIC2"]
        self.block_range_size = CONFIG["BLOCK_RANGE_SIZE"]
        self.owner_check_batch_size = min(CONFIG["OWNER_CHECK_BATCH_SIZE"], 100)  # hard cap: 100
        self.sleep_between_batches = CONFIG["SLEEP_BETWEEN_BATCHES"]

        self.target_pool_key = PoolKey(
            currency0="0x6B19E31C1813cD00b0d47d798601414b79A3e8AD",
            currency1="0xc4D4FD4F4459730d176844c170F2bB323c87Eb3B",
            fee=8388608,
            tickSpacing=60,
            hooks="0x785319f8fCE23Cd733DE94Fd7f34b74A5cAa1000"
        )

        # token_id -> Position
        self.positions: Dict[int, Position] = {}

        self.load_data()

        print("Initialized Uniswap V4 Monitor")
        print(f"Starting from block: {self.current_block}")
        print(f"PoolManager Contract: {self.pool_manager_address}")
        print(f"Target PoolId: {self.target_pool_id}")
        print(f"NFT Contract: {self.nft_address}")
        print(f"Token Owner Checker: {self.token_owner_checker_address}")
        print(f"Block range size: {self.block_range_size}")
        print(f"Owner check batch size: {self.owner_check_batch_size}")
        print(f"Save file: {self.save_file}")

    # ---------------- persistence ----------------

    def save_data(self):
        try:
            data = {
                "metadata": {
                    "last_updated": datetime.now().isoformat(),
                    "current_block": self.current_block,
                    "start_block": self.start_block,
                    "target_pool_key": asdict(self.target_pool_key),
                    "target_pool_id": self.target_pool_id,
                    "total_positions": len(self.positions),
                },
                "valid_positions": [
                    {
                        "token_id": pos.token_id,
                        "pool_key": asdict(pos.pool_key),
                        "owner": pos.owner,
                        "block_number": pos.block_number,
                        "tx_hash": pos.tx_hash,
                        "timestamp": pos.timestamp
                    } for pos in self.positions.values()
                ]
            }

            temp_file = self.save_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            os.rename(temp_file, self.save_file)
            print(f"Data saved to {self.save_file}")

            try:
                if not os.environ.get('GITHUB_ACTIONS'):
                    temp_file_server = self.save_file_server + ".tmp"
                    with open(temp_file_server, 'w') as f:
                        json.dump(data, f, indent=2)
                    os.rename(temp_file_server, self.save_file_server)
                    print(f"Data saved to {self.save_file_server}")
            except Exception as server_e:
                print(f"Could not save to server file: {server_e}")

        except Exception as e:
            print(f"Error saving data: {e}")

    def load_data(self):
        possible_paths = [f"mainnetB0x/{self.save_file}", self.save_file]

        file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                file_path = path
                print(f"Found existing file at: {path}")
                break

        if not file_path:
            print("No existing save file found")
            return

        try:
            with open(file_path, 'r') as f:
                content = f.read().strip()
                if not content:
                    print(f"File {file_path} is empty, starting fresh")
                    return
                data = json.loads(content)

            metadata = data.get("metadata", {})
            self.current_block = metadata.get("current_block", self.start_block)

            for pos_data in data.get("valid_positions", []):
                pool_key = PoolKey(**pos_data["pool_key"])
                position = Position(
                    token_id=pos_data["token_id"],
                    pool_key=pool_key,
                    owner=pos_data["owner"],
                    block_number=pos_data["block_number"],
                    tx_hash=pos_data["tx_hash"],
                    tick_lower=pos_data.get("tick_lower", 0),
                    tick_upper=pos_data.get("tick_upper", 0),
                    timestamp=pos_data["timestamp"]
                )
                self.positions[position.token_id] = position

            print(f"Loaded data from {file_path}")
            print(f"  Resuming from block: {self.current_block}")
            print(f"  valid_positions: {len(self.positions)}")

        except Exception as e:
            print(f"Error loading data: {e}")
            print("Starting fresh...")

    # ---------------- RPC helpers ----------------

    def exponential_backoff_delay(self, attempt: int) -> float:
        delay = min(self.base_retry_delay * (2 ** attempt), self.max_retry_delay)
        jitter = delay * 0.1 * random.random()
        return delay + jitter

    def retry_with_backoff(self, func, *args, **kwargs):
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt == self.max_retries - 1:
                    break
                delay = self.exponential_backoff_delay(attempt)
                print(f"Attempt {attempt + 1} failed: {str(e)[:100]}...")
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
        print(f"All {self.max_retries} attempts failed. Last error: {last_exception}")
        raise last_exception

    def _get_logs_internal(self, from_block: int, to_block: int, topics: List, address: str) -> List[Dict]:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": address,
                "topics": topics
            }],
            "id": 1
        }
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            error_msg = result['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise Exception(f"RPC Error: {error_msg}")
        return result.get("result", [])

    def get_logs(self, from_block: int, to_block: int, topics: List, address: str) -> List[Dict]:
        try:
            return self.retry_with_backoff(self._get_logs_internal, from_block, to_block, topics, address)
        except Exception as e:
            print(f"Failed to get logs after {self.max_retries} attempts: {e}")
            return []

    def _get_latest_block_internal(self) -> int:
        payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            error_msg = result['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise Exception(f"RPC Error: {error_msg}")
        return int(result.get("result", "0x0"), 16)

    def get_latest_block(self) -> int:
        try:
            return self.retry_with_backoff(self._get_latest_block_internal)
        except Exception as e:
            print(f"Failed to get latest block after {self.max_retries} attempts: {e}")
            return self.current_block

    # ---------------- ModifyLiquidity log processing ----------------

    def process_modify_liquidity_logs(self, logs: List[Dict]) -> List[Dict]:
        """
        Decode ModifyLiquidity logs into position records.
        topics[0] = event signature, topics[1] = poolId, topics[2] = sender
        data = abi.encode(tickLower int24, tickUpper int24, liquidityDelta int256, salt bytes32)
        salt IS the tokenId directly (per position manager's encoding).
        """
        positions = []
        skipped_zero_token = 0

        for log in logs:
            try:
                pool_id = log["topics"][1]
                data_hex = log["data"]
                data_bytes = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)

                tick_lower, tick_upper, liquidity_delta, salt = abi_decode(
                    ['int24', 'int24', 'int256', 'bytes32'], data_bytes
                )

                token_id = int.from_bytes(salt, 'big')

                if token_id == 0:
                    skipped_zero_token += 1
                    continue

                positions.append({
                    "token_id": token_id,
                    "tx_hash": log.get("transactionHash", ""),
                    "block_number": int(log["blockNumber"], 16) if isinstance(log["blockNumber"], str) else log["blockNumber"],
                    "pool_id": pool_id,
                    "sender": "0x" + log["topics"][2][-40:],
                    "tick_lower": tick_lower,
                    "tick_upper": tick_upper,
                    "liquidity_delta": liquidity_delta,
                })

            except Exception as e:
                print(f"Error processing ModifyLiquidity log at block {log.get('blockNumber')}: {e}")

        if skipped_zero_token > 0:
            print(f"  Skipped {skipped_zero_token} events with tokenId=0 (non-NFT direct pool interactions)")

        return positions

    def calculate_block_range(self, from_block: int, to_block: int) -> List[Tuple[int, int]]:
        ranges = []
        current = from_block
        while current <= to_block:
            end_block = min(current + self.block_range_size - 1, to_block)
            ranges.append((current, end_block))
            current = end_block + 1
        return ranges

    def scan_blocks(self, from_block: int, to_block: int) -> int:
        """Scan a range of blocks for ModifyLiquidity events on our target pool only."""
        print(f"\nScanning blocks {from_block} to {to_block}...")
        block_ranges = self.calculate_block_range(from_block, to_block)

        new_or_updated = 0

        for start, end in block_ranges:
            print(f"  Scanning sub-range: {start} to {end} ({end - start + 1} blocks)")

            modify_logs = self.get_logs(
                from_block=start,
                to_block=end,
                topics=[self.modify_liquidity_topic, self.target_pool_id],
                address=self.pool_manager_address
            )
            print(f"    Found {len(modify_logs)} ModifyLiquidity events")
            time.sleep(0.1)

            if not modify_logs:
                time.sleep(self.sleep_between_batches)
                continue

            decoded = self.process_modify_liquidity_logs(modify_logs)
            print(f"    Processed {len(decoded)} positions")

            for item in decoded:
                token_id = item["token_id"]
                is_new = token_id not in self.positions
                position = self.positions.get(token_id)

                if position is None:
                    position = Position(
                        token_id=token_id,
                        pool_key=self.target_pool_key,
                        owner="Unknown",  # populated by refresh_ownership()
                        block_number=item["block_number"],
                        tx_hash=item["tx_hash"],
                        tick_lower=item["tick_lower"],
                        tick_upper=item["tick_upper"],
                        timestamp=datetime.now().isoformat()
                    )
                    self.positions[token_id] = position
                else:
                    # Keep latest tick range / block info for existing positions
                    position.tick_lower = item["tick_lower"]
                    position.tick_upper = item["tick_upper"]
                    position.block_number = item["block_number"]
                    position.tx_hash = item["tx_hash"]

                if is_new:
                    new_or_updated += 1
                    print(f"    New position discovered: token {token_id} (tx {item['tx_hash']})")

            time.sleep(self.sleep_between_batches)

        if new_or_updated:
            print(f"  Block range summary: {new_or_updated} new positions discovered")

        return new_or_updated

    # ---------------- ownership via TOKEN_OWNER_CHECKER ----------------

    def check_owners_batch(self, token_ids: List[int]) -> Dict[int, Optional[str]]:
        """
        Call TOKEN_OWNER_CHECKER for up to owner_check_batch_size (100) token IDs at once.

        ASSUMPTION: function signature is getOwnersOf(address nftContract, uint256[] tokenIds)
        -> address[]. If this reverts or the decode fails, confirm the real signature on
        Basescan and update TOKEN_OWNER_CHECKER_FUNCTION_SIG (and the input types passed to
        abi_encode below) accordingly -- everything else here stays the same.
        """
        results: Dict[int, Optional[str]] = {}
        if not token_ids:
            return results

        try:
            selector = Web3.keccak(text=TOKEN_OWNER_CHECKER_FUNCTION_SIG)[:4]
            encoded_args = abi_encode(['address', 'uint256[]'], [self.nft_address, token_ids])
            calldata = "0x" + selector.hex() + encoded_args.hex()

            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": self.token_owner_checker_address, "data": calldata}, "latest"],
                "id": 1
            }

            response = self.retry_with_backoff(
                lambda: requests.post(self.rpc_url, json=payload, timeout=30)
            )
            response.raise_for_status()
            result = response.json()

            if "error" in result:
                error_msg = result['error']
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get('message', str(error_msg))
                raise Exception(f"RPC Error calling TOKEN_OWNER_CHECKER: {error_msg}")

            raw = result.get("result")
            if not raw or raw == "0x":
                raise Exception("Empty result from TOKEN_OWNER_CHECKER -- likely wrong function signature")

            return_bytes = bytes.fromhex(raw[2:])
            (owners,) = abi_decode(['address[]'], return_bytes)

            if len(owners) != len(token_ids):
                raise Exception(
                    f"TOKEN_OWNER_CHECKER returned {len(owners)} owners for {len(token_ids)} tokenIds"
                )

            for token_id, owner in zip(token_ids, owners):
                if owner.lower() == ZERO_ADDRESS:
                    # getOwnersSafe returns address(0) when it can't resolve an owner
                    # (e.g. burned/nonexistent token) -- treat as unknown, not a real owner.
                    results[token_id] = None
                else:
                    results[token_id] = Web3.to_checksum_address(owner)

        except Exception as e:
            print(f"    TOKEN_OWNER_CHECKER batch call failed: {e}")
            print(f"    -> Confirm the real function signature and update TOKEN_OWNER_CHECKER_FUNCTION_SIG")
            for token_id in token_ids:
                results[token_id] = None

        return results

    def refresh_ownership(self):
        """Look up current owner for every tracked position, 100 tokenIds per call max."""
        if not self.positions:
            return

        token_ids = list(self.positions.keys())
        batch_size = self.owner_check_batch_size
        print(f"\nRefreshing ownership for {len(token_ids)} positions "
              f"(batches of {batch_size} via TOKEN_OWNER_CHECKER)...")

        changed = 0
        failed = 0

        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i + batch_size]
            print(f"  Owner batch {i // batch_size + 1}/{(len(token_ids) + batch_size - 1) // batch_size} "
                  f"({len(batch)} tokens)")

            batch_owners = self.check_owners_batch(batch)

            for token_id, owner in batch_owners.items():
                position = self.positions[token_id]
                if owner is None:
                    failed += 1
                    continue
                if owner.lower() != position.owner.lower():
                    if position.owner != "Unknown":
                        print(f"    Ownership changed for token {token_id}: {position.owner} -> {owner}")
                    position.owner = owner
                    changed += 1

            time.sleep(self.sleep_between_batches)

        print(f"Ownership refresh complete: {changed} updated, {failed} lookups failed, "
              f"{len(token_ids) - changed - failed} unchanged")

    # ---------------- summary / run ----------------

    def print_summary(self):
        print(f"\n{'='*50}")
        print("SUMMARY")
        print(f"{'='*50}")
        print(f"Blocks scanned: {self.start_block} to {self.current_block}")
        print(f"Total positions tracked: {len(self.positions)}")

        if self.positions:
            print("\nPOSITIONS:")
            for pos in self.positions.values():
                print(f"  Token ID: {pos.token_id}, Owner: {pos.owner}, Block: {pos.block_number}")
        else:
            print("\nNo positions found yet")

    def run_once(self, blocks_per_scan: int = 2000, max_total_blocks = 100000):
        latest_block = self.get_latest_block()
        total_blocks_scanned = 0

        if max_total_blocks is None:
            max_total_blocks = max(latest_block - self.current_block + 1, 0)

        print(f"Starting scan. Current block: {self.current_block}, Latest block: {latest_block}")

        while self.current_block <= latest_block and total_blocks_scanned < max_total_blocks:
            remaining_blocks = latest_block - self.current_block + 1
            blocks_to_scan = min(blocks_per_scan, remaining_blocks, max_total_blocks - total_blocks_scanned)
            to_block = self.current_block + blocks_to_scan - 1

            print(f"\nIteration: Current {self.current_block}, Scanning to {to_block} ({blocks_to_scan} blocks)")

            self.scan_blocks(self.current_block, to_block)
            self.current_block = to_block + 1
            total_blocks_scanned += blocks_to_scan

            self.save_data()

            if self.current_block > latest_block:
                print(f"\nCaught up to block {latest_block}")
                break

        if total_blocks_scanned >= max_total_blocks and self.current_block <= latest_block:
            print(f"\nReached max scan limit of {max_total_blocks} blocks")
            print(f"Still {latest_block - self.current_block + 1} blocks behind")

        self.refresh_ownership()
        self.save_data()
        self.print_summary()
        return latest_block


def main():
    RPC_URL = CONFIG["RPC_URL"]
    START_BLOCK = 35956776
    SAVE_FILE = "mainnet_uniswap_v4_data.json"

    print(f"Connecting to RPC: {RPC_URL[:50]}...")

    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        latest_block = web3.eth.get_block('latest')
        print(f"Successfully connected to RPC. Latest block: {latest_block['number']}")
    except Exception as e:
        print(f"Failed to connect to RPC: {e}")
        exit(1)

    monitor = UniswapV4Monitor(RPC_URL, START_BLOCK, SAVE_FILE)

    print("Running single scan for GitHub Actions...")
    try:
        monitor.run_once(blocks_per_scan=2000)
        print("GitHub Actions scan completed successfully!")
    except Exception as e:
        print(f"Error during GitHub Actions scan: {e}")
        monitor.save_data()
        exit(1)


if __name__ == "__main__":
    main()
